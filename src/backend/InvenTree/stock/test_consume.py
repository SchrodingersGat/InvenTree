"""Unit tests for the generic stock consumption workflow (stock.consume)."""

from decimal import Decimal
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from build.models import Build, generate_next_build_reference
from build.status_codes import BuildStatus
from company.models import Company
from generic.models.modelmixins import ConsumedStockPolicy, StockConsumerMixin
from order.models import SalesOrder
from part.models import Part
from stock.consume import ConsumeRequest, consume_stock, release_stock
from stock.events import StockEvents
from stock.models import StockItem, StockLocation
from stock.status_codes import StockHistoryCode, StockStatus
from stock.test_consumption import (
    ConsumptionTestBase,
    is_available,
    last_event,
    recorded,
)


class ConsumerDeclarationTest(ConsumptionTestBase):
    """The core consumer models declare the StockConsumerMixin correctly."""

    def test_consumers(self):
        """Each consumer declares its delete policy, audit event and customer."""
        expected = {
            Build: (ConsumedStockPolicy.KEEP, 'BUILD_CONSUMED', 'buildorder'),
            SalesOrder: (
                ConsumedStockPolicy.KEEP,
                'SHIPPED_AGAINST_SALES_ORDER',
                'salesorder',
            ),
            StockItem: (
                ConsumedStockPolicy.CASCADE,
                'INSTALLED_INTO_ASSEMBLY',
                'stockitem',
            ),
            Company: (ConsumedStockPolicy.KEEP, 'SENT_TO_CUSTOMER', 'customer'),
        }

        for model, (policy, event, key) in expected.items():
            self.assertTrue(issubclass(model, StockConsumerMixin), model)
            self.assertEqual(model.CONSUMED_STOCK_ON_DELETE, policy, model)
            self.assertTrue(hasattr(StockHistoryCode, event), model)
            self.assertEqual(model.STOCK_CONSUMED_EVENT, event, model)
            self.assertEqual(model.STOCK_CONSUMED_DELTA_KEY, key, model)

        self.assertIsNone(self.build.get_consuming_customer())
        self.assertEqual(self.order.get_consuming_customer(), self.customer)
        self.assertEqual(self.customer.get_consuming_customer(), self.customer)
        self.assertIsNone(self.stock(1).get_consuming_customer())

    def test_non_consumer(self):
        """A model without the mixin is not a consumer."""
        self.assertFalse(issubclass(Part, StockConsumerMixin))
        self.assertFalse(issubclass(StockLocation, StockConsumerMixin))


