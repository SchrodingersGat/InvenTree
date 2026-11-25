"""Generic stock consumption workflow.

Stock is "consumed" when it leaves inventory into another object: a build order,
a sales order, another stock item (installation), a customer, etc. Any model which
implements StockConsumerMixin can consume stock.

- consume_stock() marks stock as consumed, splitting off partial quantities (batched)
- release_stock() returns consumed stock back into inventory

The delete policy of a consumer is applied by ConsumedStockRelation (generic.models.modelmixins).
By default consumed stock is kept when its consumer is deleted, leaving a dangling reference.
"""

import copy
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model
from django.db.models.base import ModelState
from django.utils.translation import gettext_lazy as _

import InvenTree.tasks
from audit.helpers import (
    audit_event,
    audited_bulk_update,
    copy_related_cache,
    record_created,
    write_batched,
)
from generic.models.modelmixins import StockConsumerMixin
from InvenTree.helpers_db import bulk_create_and_fetch
from plugin.events import bulk_trigger_event, trigger_event
from stock.events import StockEvents
from stock.status_codes import StockHistoryCode

# Note: stock.models imports this module, so StockItem is resolved via the app registry
# (and type annotations refer to the generic Model class)


@dataclass
class ConsumeRequest:
    """A request to consume a quantity of a stock item.

    Attributes:
        stock_item: The StockItem to consume from
        quantity: The quantity to consume (clamped to the available quantity)
        consumer: The object which consumes the stock (must implement StockConsumerMixin)
        deltas: Additional audit deltas to record (e.g. {'buildorder': build})
        ref: Caller-defined handle (e.g. a BuildItem), returned with the result
    """

    stock_item: Model
    quantity: Decimal
    consumer: StockConsumerMixin
    deltas: dict = field(default_factory=dict)
    ref: Any = None


@dataclass
class ConsumeResult:
    """The outcome of a single ConsumeRequest.

    Attributes:
        ref: The 'ref' of the originating request
        item: The consumed StockItem (the original item, or a newly split-off item)
        source: The StockItem which was consumed from
        quantity: The quantity actually consumed
        consumer: The object which consumed the stock
    """

    ref: Any
    item: Model
    source: Model
    quantity: Decimal
    consumer: StockConsumerMixin


def get_consumed_event(consumer: StockConsumerMixin) -> int:
    """Return the StockHistoryCode recorded when stock is consumed by the provided consumer."""
    name = getattr(consumer, 'STOCK_CONSUMED_EVENT', None)

    if not name or not hasattr(StockHistoryCode, name):
        raise ValueError(
            f'{type(consumer).__name__} does not define a valid STOCK_CONSUMED_EVENT'
        )

    return getattr(StockHistoryCode, name).value


def get_consumer_delta_key(consumer) -> str:
    """Return the key under which the consumer (instance or model class) is recorded in the audit deltas."""
    key = getattr(consumer, 'STOCK_CONSUMED_DELTA_KEY', None)

    if not key:
        raise ValueError(
            f'{type(consumer).__name__} does not define STOCK_CONSUMED_DELTA_KEY'
        )

    return key


def validate_consumer(consumer) -> None:
    """Check that the provided object can be used as a stock consumer."""
    if not isinstance(consumer, StockConsumerMixin):
        raise ValidationError(_('Invalid stock consumer'))

    if consumer.pk is None:
        raise ValidationError(_('Stock consumer must be saved before consuming stock'))

    # Raises ValueError for a misconfigured consumer class (programming error)
    get_consumed_event(consumer)
    get_consumer_delta_key(consumer)


def to_decimal(value) -> Decimal:
    """Convert a quantity to Decimal, avoiding float -> Decimal precision artifacts."""
    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


