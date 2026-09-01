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
from rest_framework.test import APIClient
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from sales.services import SalesService

User = get_user_model()


class HiddenCategorySalesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = make_user('hiddenstaff')
        self.admin = make_user('hiddenadmin', role='admin')
        self.category = Category.objects.create(
            name='Admin Only', is_visible_to_staff=False
        )
        self.product = Product.objects.create(
            category=self.category, name='Hidden Product', unit='piece',
            unit_price=Decimal('10.00'), shelf_life=7,
        )
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-HIDDEN-SALE',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('20.00'),
            remaining_quantity=Decimal('20.00'), expiration_date='2026-12-31',
        )
        self.customer = Customer.objects.create(
            name='Test Customer', created_by=self.staff
        )

    def test_staff_cannot_checkout_hidden_product_by_known_id(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not found', response.data['error'])

    def test_staff_cannot_order_hidden_product_by_known_id(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not found', response.data['error'])

    def test_admin_can_checkout_hidden_product(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)


class TransactionCustomerHistoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = make_user('customerhistorystaff')
        self.category = Category.objects.create(name='Customer History')
        self.product = Product.objects.create(
            category=self.category, name='History Product', unit='piece',
            unit_price=Decimal('10.00'), shelf_life=7,
        )
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-CUSTOMER-HISTORY',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('10.00'),
            remaining_quantity=Decimal('10.00'), expiration_date='2026-12-31',
        )
        self.customer = Customer.objects.create(
            name='Juan Dela Cruz', contact_number='09171234567',
            created_by=self.staff,
        )
        self.client.force_authenticate(user=self.staff)

    def test_checkout_customer_is_returned_in_sales_history(self):
        checkout = self.client.post('/sales/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        self.assertEqual(checkout.data['customer']['name'], 'Juan Dela Cruz')

        history = self.client.get('/sales/transactions/')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data[0]['customer']['name'], 'Juan Dela Cruz')

    def test_checkout_without_customer_remains_walk_in_compatible(self):
        checkout = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        self.assertIsNone(checkout.data['customer'])

    def test_order_transaction_carries_the_order_customer(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction']['customer']['name'], 'Juan Dela Cruz')


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user(  # pyright: ignore[reportAttributeAccessIssue]
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


class CheckoutPaymentMethodValidationTests(TestCase):
    """Covers: checkout() used to accept any string as payment_method with
    zero validation — a bad value would silently skip the amount_tendered
    requirement (only the 'cash' branch enforces it), and Transaction.save()
    doesn't validate choices= at the ORM level, so garbage was written
    straight into the DB."""

    def setUp(self):
        self.staff = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-970',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def _cart(self, qty=Decimal('2.00')):
        return [(self.product, qty)]

    def test_invalid_payment_method_rejected(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='bitcoin',
                amount_tendered=Decimal('100.00'),
            )

    def test_invalid_payment_method_rejected_before_stock_is_deducted(self):
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='bitcoin',
                amount_tendered=Decimal('100.00'),
            )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # untouched, rolled back

    def test_invalid_payment_method_rejected_even_without_amount_tendered(self):
        """The old bug: a bad payment_method wasn't 'cash', so it skipped the
        amount_tendered requirement entirely and could succeed with no tender."""
        with self.assertRaises(ValueError):
            SalesService.checkout(
                self._cart(), self.staff, payment_method='bitcoin',
                amount_tendered=None,
            )

    def test_valid_cash_still_works(self):
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='cash',
            amount_tendered=Decimal('100.00'),
        )
        self.assertEqual(txn.payment_method, 'cash')

    def test_valid_online_still_works(self):
        txn = SalesService.checkout(
            self._cart(), self.staff, payment_method='online',
        )
        self.assertEqual(txn.payment_method, 'online')

    def test_checkout_view_returns_400_not_500_for_invalid_payment_method(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '2.00'}],
            'payment_method': 'bitcoin',
            'amount_tendered': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_order_creation_returns_400_not_500_for_invalid_payment_method(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        response = client.post('/sales/orders/', {
            'customer_id': customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'payment_method': 'bitcoin',
            'amount_tendered': '50.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)  # rolled back, no orphan order


class VoidFulfilledOrderTests(TestCase):
    """Voiding a fulfilled order — restoring stock via a locked, fresh re-fetch
    instead of a stale FK read. Every order is fulfilled the instant it's
    created now, so this is just 'cancel an order', full stop."""

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

    def _place_order(self, qty=Decimal('10.00')):
        return SalesService.place_order(
            customer=self.customer, items=[(self.product, qty)], handled_by=self.staff,
            amount_tendered=qty * self.product.unit_price,
        )

    def test_void_restores_stock_to_correct_batch(self):
        order = self._place_order(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))
        SalesService.void_fulfilled_order(order, self.admin)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))

    def test_void_uses_current_db_state_not_stale_batch_data(self):
        order = self._place_order(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

        from inventory.services.batch_service import BatchService
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('5.00'),
            unit_cost=Decimal('50.00'), adjusted_by=self.admin,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('85.00'))

        SalesService.void_fulfilled_order(order, self.admin)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))

    def test_voiding_already_voided_transaction_rejected(self):
        order = self._place_order()
        SalesService.void_fulfilled_order(order, self.admin)
        with self.assertRaises(ValueError):
            SalesService.void_fulfilled_order(order, self.admin)

    def test_void_marks_order_cancelled(self):
        order = self._place_order()
        SalesService.void_fulfilled_order(order, self.admin)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_void_skips_expired_or_disposed_batches(self):
        order = self._place_order(qty=Decimal('10.00'))
        self.batch.refresh_from_db()
        self.batch.status = 'disposed'
        self.batch.save()
        txn, skipped = SalesService.void_fulfilled_order(order, self.admin)
        self.assertIn(self.batch.batch_number, skipped)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))


