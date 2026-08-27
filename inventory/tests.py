"""
Tests for inventory app — covers the bug fixes made in the Round 3 audit session,
Round 6 (quantity/price validation, BatchSequence race fix, batch_number
uniqueness), and Round 7 (expired batches excluded from FEFO deduction).

Run with: python manage.py test inventory

NOTE on concurrency: standard Django TestCase runs on a single connection, so it
cannot exercise real select_for_update() row-locking the way two concurrent
requests would in production against Postgres. Most tests below verify the
*logic* each fix depends on (correct validation, correct math, correct
rejection of bad states) rather than the locking itself. The one exception is
BatchSequenceConcurrencyTests, which uses TransactionTestCase + threading +
the real Postgres connection to actually exercise the row lock — that's the
one place true concurrency is worth the extra weight, since it's the whole
point of the BatchSequence redesign.
"""
import threading
from datetime import timedelta
from decimal import Decimal
from django.db import IntegrityError
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from inventory.models import (
    Category, Product, ProductBatch, Ingredient, IngredientBatch,
    Supplier, StockAdjustment, StockCount, BatchSequence,
)
from inventory.services.batch_service import BatchService
from inventory.services.batch_sequence_service import next_sequence

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
            expiration_date=timezone.now().date() + timedelta(days=30),
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
            expiration_date=timezone.now().date() + timedelta(days=30),
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
            'expiration_date': (timezone.now().date() + timedelta(days=30)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.unit_price, Decimal('25.00')) # type: ignore


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
            expiration_date=timezone.now().date() + timedelta(days=30),
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

        new_date = timezone.now().date() + timedelta(days=60)
        serializer = ProdBatchSerializer(
            self.batch, data={'expiration_date': new_date.isoformat()}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.expiration_date, new_date)


