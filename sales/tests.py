"""
Tests for sales app — covers the bug fixes made in the Round 3 audit session.

Run with: python manage.py test sales

NOTE on concurrency: see the same caveat in inventory/tests.py — these tests
verify the logic each fix depends on (correct validation, correct math,
correct rejection of bad states, correct locking-adjacent behavior against a
single connection) rather than true concurrent-request races, which would
need TransactionTestCase + threading + Postgres.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from sales.services import SalesService

User = get_user_model()


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user(
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


class CheckoutDiscountTests(TestCase):
    """Covers: fix #5 (negative fixed discount) and fix #6 (invalid discount_type)."""

    def setUp(self):
        self.staff = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-901',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def _cart(self, qty=Decimal('2.00')):
        return [(self.product, qty)]

    def test_percent_discount_applies_correctly(self):
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='cash',
            discount_type='percent', discount_value=Decimal('10'),
            amount_tendered=Decimal('100.00'),
        )
        self.assertEqual(txn.subtotal, Decimal('100.00'))
        self.assertEqual(txn.discount_amount, Decimal('10.00'))
        self.assertEqual(txn.total_amount, Decimal('90.00'))

    def test_percent_discount_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                discount_type='percent', discount_value=Decimal('150'),
                amount_tendered=Decimal('100.00'),
            )

    def test_fixed_discount_applies_correctly(self):
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='cash',
            discount_type='fixed', discount_value=Decimal('20.00'),
            amount_tendered=Decimal('100.00'),
        )
        self.assertEqual(txn.discount_amount, Decimal('20.00'))
        self.assertEqual(txn.total_amount, Decimal('80.00'))

    def test_fixed_discount_exceeding_subtotal_rejected(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                discount_type='fixed', discount_value=Decimal('200.00'),
                amount_tendered=Decimal('200.00'),
            )

    def test_negative_fixed_discount_rejected(self):
        """Fix #5: a negative fixed discount used to inflate the total instead of erroring."""
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                discount_type='fixed', discount_value=Decimal('-50.00'),
                amount_tendered=Decimal('100.00'),
            )

    def test_invalid_discount_type_rejected(self):
        """Fix #6: an unrecognized discount_type used to silently apply zero discount."""
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                discount_type='banana', discount_value=Decimal('10.00'),
                amount_tendered=Decimal('100.00'),
            )

    def test_none_discount_type_applies_zero_discount_intentionally(self):
        """The 'none' branch is a deliberate zero-discount path, distinct from invalid types."""
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='cash',
            discount_type='none', discount_value=Decimal('0.00'),
            amount_tendered=Decimal('100.00'),
        )
        self.assertEqual(txn.discount_amount, Decimal('0.00'))
        self.assertEqual(txn.total_amount, Decimal('100.00'))

    def test_cash_payment_requires_amount_tendered(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                amount_tendered=None,
            )

    def test_cash_payment_insufficient_tender_rejected(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='cash',
                amount_tendered=Decimal('10.00'),  # subtotal is 100
            )

    def test_change_due_computed_correctly(self):
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='cash',
            amount_tendered=Decimal('150.00'),
        )
        self.assertEqual(txn.change_due, Decimal('50.00'))