class BestSellersReportTests(TestCase):
    """Covers: fix #8 — bad `limit` query param crashing the view."""

    def setUp(self):
        self.client: APIClient = APIClient()
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
        self.client: APIClient = APIClient()
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


class PlaceOrderTests(TestCase):
    """Covers the rebuilt order flow: POST /sales/orders/ creates the Order,
    its OrderItems, and fulfills it in one atomic call."""

    def setUp(self):
        self.client: APIClient = APIClient()
        self.staff = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-960',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        self.client.force_authenticate(user=self.staff)

    def test_create_order_immediately_fulfills_and_deducts_stock(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '5.00'}],
            'amount_tendered': '250.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'fulfilled')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))

    def test_response_includes_nested_transaction_with_change_due(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '2.00'}],
            'amount_tendered': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['transaction'])
        self.assertEqual(response.data['transaction']['change_due'], '50.00')

    def test_discount_applies_correctly_on_creation(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '2.00'}],
            'discount_type': 'percent',
            'discount_value': '10',
            'amount_tendered': '90.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction']['discount_amount'], '10.00')
        self.assertEqual(response.data['transaction']['total_amount'], '90.00')

    def test_invalid_percent_discount_rejected(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'discount_type': 'percent',
            'discount_value': '150',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_missing_items_rejected(self):
        response = self.client.post('/sales/orders/', {'customer_id': self.customer.pk, 'items': []}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_missing_customer_id_rejected(self):
        response = self.client.post('/sales/orders/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_bad_product_id_in_items_gives_specific_index_error(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [
                {'product_id': self.product.pk, 'quantity': '1.00'},
                {'product_id': 999999, 'quantity': '1.00'},
            ],
            'amount_tendered': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('index 1', response.data['error'])

    def test_zero_quantity_item_rejected(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '0.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_insufficient_stock_rolls_back_entire_order(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '9999.00'}],
            'amount_tendered': '999999.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_insufficient_cash_tendered_rolls_back(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '5.00'}],
            'amount_tendered': '10.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))


class OrderMutationRemovedTests(TestCase):
    """PATCH/PUT/DELETE are all gone — orders are immutable once created
    ('cancel' is the only sanctioned way to end one)."""

    def setUp(self):
        self.client: APIClient = APIClient()
        self.staff = make_user()
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-961',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date='2026-12-31',
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'amount_tendered': '50.00',
        }, format='json')
        self.order_id = response.data['id']

    def test_patch_returns_405(self):
        response = self.client.patch(f'/sales/orders/{self.order_id}/', {'discount_value': '5.00'}, format='json')
        self.assertEqual(response.status_code, 405)

    def test_delete_returns_405(self):
        response = self.client.delete(f'/sales/orders/{self.order_id}/')
        self.assertEqual(response.status_code, 405)


class CancelOrderPermissionTests(TestCase):
    """Cancelling voids a real completed sale — admin only. No staff exception,
    since there's no more 'still just placed' state for staff to back out of."""

    def setUp(self):
        self.client: APIClient = APIClient()
        self.staff = make_user()
        self.admin = make_user(username='adminuser', role='admin')
        self.customer = Customer.objects.create(name='Walk-in', created_by=self.staff)
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-962',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date='2026-12-31',
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00'}],
            'amount_tendered': '50.00',
        }, format='json')
        self.order_id = response.data['id']

    def test_staff_cannot_cancel(self):
        response = self.client.post(f'/sales/orders/{self.order_id}/cancel/')
        self.assertEqual(response.status_code, 403)

    def test_admin_can_cancel(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f'/sales/orders/{self.order_id}/cancel/')
        self.assertEqual(response.status_code, 200)
