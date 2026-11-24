"""Copy the legacy "consumed" fields on StockItem into the generic consumer reference.

Legacy fields (still present until migration 0134):
- belongs_to: installed in another StockItem
- sales_order: shipped against a SalesOrder
- consumed_by: consumed by a Build
- customer: assigned to a Company

Where more than one legacy field is set, the generic reference takes the
highest-precedence consumer, in the order listed above. The information which is
dropped is still present in the audit history (and in the legacy fields, until 0134).

The copy is performed with set-based UPDATE statements (one per consumer type),
so it remains fast on large stock tables.
"""

import logging

from django.db import migrations
from django.db.models import F, OuterRef, Q, Subquery

logger = logging.getLogger('inventree')

# (legacy field, consumer app label, consumer model name) - in order of precedence
CONSUMER_RULES = [
    ('belongs_to', 'stock', 'stockitem'),
    ('sales_order', 'order', 'salesorder'),
    ('consumed_by', 'build', 'build'),
    ('customer', 'company', 'company'),
]

LEGACY_FIELDS = [rule[0] for rule in CONSUMER_RULES]


def get_content_type(apps, app_label: str, model: str):
    """Return the ContentType for a model (it may not exist yet on a fresh database)."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ct, _created = ContentType.objects.get_or_create(app_label=app_label, model=model)
    return ct


def preflight_report(apps) -> dict:
    """Count (and log) the stock items for which the migration drops information.

    Returns:
        A dict with the following counts:
        - multiple: Items with more than one legacy consumer field set
        - customer_mismatch: Items whose customer differs from the sales order customer
    """
    StockItem = apps.get_model('stock', 'StockItem')

    # Items with (at least) two legacy fields set
    pairs = Q()

    for idx, field in enumerate(LEGACY_FIELDS):
        for other in LEGACY_FIELDS[idx + 1 :]:
            pairs |= Q(**{f'{field}__isnull': False, f'{other}__isnull': False})

    multiple = StockItem.objects.filter(pairs).count()

    customer_mismatch = (
        StockItem.objects.filter(sales_order__isnull=False, customer__isnull=False)
        .exclude(customer=F('sales_order__customer'))
        .count()
    )

    if multiple or customer_mismatch:
        logger.info(
            'stock.0133: %s stock items have multiple consumer fields set; '
            '%s have a customer which differs from the sales order customer',
            multiple,
            customer_mismatch,
        )

    return {'multiple': multiple, 'customer_mismatch': customer_mismatch}


def forward(apps, schema_editor):
    """Copy the legacy consumer fields into the generic consumer reference."""
    StockItem = apps.get_model('stock', 'StockItem')

    preflight_report(apps)

    total = 0

    # Highest precedence first: an item which already has a consumer is skipped
    for field, app_label, model in CONSUMER_RULES:
        ct = get_content_type(apps, app_label, model)

        total += StockItem.objects.filter(**{
            f'{field}__isnull': False,
            'consumed_by_model_type__isnull': True,
        }).update(consumed_by_model_type=ct, consumed_by_model_id=F(field))

    if total:
        logger.info('stock.0133: migrated %s consumed stock items', total)


def reverse(apps, schema_editor):
    """Restore the legacy consumer fields, and clear the generic consumer reference.

    Items which still carry a legacy consumer field were consumed before the forward
    migration (or by legacy code), and their legacy fields are left untouched.

    Items with no legacy field set were consumed after 0134 dropped the legacy fields
    (which 0134's reverse re-created empty), so their legacy fields are reconstructed:
    - StockItem -> belongs_to (and consumed_by from parent.build, for build outputs)
    - SalesOrder -> sales_order (and customer from order.customer)
    - Build -> consumed_by
    - Company -> customer

    A reference to a deleted consumer cannot be restored to a legacy foreign key,
    so such items are released (logged).
    """
    StockItem = apps.get_model('stock', 'StockItem')

    no_legacy = StockItem.objects.filter(
        consumed_by_model_type__isnull=False, **{f'{f}__isnull': True for f in LEGACY_FIELDS}
    )

    restored = 0
    dangling = 0

    for field, app_label, model in CONSUMER_RULES:
        ct = get_content_type(apps, app_label, model)
        Consumer = apps.get_model(app_label, model)

        items = no_legacy.filter(consumed_by_model_type=ct)
        existing = items.filter(
            consumed_by_model_id__in=Consumer.objects.values('pk')
        )

        dangling += items.exclude(pk__in=existing.values('pk')).count()

        updates = {field: F('consumed_by_model_id')}

        if field == 'sales_order':
            updates['customer'] = Subquery(
                Consumer.objects.filter(pk=OuterRef('consumed_by_model_id')).values(
                    'customer'
                )[:1]
            )

        if field == 'belongs_to':
            # Evaluate in Python: MySQL cannot UPDATE a table with a subquery on itself
            rows = list(existing.values_list('pk', 'consumed_by_model_id'))
            parent_builds = dict(
                StockItem.objects.filter(
                    pk__in={parent for _pk, parent in rows}, build__isnull=False
                ).values_list('pk', 'build')
            )

            builds = {}
            for pk, parent in rows:
                builds.setdefault(parent_builds.get(parent), []).append(pk)

            for build, pks in builds.items():
                restored += StockItem.objects.filter(pk__in=pks).update(
                    belongs_to=F('consumed_by_model_id'), consumed_by=build
                )

            continue

        restored += existing.update(**updates)

    StockItem.objects.filter(consumed_by_model_type__isnull=False).update(
        consumed_by_model_type=None, consumed_by_model_id=None
    )

    if restored or dangling:
        logger.info(
            'stock.0133 (reverse): restored legacy fields for %s stock items; '
            '%s items referenced a deleted consumer and were released',
            restored,
            dangling,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('stock', '0132_stockitem_consumed_by_generic'),
    ]

    operations = [migrations.RunPython(forward, reverse_code=reverse)]
