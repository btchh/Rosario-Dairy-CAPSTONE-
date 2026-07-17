"""
Tests for inventory app — covers the bug fixes made in the Round 3 audit session.

Run with: python manage.py test inventory

NOTE on concurrency: standard Django TestCase runs on a single connection, so it
cannot exercise real select_for_update() row-locking the way two concurrent
requests would in production against Postgres. These tests verify the *logic*
each race-condition fix depends on (correct validation, correct math, correct
rejection of bad states) rather than the locking itself. A true concurrency
test would need TransactionTestCase + threading + a real Postgres connection,
noted inline where relevant.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from inventory.models import (
    Category, Product, ProductBatch, Ingredient, IngredientBatch,
    Supplier, StockAdjustment, StockCount,
)
from inventory.services.batch_service import BatchService

User = get_user_model()


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user( # type: ignore
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


class CreateStockAdjustmentTests(TestCase):
    """Covers: race-condition fix (lock/re-fetch), correction upper-bound cap."""

    def setUp(self):
        self.user = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def test_rejects_zero_quantity_for_non_correction(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spoilage', quantity=Decimal('0.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )

    def test_rejects_negative_quantity_for_non_correction(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spillage', quantity=Decimal('-5.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )

    def test_rejects_quantity_exceeding_remaining_stock(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spoilage', quantity=Decimal('150.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # unchanged

    def test_valid_spoilage_deducts_correctly_and_persists(self):
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('20.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('80.00'))

    def test_depleting_batch_sets_depleted_status(self):
        BatchService.create_stock_adjustment(
            adjustment_type='correction', quantity=Decimal('100.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('0.00'))
        self.assertEqual(self.batch.status, 'depleted')

    def test_spoilage_or_expired_sets_disposed_not_depleted(self):
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('100.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'disposed')

    def test_requires_exactly_one_of_product_or_ingredient_batch(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='correction', quantity=Decimal('1.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
            )  # neither provided

    # --- correction upper-bound cap (fix #11) ---

    def test_correction_cannot_push_remaining_above_initial(self):
        """A correction that would add more stock than was ever received must be rejected."""
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='correction', quantity=Decimal('-50.00'),  # adds 50 back
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,  # remaining=100, initial=100 -> would become 150
            )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # unchanged, rejected

    def test_correction_within_initial_quantity_succeeds(self):
        """Sanity check: a correction that adds back stock but stays within initial_quantity is fine."""
        self.batch.remaining_quantity = Decimal('60.00')
        self.batch.save()
        BatchService.create_stock_adjustment(
            adjustment_type='correction', quantity=Decimal('-30.00'),  # adds 30 back
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,  # 60 + 30 = 90, still <= initial 100
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))


class ReconcileTests(TestCase):
    """Covers: reconcile() stale-read fix, null unit_price guard, IngBatchSerializer unit_price auto-fill gap."""

    def setUp(self):
        self.user = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def test_reconcile_with_shortage_creates_correction_adjustment(self):
        count = BatchService.reconcile(
            counted_quantity=Decimal('90.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.expected_quantity, Decimal('100.00'))
        self.assertEqual(count.variance, Decimal('-10.00'))
        self.assertIsNotNone(count.resulting_adjustment)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_reconcile_with_no_variance_creates_no_adjustment(self):
        count = BatchService.reconcile(
            counted_quantity=Decimal('100.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.variance, Decimal('0.00'))
        self.assertIsNone(count.resulting_adjustment)

    def test_reconcile_reads_current_db_state_not_stale_instance(self):
        """
        Simulates the staleness bug: caller holds an old in-memory batch object
        (remaining=100) while the real DB row has since changed (remaining=70,
        as if a concurrent sale happened). reconcile() must use the current DB
        value, not the stale one passed in.
        """
        stale_batch = ProductBatch.objects.get(pk=self.batch.pk)  # snapshot at remaining=100

        # Simulate a concurrent sale/adjustment changing the real row
        self.batch.remaining_quantity = Decimal('70.00')
        self.batch.save()

        count = BatchService.reconcile(
            counted_quantity=Decimal('65.00'), counted_by=self.user,
            product_batch=stale_batch,  # caller's stale object, remaining=100
        )
        # expected must reflect the CURRENT db value (70), not the stale one (100)
        self.assertEqual(count.expected_quantity, Decimal('70.00'))
        self.assertEqual(count.variance, Decimal('-5.00'))

    def test_reconcile_raises_on_null_unit_price_with_variance(self):
        self.batch.unit_price = None
        self.batch.save()
        with self.assertRaises(ValueError):
            BatchService.reconcile(
                counted_quantity=Decimal('90.00'), counted_by=self.user,
                product_batch=self.batch,
            )

    def test_reconcile_allows_null_unit_price_when_no_variance(self):
        """No variance means no adjustment is created, so a null unit_price shouldn't block it."""
        self.batch.unit_price = None
        self.batch.save()
        count = BatchService.reconcile(
            counted_quantity=Decimal('100.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.variance, Decimal('0.00'))
        self.assertIsNone(count.resulting_adjustment)

    def test_ingredient_batch_auto_fills_unit_price_from_ingredient(self):
        """
        Covers the IngBatchSerializer.create() gap fix: an ingredient batch
        created without an explicit unit_price should inherit it from the
        ingredient, matching ProductBatch's existing behavior.
        """
        from inventory.serializers import IngBatchSerializer

        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        serializer = IngBatchSerializer(data={
            'ingredient_id': ingredient.pk,
            'quantity': '50.00',
            'expiration_date': '2026-08-01',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.unit_price, Decimal('25.00'))


class BatchQuantityUpdateTests(TestCase):
    """Covers: fix #7 — PATCH quantity used to silently no-op."""

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def test_patching_quantity_does_not_error_and_does_not_touch_remaining_quantity(self):
        """
        quantity is intentionally write_only/create-only. This test locks in
        that PATCHing it is silently ignored rather than raising OR actually
        changing stock outside the audited adjustment/reconcile paths —
        this is the corrected, intentional behavior, not the old bug.
        """
        from inventory.serializers import ProdBatchSerializer

        serializer = ProdBatchSerializer(
            self.batch, data={'quantity': '9999.00', 'notes': 'test note'}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # untouched
        self.assertEqual(self.batch.notes, 'test note')  # other fields still patch fine

    def test_patching_expiration_date_persists(self):
        from inventory.serializers import ProdBatchSerializer

        serializer = ProdBatchSerializer(
            self.batch, data={'expiration_date': '2027-01-15'}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(str(self.batch.expiration_date), '2027-01-15')


class DeductBatchTests(TestCase):
    """Sanity coverage for existing FEFO deduction logic (already correct, not part of this
    session's fixes, but included since it's the other place remaining_quantity is mutated)."""

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        # Two batches, different expiration dates — FEFO should drain the soonest first.
        self.batch_soon = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-004',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('30.00'), remaining_quantity=Decimal('30.00'),
            expiration_date='2026-08-01',
        )
        self.batch_later = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-005',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date='2026-09-01',
        )

    def test_deducts_from_earliest_expiring_batch_first(self):
        consumed = BatchService.deduct_product_batch(self.product, Decimal('20.00'))
        self.assertEqual(len(consumed), 1)
        self.batch_soon.refresh_from_db()
        self.batch_later.refresh_from_db()
        self.assertEqual(self.batch_soon.remaining_quantity, Decimal('10.00'))
        self.assertEqual(self.batch_later.remaining_quantity, Decimal('50.00'))  # untouched

    def test_deducts_across_multiple_batches_when_first_is_insufficient(self):
        consumed = BatchService.deduct_product_batch(self.product, Decimal('40.00'))
        self.assertEqual(len(consumed), 2)
        self.batch_soon.refresh_from_db()
        self.batch_later.refresh_from_db()
        self.assertEqual(self.batch_soon.remaining_quantity, Decimal('0.00'))
        self.assertEqual(self.batch_soon.status, 'depleted')
        self.assertEqual(self.batch_later.remaining_quantity, Decimal('40.00'))

    def test_raises_on_insufficient_total_stock(self):
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('1000.00'))

    def test_raises_on_zero_or_negative_quantity(self):
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('0.00'))