class ConsumeStockTest(ConsumptionTestBase):
    """Tests for consume_stock()."""

    def consumers(self):
        """Return one instance of each consumer type."""
        return [
            self.build,
            self.order,
            self.customer,
            self.stock(1, part=self.assembly, serial='PARENT'),
        ]

    def assertConsumed(self, item, consumer, quantity):
        """Check that an item is consumed by the given consumer."""
        item.refresh_from_db()

        self.assertEqual(item.consumed_by, consumer)
        self.assertEqual(
            item.consumed_by_model_type, ContentType.objects.get_for_model(consumer)
        )
        self.assertEqual(item.consumed_by_model_id, consumer.pk)
        self.assertTrue(item.is_consumed)
        self.assertEqual(item.quantity, quantity)
        self.assertIsNone(item.location)
        self.assertFalse(is_available(item))

        self.assertIn(item, consumer.get_consumed_stock())

    def test_partial_each_consumer(self):
        """A partial quantity is split off and consumed, for each consumer type."""
        for consumer in self.consumers():
            with self.subTest(consumer=type(consumer).__name__):
                source = self.stock(10)

                [result] = consume_stock(
                    [ConsumeRequest(source, Decimal(4), consumer, ref='r1')],
                    user=self.user,
                    notes='Consume test',
                )

                self.assertEqual(result.ref, 'r1')
                self.assertEqual(result.quantity, 4)
                self.assertEqual(result.source.pk, source.pk)
                self.assertNotEqual(result.item.pk, source.pk)
                self.assertEqual(result.consumer, consumer)

                self.assertConsumed(result.item, consumer, 4)
                self.assertEqual(result.item.parent, source)

                source.refresh_from_db()
                self.assertEqual(source.quantity, 6)
                self.assertEqual(source.location, self.location)
                self.assertFalse(source.is_consumed)
                self.assertTrue(is_available(source))

                # Audit: the consumer is recorded as a delta, with the quantity
                code = getattr(StockHistoryCode, consumer.STOCK_CONSUMED_EVENT)
                event = last_event(result.item, code)
                self.assertEqual(
                    recorded(event, consumer.STOCK_CONSUMED_DELTA_KEY), consumer.pk
                )
                self.assertEqual(Decimal(str(recorded(event, 'quantity'))), 4)

                split = last_event(source, StockHistoryCode.SPLIT_CHILD_ITEM)
                self.assertEqual(recorded(split, 'stockitem'), result.item.pk)

    def test_full_each_consumer(self):
        """The whole quantity consumes the item itself (no split)."""
        for consumer in self.consumers():
            with self.subTest(consumer=type(consumer).__name__):
                source = self.stock(5)
                n = StockItem.objects.count()

                [result] = consume_stock(
                    [ConsumeRequest(source, 5, consumer)], user=self.user
                )

                self.assertEqual(StockItem.objects.count(), n)
                self.assertEqual(result.item.pk, source.pk)
                self.assertConsumed(source, consumer, 5)

    def test_install_records_parent_event(self):
        """Consuming into a stock item also records an event against the parent."""
        parent = self.stock(1, part=self.assembly, serial='P-1')
        child = self.stock(1, part=self.tracked, serial='C-1')

        consume_stock([ConsumeRequest(child, 1, parent)], user=self.user)

        self.assertConsumed(child, parent, 1)

        event = last_event(parent, StockHistoryCode.INSTALLED_CHILD_ITEM)
        self.assertEqual(recorded(event, 'stockitem'), child.pk)

        event = last_event(child, StockHistoryCode.INSTALLED_INTO_ASSEMBLY)
        self.assertEqual(recorded(event, 'stockitem'), parent.pk)

    def test_extra_deltas(self):
        """Caller-provided deltas are recorded (e.g. the build for an output install)."""
        output = StockItem.objects.create(
            part=self.assembly, quantity=1, is_building=True, build=self.build
        )
        child = self.stock(3, part=self.tracked)

        [result] = consume_stock(
            [ConsumeRequest(child, 1, output, deltas={'buildorder': self.build})],
            user=self.user,
        )

        event = last_event(result.item, StockHistoryCode.INSTALLED_INTO_ASSEMBLY)
        self.assertEqual(recorded(event, 'buildorder'), self.build.pk)
        self.assertEqual(recorded(event, 'stockitem'), output.pk)

    def test_multiple_requests_one_item(self):
        """Several requests against one stock item draw on the running quantity."""
        source = self.stock(10)

        results = consume_stock(
            [
                ConsumeRequest(source, 3, self.build, ref='a'),
                ConsumeRequest(source, 4, self.order, ref='b'),
                ConsumeRequest(source, 3, self.customer, ref='c'),
                ConsumeRequest(source, 5, self.build, ref='d'),
            ],
            user=self.user,
        )

        # The third request consumes the remainder (the item itself);
        # the fourth has nothing left to consume, so returns no result
        self.assertEqual([r.ref for r in results], ['a', 'b', 'c'])
        self.assertEqual([r.quantity for r in results], [3, 4, 3])

        self.assertConsumed(results[0].item, self.build, 3)
        self.assertConsumed(results[1].item, self.order, 4)
        self.assertEqual(results[2].item.pk, source.pk)
        self.assertConsumed(source, self.customer, 3)

    def test_clamp(self):
        """A request for more than the available quantity is clamped."""
        source = self.stock(5)

        [result] = consume_stock([ConsumeRequest(source, 50, self.build)])

        self.assertEqual(result.quantity, 5)
        self.assertEqual(result.item.pk, source.pk)

    def test_zero_and_missing(self):
        """Zero quantities and deleted stock items are skipped."""
        source = self.stock(5)
        deleted = self.stock(5)
        deleted_ref = StockItem.objects.get(pk=deleted.pk)
        deleted.delete()

        results = consume_stock([
            ConsumeRequest(source, 0, self.build),
            ConsumeRequest(deleted_ref, 5, self.build),
        ])

        self.assertEqual(results, [])

        source.refresh_from_db()
        self.assertFalse(source.is_consumed)
        self.assertEqual(source.quantity, 5)

    def test_already_consumed(self):
        """Stock which is already consumed cannot be consumed again."""
        source = self.stock(5)
        consume_stock([ConsumeRequest(source, 5, self.build)])

        with self.assertRaises(ValidationError):
            consume_stock([ConsumeRequest(source, 5, self.order)])

        self.assertConsumed(source, self.build, 5)

    def test_already_consumed_legacy(self):
        """Stock consumed via the legacy fields cannot be consumed either."""
        source = self.stock(5)
        source.allocateToCustomer(self.customer, user=self.user)

        with self.assertRaises(ValidationError):
            consume_stock([ConsumeRequest(source, 5, self.build)])

    def test_invalid_consumer(self):
        """The consumer must be a saved StockConsumerMixin instance."""
        source = self.stock(5)

        with self.assertRaises(ValidationError):
            consume_stock([ConsumeRequest(source, 1, self.component)])

        with self.assertRaises(ValidationError):
            consume_stock([ConsumeRequest(source, 1, Company(name='Unsaved'))])

        with self.assertRaises(ValidationError):
            consume_stock([ConsumeRequest(source, 1, source)])

        source.refresh_from_db()
        self.assertFalse(source.is_consumed)

    def test_can_consume_hook(self):
        """A consumer can refuse stock via can_consume_stock()."""
        source = self.stock(5)

        with (
            mock.patch.object(Build, 'can_consume_stock', return_value=False),
            self.assertRaises(ValidationError),
        ):
            consume_stock([ConsumeRequest(source, 1, self.build)])

    def test_atomic(self):
        """A failure in any request rolls back the whole operation."""
        good = self.stock(5)
        bad = self.stock(5)
        consume_stock([ConsumeRequest(bad, 5, self.build)])

        with self.assertRaises(ValidationError):
            consume_stock([
                ConsumeRequest(good, 2, self.order),
                ConsumeRequest(bad, 1, self.order),
            ])

        good.refresh_from_db()
        self.assertEqual(good.quantity, 5)
        self.assertFalse(good.is_consumed)
        self.assertFalse(StockItem.objects.filter(parent=good).exists())

    def test_events(self):
        """ITEM_SPLIT and ITEM_CONSUMED events are queued in bulk."""
        full = self.stock(2)
        partial = self.stock(10)

        with mock.patch('stock.consume.bulk_trigger_event') as trigger:
            results = consume_stock([
                ConsumeRequest(full, 2, self.order),
                ConsumeRequest(partial, 3, self.order),
            ])

        calls = {c.args[0]: c.args[1] for c in trigger.call_args_list}

        self.assertEqual(
            calls[StockEvents.ITEM_SPLIT],
            [((), {'id': results[1].item.pk, 'parent': partial.pk})],
        )

        self.assertEqual(
            calls[StockEvents.ITEM_CONSUMED],
            [
                (
                    (),
                    {
                        'id': result.item.pk,
                        'quantity': float(result.quantity),
                        'consumer_model_type': 'order.salesorder',
                        'consumer_id': self.order.pk,
                    },
                )
                for result in results
            ],
        )

    def test_query_count(self):
        """Apart from the per-entry audit setting check, the query count is constant."""

        def count(n):
            part = Part.objects.create(name=f'Bulk {n}', component=True)
            StockItem.objects.bulk_create([
                StockItem(part=part, quantity=1 + idx % 2, location=self.location)
                for idx in range(n)
            ])
            requests = [
                ConsumeRequest(item, 1, self.build)
                for item in StockItem.objects.filter(part=part)
            ]

            with CaptureQueriesContext(connection) as ctx:
                consume_stock(requests, user=self.user)

            self.assertEqual(
                self.build.get_consumed_stock().filter(part=part).count(), n
            )

            # Known per-entry query: the AUDIT_LOG_ENABLED setting (audit.signals)
            return len([
                q for q in ctx.captured_queries if 'AUDIT_LOG_ENABLED' not in q['sql']
            ])

        self.assertEqual(count(20), count(200))