@transaction.atomic
def consume_stock(
    requests: Iterable[ConsumeRequest], *, user=None, notes: str = ''
) -> list[ConsumeResult]:
    """Consume stock items into their consumers, in a batched operation.

    For each request:
    - The quantity is clamped to the quantity remaining in the stock item
      (multiple requests may draw on the same stock item)
    - If less than the remaining quantity is consumed, it is split off into a new stock item
    - The consumed item is marked as consumed by the consumer, and removed from its location

    The operation is batched: StockItem rows are locked (in pk order) once, split items
    are bulk-created, and changes are bulk-updated with a single audit entry per item.

    Arguments:
        requests: The ConsumeRequest objects to process
        user: The user performing the operation
        notes: Notes to record against each audit entry

    Returns:
        A ConsumeResult for each request which consumed a non-zero quantity

    Raises:
        ValidationError: If a consumer is invalid, a stock item has already been
            consumed, or a stock item would consume itself
    """
    import part.tasks

    StockItem = apps.get_model('stock', 'StockItem')

    requests = list(requests)

    if not requests:
        return []

    for request in requests:
        validate_consumer(request.consumer)

    # Lock the distinct StockItem rows (in pk order, to avoid deadlocks).
    # Stock adjustments elsewhere lock these same rows, so concurrent
    # adjustments cannot be silently overwritten.
    pks = {request.stock_item.pk for request in requests}

    locked = list(
        StockItem.objects
        .select_for_update()
        .filter(pk__in=pks)
        .order_by('pk')
        .values_list('pk', flat=True)
    )

    # Re-read the (now locked) items, with the related objects needed for auditing
    items = {
        item.pk: item
        for item in StockItem.objects.filter(pk__in=locked).select_related(
            'part', 'location'
        )
    }

    # Items which have been fully consumed by an earlier request in this call
    fully_consumed: set[int] = set()

    # Items which have been checked for availability
    checked: set[int] = set()

    # Items whose fields are modified (and must be written back)
    touched: dict[int, StockItem] = {}

    split_items = []  # (source, new_item, quantity)
    results: list[ConsumeResult] = []
    deltas = []  # Per-result audit deltas (aligned with 'results')

    for request in requests:
        consumer = request.consumer
        item = items.get(request.stock_item.pk)

        if item is None:
            # The stock item no longer exists - skip
            continue

        if item.pk not in checked:
            checked.add(item.pk)

            if not item.is_in_stock(check_status=False, check_quantity=False):
                raise ValidationError(
                    _('Stock item {item} is not available for consumption').format(
                        item=item.pk
                    )
                )

        if isinstance(consumer, StockItem) and consumer.pk == item.pk:
            raise ValidationError(_('Item cannot consume itself'))

        if not consumer.can_consume_stock(item):
            raise ValidationError(
                _('Stock item {item} cannot be consumed by {consumer}').format(
                    item=item.pk, consumer=consumer
                )
            )

        remaining = Decimal(0) if item.pk in fully_consumed else item.quantity

        quantity = max(Decimal(0), min(to_decimal(request.quantity), remaining))

        if quantity <= 0:
            continue

        if quantity < remaining:
            # Split off exactly the consumed quantity into a new StockItem,
            # leaving the remainder in place as available stock
            target = copy.copy(item)
            target._state = ModelState()
            target.pk = None
            target.quantity = quantity
            target.parent = item

            item.quantity -= quantity
            split_items.append((item, target, quantity))
        else:
            target = item
            fully_consumed.add(item.pk)

        # Assign via the GenericForeignKey, which also caches the consumer
        target.consumed_by = consumer
        target.location = None

        touched[item.pk] = item

        results.append(ConsumeResult(request.ref, target, item, quantity, consumer))
        deltas.append({
            get_consumer_delta_key(consumer): consumer,
            'quantity': quantity,
            **request.deltas,
        })

    if not results:
        return []

    # Bulk-create the newly split-off stock items - this resolves their primary keys.
    # Evaluate into a list, so that indexing does not re-query the database.
    new_items = list(
        bulk_create_and_fetch(StockItem, [new for _, new, _ in split_items])
    )

    # Back-populate the resolved primary keys onto the in-memory objects,
    # which are referenced by the results (on MySQL, 'new_items' are distinct objects)
    for idx, (source, new_item, _quantity) in enumerate(split_items):
        new_item.pk = new_items[idx].pk
        new_item._state.adding = False

        # Retain the cached related objects (avoid per-item lookups)
        copy_related_cache(source, new_item)
        copy_related_cache(source, new_items[idx])
        new_items[idx].consumed_by = new_item.consumed_by

    # Record the operation in the audit log - a single entry for each stock item
    with write_batched(), ExitStack() as events:

        def audit(instance, code, entry_deltas):
            """Record a named event against the provided instance."""
            events.enter_context(
                audit_event(
                    instance, code, notes=notes, deltas=entry_deltas, actor=user
                )
            )

        for source, new_item, quantity in split_items:
            audit(
                source,
                StockHistoryCode.SPLIT_CHILD_ITEM,
                {
                    'stockitem': new_item,
                    'removed': quantity,
                    'quantity': source.quantity,
                },
            )

        for result, entry_deltas in zip(results, deltas, strict=True):
            consumer = result.consumer

            audit(result.item, get_consumed_event(consumer), entry_deltas)

            # Installation into another stock item is also recorded against the parent
            if isinstance(consumer, StockItem):
                audit(
                    consumer,
                    StockHistoryCode.INSTALLED_CHILD_ITEM,
                    {'stockitem': result.item, 'quantity': result.quantity},
                )

        # Newly split items are recorded as created (with the consume event)
        record_created(StockItem, new_items)

        # Flush the field changes of the existing items (quantity reductions, consumption)
        # Note: Newly split items were created in their final state
        audited_bulk_update(
            StockItem,
            list(touched.values()),
            ['quantity', 'location', 'consumed_by_model_type', 'consumed_by_model_id'],
        )

    # Queue the plugin events in bulk, rather than one task per item
    bulk_trigger_event(
        StockEvents.ITEM_SPLIT,
        [((), {'id': new.pk, 'parent': source.pk}) for source, new, _ in split_items],
    )

    bulk_trigger_event(
        StockEvents.ITEM_CONSUMED,
        [
            (
                (),
                {
                    'id': result.item.pk,
                    'quantity': float(result.quantity),
                    'consumer_model_type': result.consumer._meta.model_name,
                    'consumer_id': result.consumer.pk,
                },
            )
            for result in results
        ],
    )

    # bulk_update() / bulk_create() do not fire the StockItem post_save signal,
    # which normally triggers a low-stock check - so queue it once per distinct part
    InvenTree.tasks.bulk_offload_task(
        part.tasks.notify_low_stock_if_required,
        [((part_id,), {}) for part_id in {item.part_id for item in touched.values()}],
        group='notification',
        force_async=True,
    )

    return results


