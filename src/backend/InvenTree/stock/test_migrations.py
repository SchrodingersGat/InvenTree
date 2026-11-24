"""Unit tests for data migrations in the 'stock' app."""

from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestCreationDateMigration(MigratorTestCase):
    """Test the backfill data migration for StockItem.creation_date (stock.0121).

    The migration has two passes:
    - Pass 1: items with a CREATED (tracking_type=1) entry get creation_date from that entry.
    - Pass 2: remaining nulls get min(updated, stocktake_date, earliest_tracking_entry).

    Six scenarios are exercised to cover every meaningful code path.
    """

    migrate_from = ('stock', '0119_alter_stockitemtestresult_date')
    migrate_to = ('stock', '0122_alter_stockitem_creation_date')

    def prepare(self):
        """Create StockItem entries with varied data to exercise all backfill paths."""
        import datetime

        from django.conf import settings
        from django.db import connection

        Part = self.old_state.apps.get_model('part', 'part')
        StockItemTracking = self.old_state.apps.get_model('stock', 'stockitemtracking')

        utc = datetime.timezone.utc if settings.USE_TZ else None

        part = Part.objects.create(
            name='Migration Test Part', level=0, tree_id=1, lft=0, rght=0
        )

        def make_item(stocktake_date=None):
            """Insert a StockItem row via raw SQL, bypassing the duplicate-column ORM bug.

            The historical model at migration 0119 has status_custom_key enumerated twice
            (once from contribute_to_class on the status field, once from migration 0113's
            explicit AddField), so ORM-generated INSERTs fail with
            'column specified more than once'.  Raw SQL avoids the ORM field list entirely.
            Raw SQL also leaves updated=NULL (no DB-level default for auto_now), which
            makes Scenario 6 a clean "no date sources available" case.
            """
            insert_sql = """
                INSERT INTO stock_stockitem
                    (part_id, quantity, level, tree_id, lft, rght,
                     status, delete_on_deplete, review_needed, is_building,
                     link, serial_int, barcode_data, barcode_hash)
                VALUES (%s, 1, 0, 0, 0, 0, 10, false, false, false, '', 0, '', '')
                """
            with connection.cursor() as cursor:
                # MySQL has no RETURNING support at all (not even a syntax error
                # workaround) - fall back to cursor.lastrowid there, and use
                # RETURNING elsewhere since psycopg's cursor has no lastrowid.
                if connection.features.can_return_rows_from_bulk_insert:
                    cursor.execute(insert_sql + 'RETURNING id', [part.pk])
                    pk = cursor.fetchone()[0]
                else:
                    cursor.execute(insert_sql, [part.pk])
                    pk = cursor.lastrowid
                if stocktake_date is not None:
                    cursor.execute(
                        'UPDATE stock_stockitem SET stocktake_date = %s WHERE id = %s',
                        [stocktake_date, pk],
                    )
            return pk

        def add_tracking(pk, tracking_type, date):
            """Create a tracking entry, then override its auto_now_add date."""
            entry = StockItemTracking.objects.create(
                item_id=pk, tracking_type=tracking_type
            )
            # auto_now_add prevents setting date on INSERT; use UPDATE to set a historical value
            StockItemTracking.objects.filter(pk=entry.pk).update(date=date)

        # --- Scenario 1 ---
        # Item with a single CREATED (type=1) tracking entry.
        # Pass 1 should set creation_date = that entry's date.
        pk = make_item()
        add_tracking(pk, 1, datetime.datetime(2022, 1, 15, 10, 0, 0, tzinfo=utc))
        self.pk_s1 = pk
        self.expected_s1 = datetime.datetime(2022, 1, 15, 10, 0, 0, tzinfo=utc)

        # --- Scenario 2 ---
        # Item with a CREATED entry (newer date) AND an older non-CREATED entry.
        # Pass 1 sets creation_date = CREATED entry date; older entry is ignored.
        # This verifies pass 1 wins over pass 2's min() logic.
        pk = make_item()
        add_tracking(
            pk, 1, datetime.datetime(2023, 6, 1, 0, 0, 0, tzinfo=utc)
        )  # CREATED, newer
        add_tracking(
            pk, 2, datetime.datetime(2018, 3, 10, 0, 0, 0, tzinfo=utc)
        )  # non-CREATED, older
        self.pk_s2 = pk
        self.expected_s2 = datetime.datetime(2023, 6, 1, 0, 0, 0, tzinfo=utc)
        self.rejected_s2 = datetime.date(2018, 3, 10)

        # --- Scenario 3 ---
        # Item with only non-CREATED tracking entries.
        # Pass 2 uses min(earliest_entry_date), so earliest entry wins.
        pk = make_item()
        add_tracking(
            pk, 2, datetime.datetime(2021, 7, 20, 0, 0, 0, tzinfo=utc)
        )  # later
        add_tracking(
            pk, 3, datetime.datetime(2020, 2, 14, 0, 0, 0, tzinfo=utc)
        )  # earliest
        self.pk_s3 = pk
        self.expected_s3 = datetime.datetime(2020, 2, 14, 0, 0, 0, tzinfo=utc)

        # --- Scenario 4 ---
        # Item with only stocktake_date set; no tracking entries.
        # Pass 2 uses stocktake_as_datetime (in the past) as the date.
        pk = make_item(stocktake_date=datetime.date(2019, 11, 5))
        self.pk_s4 = pk
        self.expected_s4_date = datetime.date(2019, 11, 5)

        # --- Scenario 5 ---
        # Item with stocktake_date AND a non-CREATED tracking entry where the tracking
        # entry is older than stocktake_date.
        # Pass 2 uses min(stocktake, tracking); tracking wins.
        pk = make_item(stocktake_date=datetime.date(2021, 4, 1))
        add_tracking(pk, 2, datetime.datetime(2017, 8, 22, 0, 0, 0, tzinfo=utc))
        self.pk_s5 = pk
        self.expected_s5 = datetime.datetime(2017, 8, 22, 0, 0, 0, tzinfo=utc)

        # --- Scenario 6 ---
        # Item inserted via raw SQL with no stocktake_date and no tracking entries,
        # so updated=NULL. No date sources exist → creation_date stays NULL after migration.
        pk = make_item()
        self.pk_s6 = pk

    def test_migration(self):
        """Verify creation_date is correctly backfilled for each scenario."""
        import datetime

        from django.conf import settings

        StockItem = self.new_state.apps.get_model('stock', 'stockitem')
        utc = datetime.timezone.utc if settings.USE_TZ else None

        def at_utc(dt):
            """Normalise to UTC and strip sub-second precision for comparison."""
            return (
                dt.astimezone(utc).replace(microsecond=0)
                if settings.USE_TZ
                else dt.replace(microsecond=0)
            )

        # Scenario 1: CREATED tracking entry → creation_date = entry date
        item = StockItem.objects.get(pk=self.pk_s1)
        self.assertIsNotNone(item.creation_date)
        self.assertEqual(at_utc(item.creation_date), self.expected_s1)

        # Scenario 2: CREATED entry (newer) wins over older non-CREATED entry
        item = StockItem.objects.get(pk=self.pk_s2)
        self.assertIsNotNone(item.creation_date)
        self.assertEqual(at_utc(item.creation_date), self.expected_s2)
        # Explicitly confirm the older non-CREATED date was NOT chosen
        self.assertNotEqual(item.creation_date.astimezone(utc).date(), self.rejected_s2)

        # Scenario 3: Earliest non-CREATED tracking entry wins (pass 2 min())
        item = StockItem.objects.get(pk=self.pk_s3)
        self.assertIsNotNone(item.creation_date)
        self.assertEqual(at_utc(item.creation_date), self.expected_s3)

        # Scenario 4: stocktake_date (past) wins over auto_now 'updated' (now)
        item = StockItem.objects.get(pk=self.pk_s4)
        self.assertIsNotNone(item.creation_date)
        self.assertEqual(
            item.creation_date.astimezone(utc).date(), self.expected_s4_date
        )

        # Scenario 5: Oldest tracking entry wins over stocktake_date (pass 2 min())
        item = StockItem.objects.get(pk=self.pk_s5)
        self.assertIsNotNone(item.creation_date)
        self.assertEqual(at_utc(item.creation_date), self.expected_s5)

        # Scenario 6: updated=NULL, no stocktake, no tracking → creation_date stays NULL
        item = StockItem.objects.get(pk=self.pk_s6)
        self.assertIsNone(item.creation_date)