class ReleaseStockTest(ConsumptionTestBase):
    """Tests for release_stock()."""

    def consumed(self, quantity, consumer, **kwargs):
        """Create a stock item, and consume it entirely."""
        item = self.stock(quantity, **kwargs)
        consume_stock([ConsumeRequest(item, quantity, consumer)], user=self.user)
        item.refresh_from_db()
        return item

    def test_release_full(self):
        """Releasing the whole item clears the consumer."""
        for consumer in [self.build, self.order, self.customer]:
            with self.subTest(consumer=type(consumer).__name__):
                item = self.consumed(5, consumer)

                released = release_stock(item, self.returns, user=self.user)

                self.assertEqual(released.pk, item.pk)
                item.refresh_from_db()
                self.assertFalse(item.is_consumed)
                self.assertIsNone(item.consumed_by)
                self.assertEqual(item.location, self.returns)
                self.assertTrue(is_available(item))

                event = last_event(item, StockHistoryCode.RETURNED_TO_STOCK)
                self.assertEqual(
                    recorded(event, consumer.STOCK_CONSUMED_DELTA_KEY), consumer.pk
                )

    def test_release_partial(self):
        """Releasing part of an item splits it; the remainder stays consumed."""
        item = self.consumed(10, self.build)

        released = release_stock(item, self.returns, user=self.user, quantity=4)

        self.assertNotEqual(released.pk, item.pk)
        self.assertEqual(released.quantity, 4)
        self.assertFalse(released.is_consumed)
        self.assertEqual(released.location, self.returns)
        self.assertTrue(is_available(released))

        item.refresh_from_db()
        self.assertEqual(item.quantity, 6)
        self.assertEqual(item.consumed_by, self.build)

    def test_release_installed(self):
        """Releasing an installed item records removal against both items."""
        parent = self.stock(1, part=self.assembly, serial='P-2')
        child = self.consumed(1, parent, part=self.tracked, serial='C-2')

        release_stock(child, self.returns, user=self.user, notes='Removed')

        child.refresh_from_db()
        self.assertFalse(child.is_consumed)
        self.assertNotIn(child, parent.get_consumed_stock())

        event = last_event(child, StockHistoryCode.REMOVED_FROM_ASSEMBLY)
        self.assertEqual(recorded(event, 'stockitem'), parent.pk)

        event = last_event(parent, StockHistoryCode.REMOVED_CHILD_ITEM)
        self.assertEqual(recorded(event, 'stockitem'), child.pk)

    def test_release_status_and_code(self):
        """A status and an explicit audit code can be provided."""
        item = self.consumed(5, self.order)

        release_stock(
            item,
            self.returns,
            user=self.user,
            status=StockStatus.QUARANTINED.value,
            code=StockHistoryCode.RETURNED_AGAINST_RETURN_ORDER,
        )

        item.refresh_from_db()
        self.assertEqual(item.status, StockStatus.QUARANTINED.value)
        last_event(item, StockHistoryCode.RETURNED_AGAINST_RETURN_ORDER)

    def test_release_merge(self):
        """Released stock can be merged back into its (available) parent item."""
        source = self.stock(10)
        [result] = consume_stock([ConsumeRequest(source, 4, self.build)])

        merged = release_stock(result.item, self.location, merge=True)

        self.assertEqual(merged.pk, source.pk)
        source.refresh_from_db()
        self.assertEqual(source.quantity, 10)
        self.assertFalse(StockItem.objects.filter(pk=result.item.pk).exists())

    def test_release_invalid(self):
        """Invalid release requests are rejected."""
        with self.assertRaises(ValidationError):
            release_stock(self.stock(5), self.returns)

        item = self.consumed(5, self.build)
        structural = StockLocation.objects.create(name='Structural', structural=True)

        for kwargs in [
            {'location': structural},
            {'location': self.returns, 'quantity': 0},
            {'location': self.returns, 'quantity': 6},
        ]:
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                with self.assertRaises(ValidationError):
                    release_stock(item, **kwargs)

        item.refresh_from_db()
        self.assertTrue(item.is_consumed)