@transaction.atomic
def release_stock(
    item: Model,
    location: Optional[Model] = None,
    *,
    user=None,
    quantity: Optional[Decimal] = None,
    status=None,
    code: Optional[int] = None,
    notes: str = '',
    merge: bool = False,
    deltas: Optional[dict] = None,
) -> Model:
    """Return consumed stock back into inventory.

    Arguments:
        item: The consumed StockItem
        location: The location to return the stock to
        user: The user performing the operation
        quantity: The quantity to return (default = the entire item). A partial
            quantity is split off, and the remainder stays consumed.
        status: Optionally set the status of the returned stock
        code: The StockHistoryCode to record (default depends on the consumer)
        notes: Notes to record against the audit entry
        merge: If True, attempt to merge the returned stock back into its parent item
        deltas: Additional audit deltas to record (e.g. {'returnorder': order})

    Returns:
        The returned StockItem (which may be a newly split-off item)

    Raises:
        ValidationError: If the item has not been consumed, or the arguments are invalid
    """
    StockItem = apps.get_model('stock', 'StockItem')

    if not item.lock_quantity():
        raise ValidationError(_('Stock item no longer exists'))

    # Re-read the consumer (the provided instance may be stale)
    item.refresh_from_db(fields=['consumed_by_model_type', 'consumed_by_model_id'])

    # A cached (None) consumer is not re-validated by the GenericForeignKey, so clear it
    consumed_by = item._meta.get_field('consumed_by')

    if consumed_by.is_cached(item):
        consumed_by.delete_cached_value(item)

    if not item.is_consumed:
        raise ValidationError(_('Stock item has not been consumed'))

    # The consumer may have been deleted (KEEP policy), leaving a dangling reference
    consumer = item.consumed_by
    consumer_model = item.consumer_model

    if location and location.structural:
        raise ValidationError({
            'location': _('Cannot assign stock to structural location')
        })

    target = item

    if quantity is not None and not item.serialized:
        quantity = to_decimal(quantity)

        if quantity <= 0:
            raise ValidationError({'quantity': _('Quantity must be greater than zero')})

        if quantity > item.quantity:
            raise ValidationError({'quantity': _('Quantity exceeds available stock')})

        if quantity < item.quantity:
            # The split-off item inherits the consumer, which is cleared below
            target = item.splitStock(quantity, None, user, notes=notes)

    installed = consumer_model is not None and issubclass(consumer_model, StockItem)

    if code is None:
        code = (
            StockHistoryCode.REMOVED_FROM_ASSEMBLY
            if installed
            else StockHistoryCode.RETURNED_TO_STOCK
        )

    extra_deltas = deltas or {}

    deltas = {
        # A deleted consumer is recorded by its (dangling) primary key
        get_consumer_delta_key(consumer_model): consumer
        if consumer is not None
        else item.consumed_by_model_id,
        'quantity': float(target.quantity),
    }

    if location:
        deltas['location'] = location

    # Record the customer which the stock is returned from (e.g. via a SalesOrder)
    if consumer is not None and (customer := consumer.get_consuming_customer()):
        deltas.setdefault('customer', customer)

    deltas.update(extra_deltas)

    if status is not None and not target.compare_status(status):
        old_custom_status = target.get_custom_status()
        old_status_logical = target.status
        target.set_status(status)
        deltas['status'] = status
        deltas['status_logical'] = target.status
        deltas['old_status'] = old_custom_status or old_status_logical
        deltas['old_status_logical'] = old_status_logical

    target.consumed_by_model_type = None
    target.consumed_by_model_id = None
    target.location = location

    with ExitStack() as events:
        events.enter_context(
            target.tracking_event(code, user, notes=notes, deltas=deltas)
        )

        # Removal from another stock item is also recorded against the parent
        if installed and consumer is not None:
            events.enter_context(
                consumer.tracking_event(
                    StockHistoryCode.REMOVED_CHILD_ITEM,
                    user,
                    notes=notes,
                    deltas={'stockitem': target, 'quantity': float(target.quantity)},
                )
            )

        target.save(add_note=False)

    target.clearAllocations()

    trigger_event(StockEvents.ITEM_RETURNED_TO_STOCK, id=target.pk)

    if (
        merge
        and not target.serialized
        and target.parent
        and target.parent.is_in_stock(check_status=False, check_quantity=False)
        and target.location == target.parent.location
    ):
        target.parent.merge_stock_items(
            {target}, user=user, location=location, notes=notes
        )
        return target.parent

    return target