class TestRemoveMpttFieldsMigration(MigratorTestCase):
    """Test data migration which removes MPTT fields (level, lft, rght, tree_id) from StockItem.

    The 'parent' field itself is untouched by this migration - only the MPTT
    bookkeeping fields are removed. This test ensures that parent-child
    relationships between StockItem objects survive the migration boundary.
    """

    migrate_from = ('stock', '0123_remove_stockitem_review_needed')
    migrate_to = ('stock', '0125_remove_mptt_fields')

    def prepare(self):
        """Create a tree of StockItem objects with parent-child relationships."""
        from django.db import connection

        Part = self.old_state.apps.get_model('part', 'part')
        StockItem = self.old_state.apps.get_model('stock', 'stockitem')

        part = Part.objects.create(
            name='Migration Test Part', level=0, tree_id=0, lft=0, rght=0
        )

        def make_item(quantity, parent_id=None):
            """Insert via raw SQL to avoid duplicate status_custom_key ORM bug."""
            insert_sql = """
                INSERT INTO stock_stockitem
                    (part_id, quantity, level, tree_id, lft, rght,
                     status, delete_on_deplete, is_building,
                     link, serial_int, barcode_data, barcode_hash, parent_id)
                VALUES (%s, %s, 0, 0, 0, 0, 10, false, false, '', 0, '', '', %s)
                """
            with connection.cursor() as cursor:
                # MySQL has no RETURNING support at all - fall back to
                # cursor.lastrowid there, and use RETURNING elsewhere since
                # psycopg's cursor has no lastrowid.
                if connection.features.can_return_rows_from_bulk_insert:
                    cursor.execute(
                        insert_sql + 'RETURNING id', [part.pk, quantity, parent_id]
                    )
                    return cursor.fetchone()[0]
                cursor.execute(insert_sql, [part.pk, quantity, parent_id])
                return cursor.lastrowid

        # Root stock item, with no parent
        root_pk = make_item(100)

        # A set of child items, parented to the root item
        child_pks = [make_item(10, root_pk) for _ in range(3)]

        # A grandchild item, parented to the first child item
        grandchild_pk = make_item(1, child_pks[0])

        # An unrelated top-level item, with no parent
        orphan_pk = make_item(5)

        self.root_pk = root_pk
        self.child_pks = child_pks
        self.grandchild_pk = grandchild_pk
        self.orphan_pk = orphan_pk

        self.assertEqual(StockItem.objects.count(), 6)

    def test_migration(self):
        """Test that parent associations are preserved after MPTT fields are removed."""
        StockItem = self.new_state.apps.get_model('stock', 'stockitem')

        self.assertEqual(StockItem.objects.count(), 6)

        # The MPTT bookkeeping fields should no longer exist on the model
        field_names = {field.name for field in StockItem._meta.get_fields()}
        for removed_field in ['level', 'lft', 'rght', 'tree_id']:
            self.assertNotIn(removed_field, field_names)

        # The root item still has no parent
        root = StockItem.objects.get(pk=self.root_pk)
        self.assertIsNone(root.parent_id)

        # Each child item is still correctly parented to the root item
        for pk in self.child_pks:
            child = StockItem.objects.get(pk=pk)
            self.assertEqual(child.parent_id, self.root_pk)

        # The grandchild item is still correctly parented to the first child item
        grandchild = StockItem.objects.get(pk=self.grandchild_pk)
        self.assertEqual(grandchild.parent_id, self.child_pks[0])

        # The orphan item still has no parent
        orphan = StockItem.objects.get(pk=self.orphan_pk)
        self.assertIsNone(orphan.parent_id)