class VoidFulfilledOrderTests(TestCase):
    """Covers: fix #3 — void restoring stock via a locked, fresh re-fetch instead of a stale FK read."""

    def setUp(self):
        self.staff = make_user()
        self.admin = make_user(username='adminuser', role='admin')
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-902',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)

    def _place_confirm_fulfill(self, qty=Decimal('10.00')):
        order = Order.objects.create(customer=self.customer, handled_by=self.staff)
        OrderItem.objects.create(
            order=order, product=self.product, quantity=qty,
            unit_price=self.product.unit_price, subtotal=qty * self.product.unit_price,
        )
        order.status = 'confirmed'
        order.save()
        SalesService.fulfill_order(
            order, self.staff, payment_method='cash',
            amount_tendered=qty * self.product.unit_price,  # exact amount, no change due
        )
        order.refresh_from_db()
        return order

    def test_void_restores_stock_to_correct_batch(self):
        order = self._place_confirm_fulfill(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))  # 100 - 10 sold

        SalesService.void_fulfilled_order(order, self.admin)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # restored

    def test_void_uses_current_db_state_not_stale_batch_data(self):
        """
        Simulates the race scenario the fix addresses: after fulfillment, a
        separate stock adjustment reduces the batch further before the void
        is processed. The void must add its restored quantity on top of the
        CURRENT db value, not clobber it with a stale read.
        """
        order = self._place_confirm_fulfill(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

        # Simulate a concurrent spoilage adjustment happening between fulfillment and void
        from inventory.services.batch_service import BatchService
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('5.00'),
            unit_cost=Decimal('50.00'), adjusted_by=self.admin,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('85.00'))  # 90 - 5

        SalesService.void_fulfilled_order(order, self.admin)
        self.batch.refresh_from_db()
        # Must be 85 + 10 = 95, NOT 90 + 10 = 100 (which would silently erase the spoilage)
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))

    def test_voiding_already_voided_transaction_rejected(self):
        order = self._place_confirm_fulfill()
        SalesService.void_fulfilled_order(order, self.admin)
        with self.assertRaises(ValueError):
            SalesService.void_fulfilled_order(order, self.admin)

    def test_void_marks_order_cancelled(self):
        order = self._place_confirm_fulfill()
        SalesService.void_fulfilled_order(order, self.admin)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_void_skips_expired_or_disposed_batches(self):
        order = self._place_confirm_fulfill(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.batch.status = 'disposed'
        self.batch.save()

        txn, skipped = SalesService.void_fulfilled_order(order, self.admin)
        self.assertIn(self.batch.batch_number, skipped)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))  # not restored


class OrderItemsActionTests(TestCase):
    """Covers: fix #9 — the new POST /sales/orders/<id>/items/ action."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.staff = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        self.order = Order.objects.create(customer=self.customer, handled_by=self.staff)
        self.client.force_authenticate(user=self.staff)

    def test_add_item_to_placed_order_succeeds(self):
        response = self.client.post(
            f'/sales/orders/{self.order.pk}/items/',
            {'product_id': self.product.pk, 'quantity': '3.00'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.order.refresh_from_db()
        self.assertEqual(self.order.items.count(), 1)
        item = self.order.items.first()
        self.assertEqual(item.unit_price, self.product.unit_price)  # snapshotted correctly
        self.assertEqual(item.subtotal, Decimal('150.00'))

    def test_add_item_to_confirmed_order_rejected(self):
        self.order.status = 'confirmed'
        self.order.save()
        response = self.client.post(
            f'/sales/orders/{self.order.pk}/items/',
            {'product_id': self.product.pk, 'quantity': '3.00'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.items.count(), 0)

    def test_add_item_to_cancelled_order_rejected(self):
        self.order.status = 'cancelled'
        self.order.save()
        response = self.client.post(
            f'/sales/orders/{self.order.pk}/items/',
            {'product_id': self.product.pk, 'quantity': '3.00'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_fulfilling_order_with_added_items_deducts_correct_stock(self):
        """End-to-end: item added via the new action actually gets consumed on fulfillment."""
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-903',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date='2026-12-31',
        )
        self.client.post(
            f'/sales/orders/{self.order.pk}/items/',
            {'product_id': self.product.pk, 'quantity': '5.00'},
            format='json',
        )
        self.order.status = 'confirmed'
        self.order.save()
        SalesService.fulfill_order(
            self.order, self.staff, payment_method='cash',
            amount_tendered=Decimal('250.00'),  # 5 units x 50.00
        )

        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, Decimal('45.00'))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'fulfilled')


class BestSellersReportTests(TestCase):
    """Covers: fix #8 — bad `limit` query param crashing the view."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.admin = make_user(username='adminuser', role='admin')
        self.client.force_authenticate(user=self.admin)

    def test_non_integer_limit_returns_400_not_500(self):
        response = self.client.get('/sales/reports/best-sellers/?limit=abc')
        self.assertEqual(response.status_code, 400)

    def test_negative_limit_returns_400(self):
        response = self.client.get('/sales/reports/best-sellers/?limit=-5')
        self.assertEqual(response.status_code, 400)

    def test_zero_limit_returns_400(self):
        response = self.client.get('/sales/reports/best-sellers/?limit=0')
        self.assertEqual(response.status_code, 400)

    def test_valid_limit_returns_200(self):
        response = self.client.get('/sales/reports/best-sellers/?limit=5')
        self.assertEqual(response.status_code, 200)

    def test_default_limit_used_when_absent(self):
        response = self.client.get('/sales/reports/best-sellers/')
        self.assertEqual(response.status_code, 200)