class ConsumedStockDeletePolicyTest(ConsumptionTestBase):
    """Deleting a consumer applies its CONSUMED_STOCK_ON_DELETE policy."""

    def consume(self, consumer, quantity=5):
        """Consume a new stock item into the consumer."""
        item = self.stock(quantity)
        consume_stock([ConsumeRequest(item, quantity, consumer)])
        return item

    def assertKept(self, item, model, pk):
        """Assert that the item is still consumed by a (deleted) consumer."""
        item = StockItem.objects.get(pk=item.pk)

        self.assertTrue(item.is_consumed)
        self.assertIsNone(item.consumed_by)
        self.assertEqual(item.consumed_by_model_type.model_class(), model)
        self.assertEqual(item.consumed_by_model_id, pk)
        self.assertFalse(item.is_in_stock())
        self.assertFalse(
            StockItem.objects.filter(StockItem.IN_STOCK_FILTER, pk=item.pk).exists()
        )

        # The dangling reference is still valid
        item.full_clean()
        item.save()

        return item

    def test_keep_sales_order(self):
        """Deleting a SalesOrder keeps its consumed stock (still consumed)."""
        order = SalesOrder.objects.create(customer=self.customer, reference='SO-9100')
        item = self.consume(order)
        pk = order.pk

        order.delete()

        self.assertFalse(SalesOrder.objects.filter(pk=pk).exists())
        self.assertKept(item, SalesOrder, pk)

    def test_keep_queryset_delete(self):
        """QuerySet.delete() also keeps consumed stock."""
        company = Company.objects.create(name='Customer C', is_customer=True)
        item = self.consume(company)
        pk = company.pk

        Company.objects.filter(pk=pk).delete()

        self.assertFalse(Company.objects.filter(pk=pk).exists())
        self.assertKept(item, Company, pk)

    def test_keep_build(self):
        """Deleting a Build keeps its consumed stock."""
        build = Build.objects.create(
            reference=generate_next_build_reference(),
            part=self.assembly,
            quantity=1,
            status=BuildStatus.PENDING,
        )
        item = self.consume(build)
        pk = build.pk

        build.delete()

        self.assertKept(item, Build, pk)

    def test_release_deleted_consumer(self):
        """Stock consumed by a deleted consumer can still be released."""
        company = Company.objects.create(name='Customer D', is_customer=True)
        item = self.consume(company, quantity=10)
        pk = company.pk
        company.delete()

        item = StockItem.objects.get(pk=item.pk)
        returned = release_stock(item, self.returns, quantity=4)

        self.assertNotEqual(returned.pk, item.pk)
        self.assertFalse(returned.is_consumed)
        self.assertTrue(returned.is_in_stock())
        self.assertEqual(returned.location, self.returns)

        # The remainder is still consumed by the deleted company
        self.assertKept(item, Company, pk)

        # The deleted consumer is recorded by its primary key
        entry = last_event(returned, StockHistoryCode.RETURNED_TO_STOCK)
        self.assertEqual(recorded(entry, 'customer'), pk)

    def test_cascade_stock_item(self):
        """Deleting a stock item deletes the items installed in it (recursively)."""
        parent = self.stock(1, part=self.assembly, serial='P-3')
        child = self.stock(1, part=self.assembly, serial='C-3')
        grandchild = self.stock(1, part=self.tracked, serial='G-3')

        consume_stock([ConsumeRequest(grandchild, 1, child)])
        consume_stock([ConsumeRequest(child, 1, parent)])

        parent.delete()

        self.assertFalse(
            StockItem.objects.filter(pk__in=[child.pk, grandchild.pk]).exists()
        )


class ConsumerValidationTest(ConsumptionTestBase):
    """StockItem.clean() validates the generic consumer reference."""

    def test_type_and_id_together(self):
        """Consumer type and ID must be set together."""
        item = self.stock(5)
        item.consumed_by_model_id = self.build.pk

        with self.assertRaises(ValidationError):
            item.save()

    def test_invalid_type(self):
        """The consumer type must implement StockConsumerMixin."""
        item = self.stock(5)
        item.consumed_by_model_type = ContentType.objects.get_for_model(Part)
        item.consumed_by_model_id = self.component.pk

        with self.assertRaises(ValidationError):
            item.save()

    def test_self(self):
        """An item cannot consume itself."""
        item = self.stock(5)
        item.consumed_by_model_type = ContentType.objects.get_for_model(StockItem)
        item.consumed_by_model_id = item.pk

        with self.assertRaises(ValidationError):
            item.save()