class DeductBatchTests(TestCase):
    """
    Sanity coverage for FEFO deduction logic. Dates are computed relative to
    'today' (not hardcoded) since Round 7 made deduction expiration-aware —
    a hardcoded date drifts into the past over time and would silently start
    excluding these fixture batches from FEFO instead of actually testing
    the earliest-expiring-first behavior.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        today = timezone.now().date()
        # Two batches, different expiration dates — FEFO should drain the soonest first.
        self.batch_soon = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-004',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('30.00'), remaining_quantity=Decimal('30.00'),
            expiration_date=today + timedelta(days=5),
        )
        self.batch_later = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-005',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=today + timedelta(days=35),
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


# ---------------------------------------------------------------------------
# Round 6: quantity/price validation on batch serializers, BatchSequence
# race-condition fix, batch_number uniqueness constraint
# ---------------------------------------------------------------------------

class BatchQuantityPriceValidationTests(TestCase):
    """
    Covers: ProdBatchSerializer/IngBatchSerializer used to accept quantity <= 0
    and negative unit_price with zero validation. Both now reject via
    min_value on quantity, and unit_price is bounded at >= 0.00 — for
    ProductBatch via an explicit serializer field override, and for
    IngredientBatch via the model-level MinValueValidator that DRF's
    ModelSerializer auto-copies onto the generated field (IngBatchSerializer
    never overrode unit_price, so this is the only thing now guarding it).
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = (timezone.now().date() + timedelta(days=30)).isoformat()

    # --- ProductBatch: quantity ---

    def test_product_batch_rejects_zero_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '0.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_product_batch_rejects_negative_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '-50.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_product_batch_accepts_minimum_valid_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '0.01',
            'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- ProductBatch: unit_price ---

    def test_product_batch_rejects_negative_unit_price(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'unit_price': '-5.00', 'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_product_batch_accepts_zero_unit_price(self):
        """Zero is a boundary-valid price (e.g. a free/promotional batch), not rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'unit_price': '0.00', 'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_product_batch_still_autofills_unit_price_when_omitted(self):
        """Regression guard: the validators shouldn't interfere with the auto-fill-from-product path."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.unit_price, self.product.unit_price) # type: ignore

    # --- IngredientBatch: quantity ---

    def test_ingredient_batch_rejects_zero_quantity(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '0.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_ingredient_batch_rejects_negative_quantity(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '-10.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    # --- IngredientBatch: unit_price (model-level validator, no serializer override) ---

    def test_ingredient_batch_rejects_negative_unit_price(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'unit_price': '-1.00', 'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_ingredient_batch_accepts_zero_unit_price(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'unit_price': '0.00', 'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class ProductIngredientPriceValidationTests(TestCase):
    """
    Covers the model-level MinValueValidator added to Product.unit_price and
    Ingredient.unit_price. ProductSerializer/IngredientSerializer don't
    override unit_price either, so this is enforced the same way as
    IngredientBatch above — via DRF copying the model field's validators.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')

    def test_product_serializer_rejects_negative_unit_price(self):
        from inventory.serializers import ProductSerializer
        serializer = ProductSerializer(data={
            'name': 'Bad Milk', 'unit': 'liter', 'unit_price': '-1.00',
            'shelf_life': 7, 'category_id': self.category.pk,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_ingredient_serializer_rejects_negative_unit_price(self):
        from inventory.serializers import IngredientSerializer
        serializer = IngredientSerializer(data={
            'name': 'Bad Ingredient', 'unit': 'liter', 'unit_price': '-1.00',
            'shelf_life': 3,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_product_model_full_clean_rejects_negative_unit_price(self):
        """
        Documents the model-level guarantee for non-serializer write paths
        (admin, shell, scripts). Note: plain .save() does NOT call
        full_clean() in Django — that's exactly why the serializer-level
        tests above matter most for the actual API, and this test only
        covers callers that explicitly validate.
        """
        product = Product(
            category=self.category, name='Bad Milk', unit='liter',
            unit_price=Decimal('-1.00'), shelf_life=7,
        )
        with self.assertRaises(Exception):
            product.full_clean()

    def test_ingredient_model_full_clean_rejects_negative_unit_price(self):
        ingredient = Ingredient(
            name='Bad Ingredient', unit='liter',
            unit_price=Decimal('-1.00'), shelf_life=3,
        )
        with self.assertRaises(Exception):
            ingredient.full_clean()


class BatchSequenceServiceTests(TestCase):
    """
    Covers next_sequence(): replaces the old count()-based numbering (racy —
    select_for_update() can't lock a row that doesn't exist yet) with a
    dedicated counter row that IS locked and incremented atomically.
    """

    def test_first_call_for_a_prefix_returns_one(self):
        seq = next_sequence('PRD')
        self.assertEqual(seq, 1)

    def test_sequential_calls_increment(self):
        first = next_sequence('PRD')
        second = next_sequence('PRD')
        third = next_sequence('PRD')
        self.assertEqual([first, second, third], [1, 2, 3])

    def test_different_prefixes_are_independent(self):
        prd_seq = next_sequence('PRD')
        ing_seq = next_sequence('ING')
        self.assertEqual(prd_seq, 1)
        self.assertEqual(ing_seq, 1)  # ING's counter didn't inherit PRD's progress

    def test_sequence_row_created_per_prefix_year_month(self):
        next_sequence('PRD')
        self.assertEqual(BatchSequence.objects.filter(prefix='PRD').count(), 1)
        row = BatchSequence.objects.get(prefix='PRD')
        self.assertEqual(row.last_seq, 1)

    def test_repeated_calls_reuse_same_row_not_create_new_ones(self):
        for _ in range(5):
            next_sequence('PRD')
        self.assertEqual(BatchSequence.objects.filter(prefix='PRD').count(), 1)
        self.assertEqual(BatchSequence.objects.get(prefix='PRD').last_seq, 5)

    def test_serializer_create_uses_sequence_and_produces_expected_format(self):
        """End-to-end: ProdBatchSerializer.create() calls next_sequence() and
        the resulting batch_number matches PRD-YYMM-NNN with the right sequence."""
        from inventory.serializers import ProdBatchSerializer

        category = Category.objects.create(name='Dairy')
        product = Product.objects.create(
            category=category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        future_date = (timezone.now().date() + timedelta(days=30)).isoformat()

        serializer1 = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(serializer1.is_valid(), serializer1.errors)
        batch1 = serializer1.save()

        serializer2 = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(serializer2.is_valid(), serializer2.errors)
        batch2 = serializer2.save()

        now = timezone.now()
        expected_prefix = f"PRD-{now.strftime('%y')}{now.strftime('%m')}-"
        self.assertEqual(batch1.batch_number, f"{expected_prefix}001") # type: ignore
        self.assertEqual(batch2.batch_number, f"{expected_prefix}002") # type: ignore

    def test_ingredient_batch_sequence_is_independent_of_product_batch_sequence(self):
        from inventory.serializers import ProdBatchSerializer, IngBatchSerializer

        category = Category.objects.create(name='Dairy')
        product = Product.objects.create(
            category=category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        future_date = (timezone.now().date() + timedelta(days=30)).isoformat()

        prod_serializer = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(prod_serializer.is_valid(), prod_serializer.errors)
        prod_batch = prod_serializer.save()

        ing_serializer = IngBatchSerializer(data={
            'ingredient_id': ingredient.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(ing_serializer.is_valid(), ing_serializer.errors)
        ing_batch = ing_serializer.save()

        self.assertIn('PRD-', prod_batch.batch_number) # type: ignore
        self.assertIn('ING-', ing_batch.batch_number) # type: ignore
        self.assertTrue(prod_batch.batch_number.endswith('001')) # type: ignore
        self.assertTrue(ing_batch.batch_number.endswith('001')) # type: ignore


class BatchSequenceConcurrencyTests(TransactionTestCase):
    """
    The actual race the BatchSequence redesign exists to close. Unlike
    TestCase, TransactionTestCase doesn't wrap the test in one shared
    connection/transaction, so separate threads here get separate real
    Postgres connections and can genuinely contend for the row lock inside
    next_sequence(). Ten threads call next_sequence('PRD') concurrently;
    a correct implementation hands out exactly 1..10 with no duplicates and
    no gaps. The old count()-based approach would be expected to produce
    duplicates under this same test.
    """

    def test_concurrent_calls_never_produce_duplicate_sequence_numbers(self):
        results = []
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                seq = next_sequence('PRD')
                with lock:
                    results.append(seq)
            except Exception as e:
                with lock:
                    errors.append(e)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"next_sequence() raised under concurrency: {errors}")
        self.assertEqual(len(results), 10)
        self.assertEqual(sorted(results), list(range(1, 11)))  # no dupes, no gaps


class BatchNumberUniquenessTests(TestCase):
    """
    Covers the unique=True constraint added to ProductBatch.batch_number and
    IngredientBatch.batch_number. BatchSequence makes collisions practically
    impossible now, but this is the hard DB-level backstop for any write
    path that bypasses the sequence entirely (bulk import, manual fixture,
    a future bug).
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = timezone.now().date() + timedelta(days=30)

    def test_duplicate_product_batch_number_rejected_at_db_level(self):
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DUP-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        with self.assertRaises(IntegrityError):
            ProductBatch.objects.create(
                product=self.product, batch_number='PRD-DUP-001',  # same number
                unit_price=Decimal('50.00'),
                initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
                expiration_date=self.future_date,
            )

    def test_duplicate_ingredient_batch_number_rejected_at_db_level(self):
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-DUP-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        with self.assertRaises(IntegrityError):
            IngredientBatch.objects.create(
                ingredient=self.ingredient, batch_number='ING-DUP-001',  # same number
                unit_price=Decimal('25.00'),
                initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
                expiration_date=self.future_date,
            )

    def test_product_and_ingredient_batches_can_share_a_prefix_free_number_space(self):
        """Uniqueness is per-model (separate DB tables), so a PRD-prefixed and
        an ING-prefixed number never collide regardless of content — this
        just confirms the constraint doesn't accidentally span both tables."""
        ProductBatch.objects.create(
            product=self.product, batch_number='SHARED-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        # Same literal string, different model/table — must NOT raise.
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='SHARED-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        self.assertEqual(ProductBatch.objects.filter(batch_number='SHARED-001').count(), 1)
        self.assertEqual(IngredientBatch.objects.filter(batch_number='SHARED-001').count(), 1)


# ---------------------------------------------------------------------------
# Round 7: expired batches excluded from FEFO deduction
# ---------------------------------------------------------------------------

class ExpiredBatchExclusionTests(TestCase):
    """
    Covers: deduct_product_batch()/deduct_ingredient_batch() previously
    filtered only on status='available', never on expiration_date. Nothing
    in this app automatically flips status to 'expired' when a batch's date
    passes, so an expired-but-still-'available' batch was silently sellable
    at checkout forever, until a human manually filed a write-off adjustment.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.today = timezone.now().date()

    # --- ProductBatch ---

    def test_expired_product_batch_excluded_from_deduction(self):
        expired = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today - timedelta(days=1),  # expired yesterday
        )
        valid = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today + timedelta(days=10),
        )

        consumed = BatchService.deduct_product_batch(self.product, Decimal('20.00'))

        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, valid.pk)
        expired.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(expired.remaining_quantity, Decimal('50.00'))  # untouched
        self.assertEqual(valid.remaining_quantity, Decimal('30.00'))

    def test_deduction_fails_when_only_expired_stock_exists(self):
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=5),
        )
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('10.00'))

    def test_batch_expiring_today_is_still_sellable(self):
        """Boundary case: a batch dated exactly today hasn't expired yet — still fair game."""
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-004',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('20.00'), remaining_quantity=Decimal('20.00'),
            expiration_date=self.today,
        )
        consumed = BatchService.deduct_product_batch(self.product, Decimal('5.00'))
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, batch.pk)

    # --- IngredientBatch ---

    def test_expired_ingredient_batch_excluded_from_deduction(self):
        expired = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today - timedelta(days=1),
        )
        valid = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-002',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today + timedelta(days=10),
        )

        consumed = BatchService.deduct_ingredient_batch(self.ingredient, Decimal('20.00'))

        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, valid.pk)
        expired.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(expired.remaining_quantity, Decimal('50.00'))  # untouched
        self.assertEqual(valid.remaining_quantity, Decimal('30.00'))

    def test_ingredient_deduction_fails_when_only_expired_stock_exists(self):
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-003',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=5),
        )
        with self.assertRaises(ValueError):
            BatchService.deduct_ingredient_batch(self.ingredient, Decimal('10.00'))

