"""Characterisation tests for stock "consumption".

Stock leaves inventory through several code paths (build orders, sales orders,
installation into other stock items, manual assignment to a customer), and is
brought back through others (return to stock, uninstall, return orders).

These tests pin down the *observable* behaviour of each path, ahead of the
refactor which replaces the separate StockItem fields (consumed_by, sales_order,
belongs_to, customer) with a single generic 'consumed_by' reference.

Every assertion about "where did this stock go" is made via consumer_of(),
which is the single seam that changes when the underlying fields change.

Tests in DeleteConsumerTest describe the *current* delete behaviour,
some of which is intentionally changed by the refactor (noted per test).
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from build.models import Build, BuildItem, BuildLine, generate_next_build_reference
from build.status_codes import BuildStatus
from company.models import Company
from InvenTree.unit_test import InvenTreeTestCase, stock_events
from order.models import (
    ReturnOrder,
    ReturnOrderLineItem,
    SalesOrder,
    SalesOrderAllocation,
    SalesOrderLineItem,
    SalesOrderShipment,
)
from part.models import BomItem, Part
from stock.models import StockItem, StockLocation
from stock.status_codes import StockHistoryCode, StockStatus


def consumer_of(item: StockItem):
    """Return the object which has consumed the provided stock item (or None).

    This is the single point of contact with the underlying "consumption" fields.
    Precedence matches the planned data migration:
    belongs_to > sales_order > consumed_by > customer
    """
    item.refresh_from_db()

    if item.belongs_to_id:
        return item.belongs_to

    if item.sales_order_id:
        return item.sales_order

    if item.consumed_by_id:
        return item.consumed_by

    if item.customer_id:
        return item.customer

    return None


def is_available(item: StockItem) -> bool:
    """Return True if the item is "in stock", checked via both the model and the DB filter."""
    item.refresh_from_db()
    in_db = StockItem.objects.filter(StockItem.IN_STOCK_FILTER, pk=item.pk).exists()
    assert in_db == item.is_in_stock(), 'IN_STOCK_FILTER and is_in_stock() disagree'
    return in_db


def last_event(item: StockItem, code: StockHistoryCode):
    """Return the most recent audit event of the given type for a stock item."""
    entry = stock_events(item, event_type=code).last()
    assert entry is not None, f'No {code.name} event for StockItem <{item.pk}>'
    return entry


def deltas(entry) -> dict:
    """Return the deltas recorded against an audit log entry."""
    return (entry.additional_data or {}).get('deltas', {})


def recorded(entry, key, previous: bool = False):
    """Return the value an audit entry records for 'key' (a pk, for model references).

    The value may be recorded as a named-event delta (model references are stored
    as {'pk', 'str', 'model_type'}), or - where the delta duplicates a changed
    model field - as that field change. For a field change, the new value is
    returned, or the old value if 'previous' is set (e.g. the customer which
    stock was returned from).
    """
    values = deltas(entry)

    if key in values:
        value = values[key]
        return value['pk'] if isinstance(value, dict) else value

    changes = entry.changes or {}

    if key in changes:
        value = changes[key][0 if previous else 1]
        return int(value) if isinstance(value, str) and value.isdigit() else value

    raise KeyError(f'{key} not recorded in audit entry <{entry.pk}>')


class ConsumptionTestBase(InvenTreeTestCase):
    """Common data for consumption tests."""

    fixtures = ['users']

    @classmethod
    def setUpTestData(cls):
        """Create parts, locations, a build order and a sales order."""
        super().setUpTestData()

        cls.location = StockLocation.objects.create(name='Shelf')
        cls.returns = StockLocation.objects.create(name='Returns')

        cls.customer = Company.objects.create(name='Customer A', is_customer=True)

        # Assembly with one untracked and one trackable component
        cls.assembly = Part.objects.create(
            name='Assembly', assembly=True, trackable=True, salable=True
        )
        cls.component = Part.objects.create(
            name='Component', component=True, salable=True
        )
        cls.tracked = Part.objects.create(
            name='Tracked component', component=True, trackable=True
        )

        cls.bom_component = BomItem.objects.create(
            part=cls.assembly, sub_part=cls.component, quantity=10
        )
        cls.bom_tracked = BomItem.objects.create(
            part=cls.assembly, sub_part=cls.tracked, quantity=1
        )

        cls.build = Build.objects.create(
            reference=generate_next_build_reference(),
            title='Consumption build',
            part=cls.assembly,
            quantity=10,
            status=BuildStatus.PENDING,
        )

        cls.line_component = BuildLine.objects.get(
            build=cls.build, bom_item=cls.bom_component
        )
        cls.line_tracked = BuildLine.objects.get(
            build=cls.build, bom_item=cls.bom_tracked
        )

        cls.order = SalesOrder.objects.create(
            customer=cls.customer, reference='SO-9001'
        )
        cls.shipment = SalesOrderShipment.objects.create(
            order=cls.order, reference='SHIP-1'
        )
        cls.so_line = SalesOrderLineItem.objects.create(
            order=cls.order, part=cls.component, quantity=100
        )

    def stock(self, quantity, part=None, **kwargs) -> StockItem:
        """Create a stock item in the default location."""
        return StockItem.objects.create(
            part=part or self.component,
            quantity=quantity,
            location=self.location,
            **kwargs,
        )


class BuildConsumptionTest(ConsumptionTestBase):
    """Stock consumed by a build order (Build.complete_allocations)."""

    def setUp(self):
        """Issue the build so that stock can be consumed."""
        super().setUp()
        self.build.issue_build()

    def consume(self, build_item: BuildItem):
        """Complete a single build allocation."""
        self.build.complete_allocations(
            BuildItem.objects.filter(pk=build_item.pk), user=self.user
        )

    def test_partial(self):
        """Consuming part of a stock item splits off the consumed quantity."""
        source = self.stock(100)
        alloc = BuildItem.objects.create(
            build_line=self.line_component, stock_item=source, quantity=30
        )

        self.consume(alloc)

        source.refresh_from_db()
        self.assertEqual(source.quantity, 70)
        self.assertIsNone(consumer_of(source))
        self.assertTrue(is_available(source))
        self.assertEqual(source.location, self.location)

        consumed = StockItem.objects.get(parent=source)
        self.assertEqual(consumed.quantity, 30)
        self.assertEqual(consumer_of(consumed), self.build)
        self.assertIsNone(consumed.location)
        self.assertIsNone(consumed.customer)
        self.assertFalse(is_available(consumed))

        self.line_component.refresh_from_db()
        self.assertEqual(self.line_component.consumed, 30)
        self.assertFalse(BuildItem.objects.filter(pk=alloc.pk).exists())

        split = last_event(source, StockHistoryCode.SPLIT_CHILD_ITEM)
        self.assertEqual(Decimal(str(deltas(split)['removed'])), 30)

        event = last_event(consumed, StockHistoryCode.BUILD_CONSUMED)
        self.assertEqual(recorded(event, 'buildorder'), self.build.pk)
        self.assertEqual(Decimal(str(recorded(event, 'quantity'))), 30)

    def test_full(self):
        """Consuming the whole of a stock item marks that item as consumed (no split)."""
        source = self.stock(25)
        alloc = BuildItem.objects.create(
            build_line=self.line_component, stock_item=source, quantity=25
        )

        n = StockItem.objects.count()
        self.consume(alloc)
        self.assertEqual(StockItem.objects.count(), n)

        self.assertEqual(consumer_of(source), self.build)
        self.assertEqual(source.quantity, 25)
        self.assertIsNone(source.location)
        self.assertFalse(is_available(source))

        last_event(source, StockHistoryCode.BUILD_CONSUMED)

    def test_multiple_allocations_one_item(self):
        """Several allocations drawing on one stock item each split off their quantity."""
        source = self.stock(100)
        a1 = BuildItem.objects.create(
            build_line=self.line_component, stock_item=source, quantity=10
        )
        output = StockItem.objects.create(
            part=self.assembly, quantity=1, is_building=True, build=self.build
        )
        a2 = BuildItem.objects.create(
            build_line=self.line_component,
            stock_item=source,
            quantity=15,
            install_into=output,
        )

        self.build.complete_allocations(
            BuildItem.objects.filter(pk__in=[a1.pk, a2.pk]), user=self.user
        )

        source.refresh_from_db()
        self.assertEqual(source.quantity, 75)
        self.assertTrue(is_available(source))

        children = StockItem.objects.filter(parent=source).order_by('quantity')
        self.assertEqual([c.quantity for c in children], [10, 15])

        # Untracked parts are consumed by the build, even with an install_into output
        for child in children:
            self.assertEqual(consumer_of(child), self.build)

        self.line_component.refresh_from_db()
        self.assertEqual(self.line_component.consumed, 25)

    def test_trackable_install(self):
        """Trackable stock allocated against an output is installed into that output."""
        output = StockItem.objects.create(
            part=self.assembly, quantity=1, is_building=True, build=self.build
        )
        source = self.stock(10, part=self.tracked)
        alloc = BuildItem.objects.create(
            build_line=self.line_tracked,
            stock_item=source,
            quantity=1,
            install_into=output,
        )

        self.consume(alloc)

        installed = StockItem.objects.get(parent=source)
        self.assertEqual(installed.quantity, 1)

        # The installed item is consumed by the output, which links back to the build
        self.assertEqual(consumer_of(installed), output)
        self.assertEqual(consumer_of(installed).build, self.build)
        self.assertIsNone(installed.location)
        self.assertFalse(is_available(installed))
        self.assertIn(installed, output.get_installed_items())

        source.refresh_from_db()
        self.assertEqual(source.quantity, 9)
        self.assertTrue(is_available(source))

        event = last_event(installed, StockHistoryCode.INSTALLED_INTO_ASSEMBLY)
        self.assertEqual(recorded(event, 'stockitem'), output.pk)
        self.assertEqual(recorded(event, 'buildorder'), self.build.pk)

        parent_event = last_event(output, StockHistoryCode.INSTALLED_CHILD_ITEM)
        self.assertEqual(recorded(parent_event, 'stockitem'), installed.pk)

    def test_serialized_install(self):
        """A serialized item is installed whole (no split)."""
        output = StockItem.objects.create(
            part=self.assembly, quantity=1, is_building=True, build=self.build
        )
        source = self.stock(1, part=self.tracked, serial='1001')
        alloc = BuildItem.objects.create(
            build_line=self.line_tracked,
            stock_item=source,
            quantity=1,
            install_into=output,
        )

        self.consume(alloc)

        self.assertFalse(StockItem.objects.filter(parent=source).exists())
        self.assertEqual(consumer_of(source), output)
        self.assertEqual(source.serial, '1001')
        self.assertFalse(is_available(source))

    def test_return_to_stock(self):
        """Consumed stock can be returned to stock, clearing the consumer."""
        source = self.stock(10)
        alloc = BuildItem.objects.create(
            build_line=self.line_component, stock_item=source, quantity=10
        )
        self.consume(alloc)
        self.assertEqual(consumer_of(source), self.build)

        source.return_to_stock(self.returns, user=self.user)

        self.assertIsNone(consumer_of(source))
        self.assertEqual(source.location, self.returns)
        self.assertTrue(is_available(source))

        event = last_event(source, StockHistoryCode.RETURNED_TO_STOCK)
        self.assertEqual(recorded(event, 'build_order'), self.build.pk)


class SalesOrderConsumptionTest(ConsumptionTestBase):
    """Stock shipped against a sales order (SalesOrderShipment.complete_allocations)."""

    def ship(self, *allocations, shipment=None):
        """Complete the provided allocations."""
        shipment = shipment or self.shipment
        shipment.complete_allocations(
            SalesOrderAllocation.objects.filter(pk__in=[a.pk for a in allocations]),
            user=self.user,
        )

    def allocate(self, item, quantity, line=None):
        """Allocate stock against the sales order line."""
        return SalesOrderAllocation.objects.create(
            line=line or self.so_line,
            shipment=self.shipment,
            item=item,
            quantity=quantity,
        )

    def test_partial(self):
        """Shipping part of a stock item splits off the shipped quantity."""
        source = self.stock(100)
        alloc = self.allocate(source, 30)

        self.ship(alloc)

        source.refresh_from_db()
        self.assertEqual(source.quantity, 70)
        self.assertIsNone(consumer_of(source))
        self.assertTrue(is_available(source))

        shipped = StockItem.objects.get(parent=source)
        self.assertEqual(shipped.quantity, 30)
        self.assertEqual(consumer_of(shipped), self.order)
        self.assertEqual(shipped.customer, self.customer)
        self.assertIsNone(shipped.location)
        self.assertFalse(is_available(shipped))

        # The allocation is repointed at the shipped item
        alloc.refresh_from_db()
        self.assertEqual(alloc.item, shipped)

        self.so_line.refresh_from_db()
        self.assertEqual(self.so_line.shipped, 30)

        event = last_event(shipped, StockHistoryCode.SHIPPED_AGAINST_SALES_ORDER)
        self.assertEqual(recorded(event, 'salesorder'), self.order.pk)
        self.assertEqual(recorded(event, 'customer'), self.customer.pk)

    def test_full(self):
        """Shipping the whole of a stock item marks that item as shipped (no split)."""
        source = self.stock(20)
        alloc = self.allocate(source, 20)

        n = StockItem.objects.count()
        self.ship(alloc)
        self.assertEqual(StockItem.objects.count(), n)

        self.assertEqual(consumer_of(source), self.order)
        self.assertEqual(source.customer, self.customer)
        self.assertFalse(is_available(source))

    def test_multiple_allocations_one_item(self):
        """Two allocations against one stock item each split off their quantity."""
        line_2 = SalesOrderLineItem.objects.create(
            order=self.order, part=self.component, quantity=50
        )
        source = self.stock(100)
        a1 = self.allocate(source, 10)
        a2 = self.allocate(source, 20, line=line_2)

        self.ship(a1, a2)

        source.refresh_from_db()
        self.assertEqual(source.quantity, 70)
        self.assertTrue(is_available(source))

        children = StockItem.objects.filter(parent=source).order_by('quantity')
        self.assertEqual([c.quantity for c in children], [10, 20])

        for child in children:
            self.assertEqual(consumer_of(child), self.order)
            self.assertEqual(child.customer, self.customer)

        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual({a1.item, a2.item}, set(children))

        self.so_line.refresh_from_db()
        line_2.refresh_from_db()
        self.assertEqual(self.so_line.shipped, 10)
        self.assertEqual(line_2.shipped, 20)

    def test_return_order_receive(self):
        """Shipped stock received against a return order is cleared and quarantined."""
        source = self.stock(10)
        self.ship(self.allocate(source, 10))
        self.assertEqual(consumer_of(source), self.order)

        ro = ReturnOrder.objects.create(customer=self.customer, reference='RMA-9001')
        ro_line = ReturnOrderLineItem.objects.create(order=ro, item=source, quantity=4)

        ro.receive_line_item(ro_line, self.returns, self.user)

        # Partial return: the returned quantity is split off the shipped item
        ro_line.refresh_from_db()
        returned = ro_line.item
        self.assertNotEqual(returned.pk, source.pk)
        self.assertEqual(returned.quantity, 4)
        self.assertIsNone(consumer_of(returned))
        self.assertIsNone(returned.customer)
        self.assertEqual(returned.location, self.returns)
        self.assertEqual(returned.status, StockStatus.QUARANTINED.value)

        # The remainder is still with the customer
        source.refresh_from_db()
        self.assertEqual(source.quantity, 6)
        self.assertEqual(consumer_of(source), self.order)

        event = last_event(returned, StockHistoryCode.RETURNED_AGAINST_RETURN_ORDER)
        self.assertEqual(recorded(event, 'returnorder'), ro.pk)
        self.assertEqual(recorded(event, 'customer', previous=True), self.customer.pk)

    def test_return_to_stock(self):
        """Shipped stock can be returned to stock directly."""
        source = self.stock(10)
        self.ship(self.allocate(source, 10))

        source.return_from_customer(self.returns, user=self.user)

        self.assertIsNone(consumer_of(source))
        self.assertIsNone(source.customer)
        self.assertTrue(is_available(source))

        event = last_event(source, StockHistoryCode.RETURNED_FROM_CUSTOMER)
        self.assertEqual(recorded(event, 'customer', previous=True), self.customer.pk)


class CustomerAssignmentTest(ConsumptionTestBase):
    """Stock assigned to a customer (StockItem.allocateToCustomer)."""

    def test_manual_partial(self):
        """Manual assignment (no order) consumes stock against the customer."""
        source = self.stock(10)

        assigned = source.allocateToCustomer(
            self.customer, quantity=4, user=self.user, notes='Manual'
        )

        self.assertNotEqual(assigned.pk, source.pk)
        self.assertEqual(assigned.quantity, 4)
        self.assertEqual(consumer_of(assigned), self.customer)
        self.assertEqual(assigned.customer, self.customer)
        self.assertIsNone(assigned.location)
        self.assertFalse(is_available(assigned))

        source.refresh_from_db()
        self.assertEqual(source.quantity, 6)
        self.assertTrue(is_available(source))

        event = last_event(assigned, StockHistoryCode.SENT_TO_CUSTOMER)
        self.assertEqual(recorded(event, 'customer'), self.customer.pk)

    def test_manual_full(self):
        """Assigning the whole quantity marks the item itself."""
        source = self.stock(5)

        assigned = source.allocateToCustomer(self.customer, user=self.user)

        self.assertEqual(assigned.pk, source.pk)
        self.assertEqual(consumer_of(source), self.customer)

    def test_with_order(self):
        """Assignment against an order records the order as the consumer."""
        source = self.stock(5)

        assigned = source.allocateToCustomer(
            self.customer, order=self.order, user=self.user
        )

        self.assertEqual(consumer_of(assigned), self.order)
        self.assertEqual(assigned.customer, self.customer)
        last_event(assigned, StockHistoryCode.SHIPPED_AGAINST_SALES_ORDER)

    def test_return_partial(self):
        """Part of an assigned item can be returned, splitting the item."""
        source = self.stock(10)
        source.allocateToCustomer(self.customer, user=self.user)

        source.return_to_stock(self.returns, user=self.user, quantity=3)

        source.refresh_from_db()
        self.assertEqual(source.quantity, 7)
        self.assertEqual(consumer_of(source), self.customer)

        returned = StockItem.objects.get(parent=source)
        self.assertEqual(returned.quantity, 3)
        self.assertIsNone(consumer_of(returned))
        self.assertEqual(returned.location, self.returns)
        self.assertTrue(is_available(returned))


class InstallationTest(ConsumptionTestBase):
    """Stock installed into another stock item (installStockItem / uninstall)."""

    def setUp(self):
        """Create a parent assembly item."""
        super().setUp()
        self.parent = self.stock(1, part=self.assembly, serial='A1')

    def test_install_partial(self):
        """Installing part of a stock item splits off the installed quantity."""
        source = self.stock(10, part=self.tracked)

        self.parent.installStockItem(source, 2, self.user, 'Install')

        source.refresh_from_db()
        self.assertEqual(source.quantity, 8)
        self.assertTrue(is_available(source))

        installed = StockItem.objects.get(parent=source)
        self.assertEqual(installed.quantity, 2)
        self.assertEqual(consumer_of(installed), self.parent)
        self.assertIsNone(installed.location)
        self.assertFalse(is_available(installed))
        self.assertEqual(self.parent.installed_item_count(), 1)

        event = last_event(installed, StockHistoryCode.INSTALLED_INTO_ASSEMBLY)
        self.assertEqual(recorded(event, 'stockitem'), self.parent.pk)

        parent_event = last_event(self.parent, StockHistoryCode.INSTALLED_CHILD_ITEM)
        self.assertEqual(recorded(parent_event, 'stockitem'), installed.pk)

    def test_install_full(self):
        """Installing the whole item installs that item (no split)."""
        source = self.stock(1, part=self.tracked, serial='T1')

        self.parent.installStockItem(source, 1, self.user, 'Install')

        self.assertEqual(consumer_of(source), self.parent)
        self.assertFalse(StockItem.objects.filter(parent=source).exists())

    def test_uninstall(self):
        """Uninstalling clears the consumer and places the item in a location."""
        source = self.stock(1, part=self.tracked, serial='T2')
        self.parent.installStockItem(source, 1, self.user, 'Install')

        source.refresh_from_db()
        source.uninstall_into_location(self.returns, self.user, 'Remove')

        self.assertIsNone(consumer_of(source))
        self.assertEqual(source.location, self.returns)
        self.assertTrue(is_available(source))
        self.assertEqual(self.parent.installed_item_count(), 0)

        event = last_event(source, StockHistoryCode.REMOVED_FROM_ASSEMBLY)
        self.assertEqual(recorded(event, 'stockitem'), self.parent.pk)

        last_event(self.parent, StockHistoryCode.REMOVED_CHILD_ITEM)

    def test_nested_install_customer(self):
        """An item installed in a shipped assembly does not report a customer.

        (Customer is derived from direct consumers only.)
        """
        source = self.stock(1, part=self.tracked, serial='T3')
        self.parent.installStockItem(source, 1, self.user, 'Install')

        self.parent.allocateToCustomer(self.customer, user=self.user)

        self.assertEqual(consumer_of(self.parent), self.customer)
        self.assertEqual(consumer_of(source), self.parent)
        self.assertIsNone(source.customer)

    def test_consumed_item_cannot_merge(self):
        """Consumed (installed) stock cannot be merged."""
        source = self.stock(1, part=self.tracked, serial='T4')
        self.parent.installStockItem(source, 1, self.user, 'Install')
        source.refresh_from_db()

        other = self.stock(1, part=self.tracked, serial='T5')
        self.assertFalse(source.can_merge(other))


class DeleteConsumerTest(ConsumptionTestBase):
    """Behaviour of consumed stock when its consumer is deleted.

    These describe the CURRENT behaviour. The refactor deliberately changes
    SalesOrder and Company deletion to PROTECT - update those tests then.
    """

    def test_delete_build_cascades(self):
        """Deleting a build deletes the stock it consumed (CASCADE)."""
        build = Build.objects.create(
            reference=generate_next_build_reference(),
            part=self.assembly,
            quantity=1,
            status=BuildStatus.PENDING,
        )
        build.issue_build()

        source = self.stock(10)
        line = BuildLine.objects.get(build=build, bom_item=self.bom_component)
        alloc = BuildItem.objects.create(
            build_line=line, stock_item=source, quantity=10
        )
        build.complete_allocations(BuildItem.objects.filter(pk=alloc.pk))
        self.assertEqual(consumer_of(source), build)

        build.delete()

        self.assertFalse(StockItem.objects.filter(pk=source.pk).exists())

    def test_delete_parent_cascades(self):
        """Deleting a stock item deletes the items installed in it (CASCADE)."""
        parent = self.stock(1, part=self.assembly, serial='P1')
        child = self.stock(1, part=self.tracked, serial='C1')
        parent.installStockItem(child, 1, self.user, 'Install')

        parent.delete()

        self.assertFalse(StockItem.objects.filter(pk=child.pk).exists())

    def test_delete_sales_order_set_null(self):
        """Deleting a sales order clears the reference, but the customer remains.

        Planned change: SalesOrder becomes PROTECT.
        """
        order = SalesOrder.objects.create(customer=self.customer, reference='SO-9002')
        source = self.stock(5)
        source.allocateToCustomer(self.customer, order=order, user=self.user)

        order.delete()

        source.refresh_from_db()
        self.assertIsNone(source.sales_order)
        self.assertEqual(source.customer, self.customer)
        self.assertFalse(is_available(source))

    def test_delete_company_set_null(self):
        """Deleting a customer clears the reference, returning stock to availability.

        Planned change: Company becomes PROTECT.
        """
        company = Company.objects.create(name='Customer B', is_customer=True)
        source = self.stock(5)
        source.allocateToCustomer(company, user=self.user)
        self.assertFalse(is_available(source))

        company.delete()

        self.assertIsNone(consumer_of(source))
        self.assertTrue(is_available(source))


class ConsumptionQueryCountTest(ConsumptionTestBase):
    """Query-count baseline for the batched consume paths.

    The ceilings are the measured pre-refactor values, keyed by the number of
    stock items consumed (half split, half consumed whole). The refactor must
    not exceed them.

    The pre-refactor counts are NOT constant - known per-item queries:
    - The AUDIT_LOG_ENABLED setting is read once per audit event
      (audit.signals, ~1.5 x N; not cached outside a request without a global cache)
    - Shipments lazily load the StockLocation of each split source item
      (SalesOrderShipment.complete_allocations does not select_related 'item__location')

    Once these are removed, replace the ceilings with a constant-count assertion.
    """

    # Measured baseline (pre-refactor, 2026-11-24): {N: queries}
    BUILD_QUERY_CEILING = {50: 97, 500: 776}
    SHIPMENT_QUERY_CEILING = {50: 131, 500: 1031}

    def make_stock(self, n) -> list[StockItem]:
        """Create n stock items; every 2nd one is consumed whole, the others split."""
        part = Part.objects.create(name=f'Bulk part {n}', component=True, salable=True)
        StockItem.objects.bulk_create([
            StockItem(part=part, quantity=1 + idx % 2, location=self.location)
            for idx in range(n)
        ])
        return list(StockItem.objects.filter(part=part).order_by('pk'))

    def build_queries(self, n) -> int:
        """Return the number of queries to consume n build allocations."""
        items = self.make_stock(n)
        part = items[0].part
        BomItem.objects.create(part=self.assembly, sub_part=part, quantity=1)

        build = Build.objects.create(
            reference=generate_next_build_reference(),
            part=self.assembly,
            quantity=n,
            status=BuildStatus.PENDING,
        )
        build.issue_build()
        line = BuildLine.objects.get(build=build, bom_item__sub_part=part)

        BuildItem.objects.bulk_create([
            BuildItem(build_line=line, stock_item=item, quantity=1) for item in items
        ])
        allocations = BuildItem.objects.filter(build_line=line)

        with CaptureQueriesContext(connection) as ctx:
            build.complete_allocations(allocations, user=self.user)

        line.refresh_from_db()
        self.assertEqual(line.consumed, n)
        self.assertEqual(
            StockItem.objects.filter(consumed_by=build, part=part).count(), n
        )

        return len(ctx.captured_queries)

    def shipment_queries(self, n) -> int:
        """Return the number of queries to ship n sales order allocations."""
        items = self.make_stock(n)
        part = items[0].part

        order = SalesOrder.objects.create(customer=self.customer, reference=f'SO-{n}')
        shipment = SalesOrderShipment.objects.create(order=order, reference=f'S{n}')
        line = SalesOrderLineItem.objects.create(order=order, part=part, quantity=n)

        SalesOrderAllocation.objects.bulk_create([
            SalesOrderAllocation(line=line, shipment=shipment, item=item, quantity=1)
            for item in items
        ])

        with CaptureQueriesContext(connection) as ctx:
            shipment.complete_allocations(
                SalesOrderAllocation.objects.filter(shipment=shipment), user=self.user
            )

        line.refresh_from_db()
        self.assertEqual(line.shipped, n)
        self.assertEqual(
            StockItem.objects.filter(sales_order=order, part=part).count(), n
        )

        return len(ctx.captured_queries)

    def test_build_query_count(self):
        """Build consumption does not exceed the pre-refactor query count."""
        for n, ceiling in self.BUILD_QUERY_CEILING.items():
            self.assertLessEqual(self.build_queries(n), ceiling, f'N={n}')

    def test_shipment_query_count(self):
        """Shipment completion does not exceed the pre-refactor query count."""
        for n, ceiling in self.SHIPMENT_QUERY_CEILING.items():
            self.assertLessEqual(self.shipment_queries(n), ceiling, f'N={n}')