class TestStockTrackingAuditMigration(MigratorTestCase):
    """Test data migration which copies StockItemTracking entries into the audit log.

    Ensures that every tracking entry is copied exactly once, with the event type,
    user, date, notes, parent (part) reference and deltas transferred correctly.
    """

    migrate_from = ('stock', '0128_remove_stockitem_notes')
    migrate_to = ('stock', '0129_migrate_stock_tracking_to_audit_log')

    def make_item(self, part, quantity, serial=None, location=None) -> int:
        """Insert a StockItem via raw SQL (avoids a duplicate status_custom_key ORM bug)."""
        from django.db import connection

        insert_sql = """
            INSERT INTO stock_stockitem
                (part_id, quantity, status, delete_on_deplete, is_building,
                 link, serial_int, barcode_data, barcode_hash, serial, location_id)
            VALUES (%s, %s, 10, false, false, '', 0, '', '', %s, %s)
            """

        params = [part.pk, quantity, serial, location.pk if location else None]

        with connection.cursor() as cursor:
            if connection.features.can_return_rows_from_bulk_insert:
                cursor.execute(insert_sql + 'RETURNING id', params)
                return cursor.fetchone()[0]

            cursor.execute(insert_sql, params)
            return cursor.lastrowid

    def prepare(self):
        """Create legacy stock tracking entries to be migrated."""
        from datetime import datetime, timezone

        from django.conf import settings

        apps = self.old_state.apps

        User = apps.get_model('auth', 'user')
        Part = apps.get_model('part', 'part')
        Company = apps.get_model('company', 'company')
        SalesOrder = apps.get_model('order', 'salesorder')
        StockLocation = apps.get_model('stock', 'stocklocation')
        StockItem = apps.get_model('stock', 'stockitem')
        StockItemTracking = apps.get_model('stock', 'stockitemtracking')

        self.user = User.objects.create(username='tracker', email='tracker@example.com')

        self.part = Part.objects.create(
            name='Tracked Widget', level=0, tree_id=0, lft=0, rght=0
        )

        self.loc_1 = StockLocation.objects.create(
            name='Shelf', pathstring='Store/Shelf', level=0, tree_id=1, lft=1, rght=2
        )
        self.loc_2 = StockLocation.objects.create(
            name='Bin', pathstring='Store/Bin', level=0, tree_id=2, lft=1, rght=2
        )

        self.customer = Company.objects.create(name='Acme Customer', is_customer=True)
        self.sales_order = SalesOrder.objects.create(
            reference='SO-1234', customer=self.customer, reference_int=1234
        )

        item_pk = self.make_item(self.part, 100, location=self.loc_1)
        other_pk = self.make_item(self.part, 1, serial='SN-42')

        item = StockItem.objects.get(pk=item_pk)
        other = StockItem.objects.get(pk=other_pk)

        self.item_pk = item_pk
        self.other_pk = other_pk

        def track(code, deltas=None, notes='', user=None, stock_item=item, day=1):
            """Create a legacy tracking entry (with a known date)."""
            entry = StockItemTracking.objects.create(
                item=stock_item,
                part=self.part,
                tracking_type=code,
                deltas=deltas,
                notes=notes,
                user=user,
            )

            # MySQL rejects timezone-aware values when USE_TZ is False
            utc = timezone.utc if settings.USE_TZ else None
            date = datetime(2025, 1, day, 12, 0, tzinfo=utc)
            StockItemTracking.objects.filter(pk=entry.pk).update(date=date)

            return entry.pk

        self.entries = {
            'created': track(
                1,
                {'status': 10, 'quantity': 100.0, 'location': self.loc_1.pk},
                notes='Initial stock',
                user=self.user,
                day=1,
            ),
            'moved': track(
                20,
                {'quantity': 100.0, 'location': self.loc_2.pk},
                user=self.user,
                day=2,
            ),
            'counted': track(
                10,
                {
                    'quantity': 90.0,
                    'location': self.loc_1.pk,
                    'old_location': self.loc_2.pk,
                },
                user=self.user,
                day=3,
            ),
            'edited': track(
                5,
                {
                    'status': 55,
                    'status_logical': 55,
                    'old_status': 10,
                    'old_status_logical': 10,
                },
                user=self.user,
                day=4,
            ),
            'custom_status': track(
                5,
                {
                    'status': 1001,
                    'status_logical': 50,
                    'old_status': 55,
                    'old_status_logical': 55,
                },
                day=5,
            ),
            'shipped': track(
                60,
                {
                    'quantity': 5.0,
                    'salesorder': self.sales_order.pk,
                    'customer': self.customer.pk,
                    'customer_name': 'Acme Customer',
                },
                user=self.user,
                day=6,
            ),
            'split': track(42, {'stockitem': other_pk, 'removed': 5.0}, day=7),
            'missing_reference': track(
                42, {'stockitem': 999999, 'removed': 1.0}, day=8
            ),
            'deleted_item': track(12, {'removed': 1.0}, stock_item=None, day=9),
            'legacy': track(0, None, notes='Legacy entry', stock_item=other, day=10),
        }

        self.assertEqual(StockItemTracking.objects.count(), len(self.entries))

    def migrated(self, name):
        """Return the audit log entry migrated from the named tracking entry."""
        AuditLogEntry = self.new_state.apps.get_model('audit', 'auditlogentry')

        return AuditLogEntry.objects.get(
            additional_data__tracking_id=self.entries[name]
        )

    def check_integrity(self):
        """Every tracking entry is copied exactly once, with all common fields transferred."""
        apps = self.new_state.apps

        AuditLogEntry = apps.get_model('audit', 'auditlogentry')
        StockItemTracking = apps.get_model('stock', 'stockitemtracking')
        ContentType = apps.get_model('contenttypes', 'contenttype')

        stock_ct = ContentType.objects.get(app_label='stock', model='stockitem')
        part_ct = ContentType.objects.get(app_label='part', model='part')

        # The legacy entries are retained
        self.assertEqual(StockItemTracking.objects.count(), len(self.entries))

        migrated = AuditLogEntry.objects.filter(
            content_type=stock_ct, additional_data__has_key='tracking_id'
        )

        self.assertEqual(migrated.count(), len(self.entries))
        self.assertEqual(
            sorted(entry.additional_data['tracking_id'] for entry in migrated),
            sorted(self.entries.values()),
        )

        for tracking in StockItemTracking.objects.all():
            entry = migrated.get(additional_data__tracking_id=tracking.pk)

            self.assertEqual(entry.timestamp, tracking.date)
            self.assertEqual(entry.actor_id, tracking.user_id)
            self.assertEqual(entry.notes, tracking.notes or None)

            # The entry is recorded against the stock item
            self.assertEqual(entry.content_type_id, stock_ct.pk)
            self.assertEqual(entry.object_id, tracking.item_id)
            self.assertEqual(
                entry.object_pk, str(tracking.item_id) if tracking.item_id else ''
            )

            # The parent reference is the part
            self.assertEqual(entry.parent_content_type_id, part_ct.pk)
            self.assertEqual(entry.parent_object_pk, str(self.part.pk))

            # The event type is transferred ('EDITED' entries are regular updates)
            if tracking.tracking_type == 5:
                self.assertIsNone(entry.event_type)
            else:
                self.assertEqual(entry.event_type, tracking.tracking_type)

    def check_created(self):
        """A creation entry is recorded as a CREATE entry, with the initial values as field changes."""
        entry = self.migrated('created')

        self.assertEqual(entry.action, 0)
        self.assertEqual(entry.event_type, 1)
        self.assertEqual(entry.notes, 'Initial stock')
        self.assertEqual(entry.actor_id, self.user.pk)
        self.assertEqual(entry.actor_username, 'tracker')
        self.assertEqual(entry.actor_email, 'tracker@example.com')
        self.assertEqual(entry.object_repr, '100 x Tracked Widget')

        self.assertEqual(
            entry.changes,
            {
                'status': [None, 10],
                'quantity': [None, '100'],
                'location': [None, self.loc_1.pk],
            },
        )

        self.assertNotIn('deltas', entry.additional_data)

    def check_moved(self):
        """A move records the destination location as a reference."""
        entry = self.migrated('moved')

        self.assertEqual(entry.action, 1)
        self.assertEqual(entry.event_type, 20)
        self.assertEqual(entry.changes, {})
        self.assertEqual(
            entry.additional_data['deltas'],
            {
                'quantity': 100.0,
                'location': {
                    'model_type': 'stock.stocklocation',
                    'pk': self.loc_2.pk,
                    'str': 'Store/Bin',
                },
            },
        )

    def check_counted(self):
        """A location change (with known previous location) is recorded as a field change."""
        entry = self.migrated('counted')

        self.assertEqual(entry.event_type, 10)
        self.assertEqual(entry.changes, {'location': [self.loc_2.pk, self.loc_1.pk]})
        self.assertEqual(entry.additional_data['deltas'], {'quantity': 90.0})

    def check_status_changes(self):
        """Status changes are recorded as field changes (including custom status values)."""
        entry = self.migrated('edited')

        self.assertEqual(entry.action, 1)
        self.assertIsNone(entry.event_type)
        self.assertEqual(entry.changes, {'status': [10, 55]})
        self.assertNotIn('deltas', entry.additional_data)

        entry = self.migrated('custom_status')

        self.assertEqual(
            entry.changes, {'status': [55, 50], 'status_custom_key': [None, 1001]}
        )

    def check_references(self):
        """References to other objects are converted to {model_type, pk, str}."""
        deltas = self.migrated('shipped').additional_data['deltas']

        self.assertEqual(
            deltas,
            {
                'quantity': 5.0,
                'salesorder': {
                    'model_type': 'order.salesorder',
                    'pk': self.sales_order.pk,
                    'str': 'SO-1234',
                },
                'customer': {
                    'model_type': 'company.company',
                    'pk': self.customer.pk,
                    'str': 'Acme Customer',
                },
            },
        )

        deltas = self.migrated('split').additional_data['deltas']

        self.assertEqual(
            deltas['stockitem'],
            {
                'model_type': 'stock.stockitem',
                'pk': self.other_pk,
                'str': 'Tracked Widget #SN-42',
            },
        )
        self.assertEqual(deltas['removed'], 5.0)

        # A reference to an object which no longer exists
        deltas = self.migrated('missing_reference').additional_data['deltas']

        self.assertEqual(
            deltas['stockitem'],
            {'model_type': 'stock.stockitem', 'pk': 999999, 'str': None},
        )

    def check_deleted_item(self):
        """Entries for deleted stock items are retained against the part."""
        entry = self.migrated('deleted_item')

        self.assertEqual(entry.object_pk, '')
        self.assertIsNone(entry.object_id)
        self.assertEqual(entry.object_repr, 'Tracked Widget')
        self.assertEqual(entry.parent_object_pk, str(self.part.pk))
        self.assertEqual(entry.event_type, 12)

    def check_legacy(self):
        """Legacy entries (without user or deltas) are migrated."""
        entry = self.migrated('legacy')

        self.assertEqual(entry.event_type, 0)
        self.assertIsNone(entry.actor_id)
        self.assertIsNone(entry.actor_username)
        self.assertEqual(entry.notes, 'Legacy entry')
        self.assertEqual(entry.object_repr, 'Tracked Widget #SN-42')
        self.assertEqual(entry.changes, {})
        self.assertEqual(entry.additional_data, {'tracking_id': self.entries['legacy']})

    def test_migration(self):
        """Run all checks against the migrated data.

        Note: A single test method is used, as the migrations are run for each test method.
        """
        for check in [
            self.check_integrity,
            self.check_created,
            self.check_moved,
            self.check_counted,
            self.check_status_changes,
            self.check_references,
            self.check_deleted_item,
            self.check_legacy,
        ]:
            with self.subTest(check=check.__name__):
                check()