class TransactionHistoryTests(TestCase):
    """Covers: new GET /sales/transactions/ and /sales/transactions/<id>/ endpoints."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.staff = make_user(username='staffuser')
        self.other_staff = make_user(username='otherstaff')
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-950',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('1000.00'),
            remaining_quantity=Decimal('1000.00'),
            expiration_date='2026-12-31',
        )
        self.client.force_authenticate(user=self.staff)

    def _make_txn(self, staff=None, qty=Decimal('2.00'), payment_method='cash', tendered=Decimal('100.00')):
        staff = staff or self.staff
        return SalesService.checkout(
            [(self.product, qty)], staff, payment_method=payment_method,
            amount_tendered=tendered if payment_method == 'cash' else None,
        )

    # --- basic list/retrieve ---

    def test_list_returns_created_transactions(self):
        self._make_txn()
        self._make_txn()
        response = self.client.get('/sales/transactions/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_retrieve_single_transaction(self):
        txn = self._make_txn()
        response = self.client.get(f'/sales/transactions/{txn.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], txn.pk)

    def test_unauthenticated_request_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/sales/transactions/')
        self.assertIn(response.status_code, (401, 403))

    # --- voided default-hidden behavior ---

    def test_voided_transaction_hidden_by_default(self):
        txn = self._make_txn()
        txn.is_voided = True
        txn.save()
        response = self.client.get('/sales/transactions/')
        self.assertEqual(len(response.data), 0)

    def test_include_voided_true_shows_voided_transaction(self):
        txn = self._make_txn()
        txn.is_voided = True
        txn.save()
        response = self.client.get('/sales/transactions/?include_voided=true')
        self.assertEqual(len(response.data), 1)

    # --- payment_method filter ---

    def test_payment_method_filter_valid(self):
        self._make_txn(payment_method='cash', tendered=Decimal('100.00'))
        self._make_txn(payment_method='online')
        response = self.client.get('/sales/transactions/?payment_method=online')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['payment_method'], 'online')

    def test_payment_method_filter_invalid_rejected(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?payment_method=bitcoin')
        self.assertEqual(response.status_code, 400)

    # --- handled_by filter ---

    def test_handled_by_filter_valid(self):
        self._make_txn(staff=self.staff)
        self._make_txn(staff=self.other_staff)
        response = self.client.get(f'/sales/transactions/?handled_by={self.other_staff.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_handled_by_filter_non_numeric_rejected(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?handled_by=notanumber')
        self.assertEqual(response.status_code, 400)

    # --- date range filter ---

    def test_date_range_bad_format_rejected(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?start_date=07-17-2026')
        self.assertEqual(response.status_code, 400)

    def test_date_range_inverted_rejected(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?start_date=2026-12-31&end_date=2026-01-01')
        self.assertEqual(response.status_code, 400)

    def test_date_range_excludes_out_of_range_transactions(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?start_date=2020-01-01&end_date=2020-01-02')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_date_range_includes_today(self):
        self._make_txn()
        from django.utils import timezone
        today = timezone.now().date().isoformat()
        response = self.client.get(f'/sales/transactions/?start_date={today}&end_date={today}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

class OrderDiscountLockTests(TestCase):
    """Covers: discount_type/discount_value are create-only, immutable after order creation."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.staff = make_user()
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        self.client.force_authenticate(user=self.staff)

    def test_discount_set_at_creation_persists(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'discount_type': 'percent',
            'discount_value': '10.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(pk=response.data['id'])
        self.assertEqual(order.discount_type, 'percent')
        self.assertEqual(order.discount_value, Decimal('10.00'))

    def test_invalid_percent_discount_rejected_at_creation(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'discount_type': 'percent',
            'discount_value': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_negative_fixed_discount_rejected_at_creation(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'discount_type': 'fixed',
            'discount_value': '-5.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_patch_cannot_change_discount_after_creation(self):
        order = Order.objects.create(
            customer=self.customer, handled_by=self.staff,
            discount_type='percent', discount_value=Decimal('10.00'),
        )
        response = self.client.patch(f'/sales/orders/{order.pk}/', {
            'discount_type': 'fixed',
            'discount_value': '999.00',
        }, format='json')
        self.assertEqual(response.status_code, 200)  # silently ignored, not rejected
        order.refresh_from_db()
        self.assertEqual(order.discount_type, 'percent')  # unchanged
        self.assertEqual(order.discount_value, Decimal('10.00'))  # unchanged