# ---------------------------------------------------------------------------
# Round 8: status locked to audited transitions only, expiration_date must
# not precede date_received
# ---------------------------------------------------------------------------

class BatchStatusReadOnlyTests(TestCase):
    """
    Covers: status was directly PATCHable on both batch serializers, bypassing
    every audited transition. A valid choice like {"status": "available"}
    could silently un-dispose a batch that was written off for spoilage,
    re-entering it into the sellable FEFO pool with no adjustment record.
    Same treatment as quantity from the prior round: read-only, only
    mutable via create_stock_adjustment()/reconcile().
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = timezone.now().date() + timedelta(days=30)

    def test_product_batch_status_ignored_on_patch(self):
        from inventory.serializers import ProdBatchSerializer

        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-STATUS-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date, status='disposed',
        )
        serializer = ProdBatchSerializer(batch, data={'status': 'available'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'disposed')  # unchanged — patch silently ignored

    def test_ingredient_batch_status_ignored_on_patch(self):
        from inventory.serializers import IngBatchSerializer

        batch = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-STATUS-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date, status='depleted',
        )
        serializer = IngBatchSerializer(batch, data={'status': 'available'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'depleted')  # unchanged — patch silently ignored

    def test_new_product_batch_defaults_to_available_regardless_of_input(self):
        from inventory.serializers import ProdBatchSerializer

        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': self.future_date.isoformat(),
            'status': 'disposed',  # attempted spoof on create
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.status, 'available') # type: ignore

    def test_status_still_mutable_via_create_stock_adjustment(self):
        """Sanity check: read-only in the serializer, but the audited path still works."""
        user = make_user()
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-STATUS-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('10.00'),
            unit_cost=Decimal('10.00'), adjusted_by=user,
            product_batch=batch,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'disposed')


class BatchDateValidationTests(TestCase):
    """
    Covers: expiration_date wasn't checked against date_received on batch
    creation or update, so a batch could be logged as already expired the
    moment it was received.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.today = timezone.now().date()

    # --- ProductBatch: create ---

    def test_product_batch_rejects_expiration_before_explicit_date_received(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_rejects_expiration_before_default_date_received(self):
        """date_received omitted -> defaults to today; expiration in the past must still be rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_allows_expiration_equal_to_date_received(self):
        """Same-day expiration is a boundary-valid case, not rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': self.today.isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_product_batch_allows_future_expiration(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today + timedelta(days=30)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- ProductBatch: update ---

    def test_product_batch_update_rejects_new_expiration_before_existing_date_received(self):
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=30),
        )
        serializer = ProdBatchSerializer(
            batch, data={'expiration_date': (self.today - timedelta(days=1)).isoformat()}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_update_rejects_new_date_received_after_existing_expiration(self):
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=5),
        )
        serializer = ProdBatchSerializer(
            batch, data={'date_received': (self.today + timedelta(days=10)).isoformat()}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_update_unrelated_field_still_succeeds(self):
        """Regression guard: the new validate() shouldn't block ordinary patches
        that don't touch either date field."""
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=30),
        )
        serializer = ProdBatchSerializer(batch, data={'notes': 'restocked shelf'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- IngredientBatch: create ---

    def test_ingredient_batch_rejects_expiration_before_date_received(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_ingredient_batch_allows_future_expiration(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today + timedelta(days=10)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)