class TestConsumedByMigration(MigratorTestCase):
    """Test the data migration which copies the legacy "consumed" fields (stock.0133).

    One item per legacy state (see the precedence table in the migration),
    plus conflict rows, and a reverse round trip.
    """

    migrate_from = ('stock', '0132_stockitem_consumed_by_generic')
    migrate_to = ('stock', '0133_migrate_consumed_by')

    def make_item(self, **kwargs) -> int:
        """Insert a StockItem via raw SQL (avoids a duplicate status_custom_key ORM bug)."""
        from django.db import connection

        fields = {
            'part_id': self.part.pk,
            'quantity': 1,
            'status': 10,
            'delete_on_deplete': False,
            'is_building': False,
            'link': '',
            'serial_int': 0,
            'barcode_data': '',
            'barcode_hash': '',
            **{f'{key}_id': value for key, value in kwargs.items()},
        }

        columns = ', '.join(fields.keys())
        values = ', '.join(['%s'] * len(fields))
        insert_sql = f'INSERT INTO stock_stockitem ({columns}) VALUES ({values}) '
        params = list(fields.values())

        with connection.cursor() as cursor:
            if connection.features.can_return_rows_from_bulk_insert:
                cursor.execute(insert_sql + 'RETURNING id', params)
                return cursor.fetchone()[0]

            cursor.execute(insert_sql, params)
            return cursor.lastrowid

    def prepare(self):
        """Create stock items in each legacy consumed state."""
        apps = self.old_state.apps

        Part = apps.get_model('part', 'part')
        Company = apps.get_model('company', 'company')
        SalesOrder = apps.get_model('order', 'salesorder')
        Build = apps.get_model('build', 'build')

        tree = {'level': 0, 'tree_id': 0, 'lft': 0, 'rght': 0}

        self.part = Part.objects.create(name='Consumable', **tree)

        self.customer = Company.objects.create(name='Customer A', is_customer=True)
        self.other_customer = Company.objects.create(
            name='Customer B', is_customer=True
        )
        self.order = SalesOrder.objects.create(
            reference='SO-0133', customer=self.customer, reference_int=133
        )
        self.build = Build.objects.create(
            part=self.part, reference='BO-0133', title='Build', quantity=1, **tree
        )

        self.output = self.make_item(build=self.build.pk)

        self.items = {
            'available': self.make_item(),
            'build_install': self.make_item(
                belongs_to=self.output, consumed_by=self.build.pk
            ),
            'manual_install': self.make_item(belongs_to=self.output),
            'shipped': self.make_item(
                sales_order=self.order.pk, customer=self.customer.pk
            ),
            'shipped_mismatch': self.make_item(
                sales_order=self.order.pk, customer=self.other_customer.pk
            ),
            'order_only': self.make_item(sales_order=self.order.pk),
            'build_only': self.make_item(consumed_by=self.build.pk),
            'customer_only': self.make_item(customer=self.customer.pk),
            'conflict': self.make_item(
                consumed_by=self.build.pk, customer=self.customer.pk
            ),
        }

        self.legacy = self.legacy_state(apps)

    def migrate_back(self):
        """Reverse the migration (keeping data), and return the historical apps."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])

        return executor.loader.project_state([self.migrate_from]).apps

    def legacy_state(self, apps) -> dict:
        """Return the legacy consumer fields for each item."""
        StockItem = apps.get_model('stock', 'stockitem')

        return {
            key: StockItem.objects
            .filter(pk=pk)
            .values('belongs_to', 'sales_order', 'consumed_by', 'customer')
            .get()
            for key, pk in self.items.items()
        }

    def consumer(self, apps, key):
        """Return the generic consumer of an item, as (model, pk)."""
        StockItem = apps.get_model('stock', 'stockitem')
        item = StockItem.objects.get(pk=self.items[key])

        if item.consumed_by_model_type is None:
            self.assertIsNone(item.consumed_by_model_id)
            return None

        return (item.consumed_by_model_type.model, item.consumed_by_model_id)

    def test_forward(self):
        """Each legacy state maps to the expected generic consumer."""
        apps = self.new_state.apps

        expected = {
            'available': None,
            'build_install': ('stockitem', self.output),
            'manual_install': ('stockitem', self.output),
            'shipped': ('salesorder', self.order.pk),
            'shipped_mismatch': ('salesorder', self.order.pk),
            'order_only': ('salesorder', self.order.pk),
            'build_only': ('build', self.build.pk),
            'customer_only': ('company', self.customer.pk),
            'conflict': ('build', self.build.pk),
        }

        for key, consumer in expected.items():
            self.assertEqual(self.consumer(apps, key), consumer, key)

        # The build output itself is not consumed
        StockItem = apps.get_model('stock', 'stockitem')
        self.assertIsNone(StockItem.objects.get(pk=self.output).consumed_by_model_type)

        # Legacy fields are left untouched (dropped later, in 0134)
        self.assertEqual(self.legacy_state(apps), self.legacy)

    def test_preflight(self):
        """The pre-flight report counts the items which lose information."""
        import importlib

        migration = importlib.import_module('stock.migrations.0133_migrate_consumed_by')

        # build_install, shipped, shipped_mismatch, conflict
        self.assertEqual(
            migration.preflight_report(self.new_state.apps),
            {'multiple': 4, 'customer_mismatch': 1},
        )

    def test_reverse(self):
        """The reverse migration restores the original state."""
        apps = self.migrate_back()

        StockItem = apps.get_model('stock', 'stockitem')
        self.assertFalse(
            StockItem.objects.filter(consumed_by_model_type__isnull=False).exists()
        )
        self.assertEqual(self.legacy_state(apps), self.legacy)

    def test_reverse_reconstruct(self):
        """Items with no legacy fields (consumed after 0134) are reconstructed."""
        apps = self.new_state.apps

        StockItem = apps.get_model('stock', 'stockitem')
        ContentType = apps.get_model('contenttypes', 'contenttype')

        def ct(app_label, model):
            return ContentType.objects.get(app_label=app_label, model=model)

        # Simulate items consumed via the generic reference only
        new_items = {
            'installed': (ct('stock', 'stockitem'), self.output),
            'shipped': (ct('order', 'salesorder'), self.order.pk),
            'built': (ct('build', 'build'), self.build.pk),
            'assigned': (ct('company', 'company'), self.customer.pk),
            'dangling': (ct('company', 'company'), 99999),
        }

        pks = {}

        for key, (content_type, pk) in new_items.items():
            pks[key] = self.make_item()
            StockItem.objects.filter(pk=pks[key]).update(
                consumed_by_model_type=content_type, consumed_by_model_id=pk
            )

        apps = self.migrate_back()
        StockItem = apps.get_model('stock', 'stockitem')

        def legacy(key):
            return (
                StockItem.objects
                .filter(pk=pks[key])
                .values('belongs_to', 'sales_order', 'consumed_by', 'customer')
                .get()
            )

        empty = dict.fromkeys(['belongs_to', 'sales_order', 'consumed_by', 'customer'])

        # Installed into a build output: the build is restored from the parent
        self.assertEqual(
            legacy('installed'),
            {**empty, 'belongs_to': self.output, 'consumed_by': self.build.pk},
        )
        self.assertEqual(
            legacy('shipped'),
            {**empty, 'sales_order': self.order.pk, 'customer': self.customer.pk},
        )
        self.assertEqual(legacy('built'), {**empty, 'consumed_by': self.build.pk})
        self.assertEqual(legacy('assigned'), {**empty, 'customer': self.customer.pk})

        # A reference to a deleted consumer cannot be restored
        self.assertEqual(legacy('dangling'), empty)

        # The original items are unaffected
        self.assertEqual(self.legacy_state(apps), self.legacy)
