"""Sales tests: checkout, orders, item pricing, encoding, payments, history and imports.

Run all app tests: python manage.py test sales
Run one class: python manage.py test sales.tests.<TestClass>
PostgreSQL is required for the concurrency and database-locking checks.
"""

import gzip
import io
import json
import subprocess
import sys
import time as clock_time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from accounts.models import Users
from config.data_cache import data_version
from forecasting.data import load_snapshot
from inventory.models import Category, FEFOConf, Product, ProductBatch
from inventory.serializers import ProdBatchSerializer
from sales.importers.annual_sales import (
    ParseResult,
    SourceLine,
    SourceTicket,
    canonical_customer,
    parse_workbooks,
    source_date,
)
from sales.importers.load_annual_sales import load, merge_imported_customers
from sales.management.commands.populate_2026_sales import (
    backup_database,
    build_plan,
    insert_plan,
    summary,
    target_date,
)
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from sales.serializers import TransactionSerializer
from sales.serializers.order_serializer import OrderListTransactionSerializer
from sales.services import SalesService
from sales.views.transaction_views import TransactionViewSet


User = get_user_model()


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user(  # pyright: ignore[reportAttributeAccessIssue]
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


# Core application behavior
# ----------------------------------------------------------------------------

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
            shelf_life=7,
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
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not found', response.data['error'])

    def test_staff_cannot_order_hidden_product_by_known_id(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not found', response.data['error'])

    def test_staff_cannot_sell_product_in_inactive_category(self):
        self.category.is_visible_to_staff = True
        self.category.is_active = False
        self.category.save(update_fields=['is_visible_to_staff', 'is_active'])
        self.client.force_authenticate(user=self.staff)
        checkout = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 400)
        order = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(order.status_code, 400)

    def test_staff_history_hides_sale_after_category_is_deactivated(self):
        self.category.is_visible_to_staff = True
        self.category.save(update_fields=['is_visible_to_staff'])
        self.client.force_authenticate(user=self.admin)
        order = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(order.status_code, 201)
        self.category.is_active = False
        self.category.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 0)
        self.assertEqual(self.client.get('/sales/orders/').data['results'], [])

    def test_admin_can_checkout_hidden_product(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)

    def test_staff_sales_history_does_not_expose_hidden_products(self):
        self.client.force_authenticate(user=self.admin)
        checkout = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        order = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(order.status_code, 201)

        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 0)
        self.assertEqual(self.client.get(f"/sales/transactions/{checkout.data['id']}/").status_code, 404)
        self.assertEqual(len(self.client.get('/sales/orders/').data['results']), 0)
        self.assertEqual(self.client.get(f"/sales/orders/{order.data['id']}/").status_code, 404)

        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 2)
        self.assertEqual(len(self.client.get('/sales/orders/').data['results']), 1)


class TransactionCustomerHistoryTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.staff = make_user('customerhistorystaff')
        self.category = Category.objects.create(name='Customer History')
        self.product = Product.objects.create(
            category=self.category, name='History Product', unit='piece',
            shelf_life=7,
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
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        self.assertEqual(checkout.data['customer']['name'], 'Juan Dela Cruz')

        history = self.client.get('/sales/transactions/')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data['results'][0]['customer']['name'], 'Juan Dela Cruz')

    def test_checkout_without_customer_remains_walk_in_compatible(self):
        checkout = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        self.assertIsNone(checkout.data['customer'])

    def test_order_transaction_carries_the_order_customer(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction']['customer']['name'], 'Juan Dela Cruz')

    def test_customer_summary_counts_direct_checkout_and_order_once_each(self):
        checkout = self.client.post('/sales/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        order = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(order.status_code, 201)

        response = self.client.get('/sales/customers/')
        self.assertEqual(response.status_code, 200)
        summary = next(row for row in response.data if row['id'] == self.customer.pk)
        self.assertEqual(summary['transaction_count'], 2)
        self.assertIsNotNone(summary['last_sale'])

        Transaction.objects.filter(pk=checkout.data['id']).update(is_voided=True)
        response = self.client.get(f'/sales/customers/{self.customer.pk}/')
        self.assertEqual(response.data['transaction_count'], 1)

    def test_customer_with_no_sale_has_empty_summary(self):
        response = self.client.get(f'/sales/customers/{self.customer.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['transaction_count'], 0)
        self.assertIsNone(response.data['last_sale'])

    def test_new_customer_response_has_same_summary_contract(self):
        response = self.client.post('/sales/customers/', {
            'name': 'New Customer', 'contact_number': '09170000000',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction_count'], 0)
        self.assertIsNone(response.data['last_sale'])

    def test_transaction_history_exposes_sale_time_product_details(self):
        checkout = self.client.post('/sales/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        self.product.name = 'Renamed Product'
        self.product.save(update_fields=['name'])
        self.category.name = 'Renamed Category'
        self.category.save(update_fields=['name'])

        history = self.client.get(f"/sales/transactions/{checkout.data['id']}/")
        self.assertEqual(history.status_code, 200)
        item = history.data['items'][0]
        self.assertEqual(item['product_name_snapshot'], 'History Product')
        self.assertEqual(item['category_name_snapshot'], 'Customer History')

    def test_customer_with_direct_checkout_is_deactivated_without_losing_history(self):
        checkout = self.client.post('/sales/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 201)
        admin = make_user('customerdeleteadmin', role='admin')
        self.client.force_authenticate(user=admin)
        response = self.client.delete(f'/sales/customers/{self.customer.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['deletion_type'], 'deactivated')
        self.assertFalse(Customer.objects.get(pk=self.customer.pk).is_active)
        self.assertEqual(self.client.get('/sales/customers/').data, [])
        history = self.client.get(f"/sales/transactions/{checkout.data['id']}/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data['customer']['name'], self.customer.name)
        self.assertEqual(self.client.post(f'/sales/customers/{self.customer.pk}/reactivate/').status_code, 200)
        self.assertTrue(Customer.objects.get(pk=self.customer.pk).is_active)

    def test_unused_customer_is_permanently_deleted(self):
        admin = make_user('unusedcustomeradmin', role='admin')
        self.client.force_authenticate(user=admin)
        response = self.client.delete(f'/sales/customers/{self.customer.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['deletion_type'], 'permanent')
        self.assertFalse(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_deactivated_customer_cannot_be_used_for_new_sales(self):
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active'])
        checkout = self.client.post('/sales/checkout/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '20.00',
        }, format='json')
        self.assertEqual(checkout.status_code, 400)
        order = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
        }, format='json')
        self.assertEqual(order.status_code, 400)


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
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-970',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date='2026-12-31',
        )

    def _cart(self, qty=Decimal('2.00')):
        return [(self.product, qty, Decimal('50.00'))]

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
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50.00'}],
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
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'}],
            'payment_method': 'bitcoin',
            'amount_tendered': '50.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)  # rolled back, no orphan order

    def test_checkout_rejects_non_object_items_without_500(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.post(
            '/sales/checkout/', {'items': ['not-an-object']}, format='json'
        )
        self.assertEqual(response.status_code, 400)

    def test_checkout_rejects_nan_and_bad_product_id_without_500(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        payloads = [
            {'items': [{'product_id': self.product.pk, 'quantity': 'NaN'}]},
            {'items': [{'product_id': {}, 'quantity': '1.00'}]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post('/sales/checkout/', payload, format='json')
                self.assertEqual(response.status_code, 400)


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
            shelf_life=7,
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
            customer=self.customer, items=[(self.product, qty, Decimal('50.00'))], handled_by=self.staff,
            amount_tendered=(qty * Decimal('50.00')).quantize(Decimal('0.01')),
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


class SalesByCategoryReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user(username='categoryadmin', role='admin')
        self.category = Category.objects.create(name='Category Report')
        product = Product.objects.create(
            category=self.category, name='Report Milk', unit='liter',
            shelf_life=7,
        )
        batch = ProductBatch.objects.create(
            product=product, batch_number='PRD-CATEGORY-REPORT',
            unit_price=Decimal('50.00'), initial_quantity=Decimal('10.00'),
            remaining_quantity=Decimal('10.00'), expiration_date='2026-12-31',
        )
        transaction = Transaction.objects.create(
            handled_by=self.admin, subtotal=Decimal('50.00'),
            total_amount=Decimal('50.00'),
        )
        TransactionItem.objects.create(
            transaction=transaction, product_batch=batch,
            quantity=Decimal('1.00'), unit_price=Decimal('50.00'),
        )
        self.client.force_authenticate(user=self.admin)

    def test_category_data_queryset_is_evaluated_once(self):
        with self.assertNumQueries(1):
            response = self.client.get('/sales/reports/sales-by-category/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['name'], self.category.name)


class TransactionHistoryTests(TestCase):
    """Covers: new GET /sales/transactions/ and /sales/transactions/<id>/ endpoints."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client: APIClient = APIClient()
        self.staff = make_user(username='staffuser')
        self.other_staff = make_user(username='otherstaff')
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
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
            [(self.product, qty, Decimal('50.00'))], staff, payment_method=payment_method,
            amount_tendered=tendered if payment_method == 'cash' else None,
        )

    # --- basic list/retrieve ---

    def test_list_returns_created_transactions(self):
        self._make_txn()
        self._make_txn()
        response = self.client.get('/sales/transactions/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 2)

    def test_retrieve_single_transaction(self):
        txn = self._make_txn()
        response = self.client.get(f'/sales/transactions/{txn.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], txn.pk)
        self.assertFalse(response.data['is_voided'])

    def test_list_is_paginated_at_fifty_transactions(self):
        Transaction.objects.bulk_create([
            Transaction(
                handled_by=self.staff, subtotal=Decimal('1.00'),
                total_amount=Decimal('1.00'),
            )
            for _ in range(51)
        ])
        response = self.client.get('/sales/transactions/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 51)
        self.assertEqual(len(response.data['results']), 50)
        self.assertIsNotNone(response.data['next'])

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
        self.assertEqual(response.data['count'], 0)

    def test_include_voided_true_shows_voided_transaction(self):
        txn = self._make_txn()
        txn.is_voided = True
        txn.save()
        response = self.client.get('/sales/transactions/?include_voided=true')
        self.assertEqual(response.data['count'], 1)
        self.assertTrue(response.data['results'][0]['is_voided'])

    # --- payment_method filter ---

    def test_payment_method_filter_valid(self):
        self._make_txn(payment_method='cash', tendered=Decimal('100.00'))
        self._make_txn(payment_method='online')
        response = self.client.get('/sales/transactions/?payment_method=online')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['payment_method'], 'online')

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
        self.assertEqual(response.data['count'], 1)

    def test_handled_by_filter_non_numeric_rejected(self):
        self._make_txn()
        response = self.client.get('/sales/transactions/?handled_by=notanumber')
        self.assertEqual(response.status_code, 400)

    def test_customer_id_filter(self):
        first = Customer.objects.create(name='First', created_by=self.staff)
        second = Customer.objects.create(name='Second', created_by=self.staff)
        Transaction.objects.create(
            handled_by=self.staff, customer=first,
            subtotal=Decimal('10.00'), total_amount=Decimal('10.00'),
        )
        Transaction.objects.create(
            handled_by=self.staff, customer=second,
            subtotal=Decimal('20.00'), total_amount=Decimal('20.00'),
        )
        response = self.client.get(f'/sales/transactions/?customer_id={second.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['customer']['id'], second.pk)

    def test_customer_id_filter_rejects_non_numeric_value(self):
        response = self.client.get('/sales/transactions/?customer_id=notanumber')
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
        self.assertEqual(response.data['count'], 0)

    def test_date_range_includes_today(self):
        self._make_txn()
        from django.utils import timezone
        today = timezone.localdate().isoformat()
        response = self.client.get(f'/sales/transactions/?start_date={today}&end_date={today}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)


class PlaceOrderTests(TestCase):
    """Covers the rebuilt order flow: POST /sales/orders/ creates the Order,
    its OrderItems, and fulfills it in one atomic call."""

    def setUp(self):
        self.client: APIClient = APIClient()
        self.staff = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
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
            'items': [{'product_id': self.product.pk, 'quantity': '5.00', 'unit_price': '50.00'}],
            'amount_tendered': '250.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'fulfilled')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))

    def test_response_includes_nested_transaction_with_change_due(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50.00'}],
            'amount_tendered': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['transaction'])
        self.assertEqual(response.data['transaction']['change_due'], '50.00')

    def test_discount_applies_correctly_on_creation(self):
        self.client.force_authenticate(make_user('discountadmin', role='admin'))
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50.00', 'discount_type': 'percent', 'discount_value': '10'}],
            'amount_tendered': '90.00',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['transaction']['discount_amount'], '10.00')
        self.assertEqual(response.data['transaction']['total_amount'], '90.00')

    def test_invalid_percent_discount_rejected(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00', 'discount_type': 'percent', 'discount_value': '150'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_missing_items_rejected(self):
        response = self.client.post('/sales/orders/', {'customer_id': self.customer.pk, 'items': []}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_missing_customer_id_rejected(self):
        response = self.client.post('/sales/orders/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_bad_product_id_in_items_gives_specific_index_error(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [
                {'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'},
                {'product_id': 999999, 'quantity': '1.00', 'unit_price': '50.00'},
            ],
            'amount_tendered': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('index 1', response.data['error'])

    def test_zero_quantity_item_rejected(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '0.00', 'unit_price': '50.00'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_insufficient_stock_rolls_back_entire_order(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '9999.00', 'unit_price': '50.00'}],
            'amount_tendered': '999999.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_insufficient_cash_tendered_rolls_back(self):
        response = self.client.post('/sales/orders/', {
            'customer_id': self.customer.pk,
            'items': [{'product_id': self.product.pk, 'quantity': '5.00', 'unit_price': '50.00'}],
            'amount_tendered': '10.00',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))

    def test_order_list_filters_by_customer_id(self):
        other_customer = Customer.objects.create(name='Other', created_by=self.staff)
        matching = Order.objects.create(customer=self.customer, handled_by=self.staff)
        Order.objects.create(customer=other_customer, handled_by=self.staff)

        response = self.client.get(f'/sales/orders/?customer_id={self.customer.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data['results']], [matching.pk])

    def test_order_customer_id_filter_rejects_non_numeric_value(self):
        response = self.client.get('/sales/orders/?customer_id=notanumber')
        self.assertEqual(response.status_code, 400)


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
            shelf_life=7,
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
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'}],
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
            shelf_life=7,
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
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'}],
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
        self.assertEqual(response.data['warning'], '')
        self.assertEqual(Order.objects.get(pk=self.order_id).warning, '')
        listed = self.client.get('/sales/orders/').data['results']
        self.assertEqual(listed[0]['warning'], '')

    def test_cancellation_warning_survives_list_and_detail_reload(self):
        batch = ProductBatch.objects.get(product=self.product)
        self.client.force_authenticate(user=self.admin)
        for reason in ('expired', 'disposed', 'past_expiration_date'):
            with self.subTest(reason=reason):
                batch.refresh_from_db()
                batch.status = 'available'
                batch.expiration_date = timezone.localdate() + timedelta(days=30)
                batch.save()
                order = SalesService.place_order(
                    self.customer, [(self.product, Decimal('1.00'), Decimal('50.00'))], self.staff,
                    amount_tendered=Decimal('50.00'),
                )
                if reason == 'past_expiration_date':
                    batch.expiration_date = timezone.localdate() - timedelta(days=1)
                else:
                    batch.status = reason
                # Update only the expiration/status so the stock sold stays deducted.
                batch.save(update_fields=['status', 'expiration_date'])
                remaining = ProductBatch.objects.get(pk=batch.pk).remaining_quantity

                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(f'/sales/orders/{order.pk}/cancel/')

                self.assertEqual(response.status_code, 200)
                warning = response.data['warning']
                self.assertIn(batch.batch_number, warning)
                order.refresh_from_db()
                self.assertEqual(order.warning, warning)
                self.assertEqual(order.status, 'cancelled')
                batch.refresh_from_db()
                self.assertEqual(batch.remaining_quantity, remaining)
                detail = self.client.get(f'/sales/orders/{order.pk}/')
                self.assertEqual(detail.data['warning'], warning)
                listed = self.client.get('/sales/orders/').data['results']
                self.assertEqual(next(o for o in listed if o['id'] == order.pk)['warning'], warning)
                repeated = self.client.post(f'/sales/orders/{order.pk}/cancel/')
                self.assertEqual(repeated.status_code, 400)
                order.refresh_from_db()
                self.assertEqual(order.warning, warning)


class SalesAuditRegressionTests(TestCase):
    def setUp(self):
        self.staff = make_user('salesauditstaff')
        self.admin = make_user('salesauditadmin', role='admin')
        self.category = Category.objects.create(name='Audit Category')
        self.product = Product.objects.create(
            category=self.category, name='Audit Product', unit='piece',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-SALES-AUDIT',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        self.customer = Customer.objects.create(
            name='Audit Customer', created_by=self.staff,
        )

    def test_void_does_not_restore_stock_to_batch_that_has_since_expired(self):
        order = SalesService.place_order(
            self.customer, [(self.product, Decimal('10.00'), Decimal('10.00'))], self.staff,
            amount_tendered=Decimal('100.00'),
        )
        self.batch.expiration_date = timezone.localdate() - timedelta(days=1)
        self.batch.save(update_fields=['expiration_date'])

        _, skipped = SalesService.void_fulfilled_order(order, self.admin)

        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))
        self.assertIn(self.batch.batch_number, skipped)

    def test_best_sellers_keep_same_named_products_separate(self):
        other = Product.objects.create(
            category=self.category, name=self.product.name, unit='piece',
            shelf_life=7,
        )
        ProductBatch.objects.create(
            product=other, batch_number='PRD-SALES-AUDIT-OTHER',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('10.00'),
            remaining_quantity=Decimal('10.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        SalesService.checkout(
            [(self.product, Decimal('2.00'), Decimal('10.00'))], self.staff,
            payment_method='online',
        )
        SalesService.checkout(
            [(other, Decimal('3.00'), Decimal('10.00'))], self.staff,
            payment_method='online',
        )

        rows = list(SalesService.get_best_sellers())

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row['product_id'] for row in rows}, {self.product.pk, other.pk}
        )

    def test_reports_use_sale_time_product_and_category_snapshots(self):
        original_product_name = self.product.name
        original_category_name = self.category.name
        SalesService.checkout(
            [(self.product, Decimal('1.00'), Decimal('10.00'))], self.staff,
            payment_method='online',
        )
        self.product.name = 'Renamed Product'
        self.product.save(update_fields=['name'])
        self.category.name = 'Renamed Category'
        self.category.save(update_fields=['name'])

        product_row = list(SalesService.get_best_sellers())[0]
        category_row = list(SalesService.get_sales_by_category())[0]

        self.assertEqual(product_row['product_name'], original_product_name)
        self.assertEqual(category_row['category_name'], original_category_name)

    def test_same_named_categories_remain_distinct_in_report(self):
        other_category = Category.objects.create(name=self.category.name)
        other_product = Product.objects.create(
            category=other_category, name='Other Product', unit='piece',
            shelf_life=7,
        )
        ProductBatch.objects.create(
            product=other_product, batch_number='PRD-SALES-OTHER-CATEGORY',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('10.00'),
            remaining_quantity=Decimal('10.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        SalesService.checkout(
            [(self.product, Decimal('2.00'), Decimal('10.00'))], self.staff,
            payment_method='online',
        )
        SalesService.checkout(
            [(other_product, Decimal('3.00'), Decimal('10.00'))], self.staff,
            payment_method='online',
        )

        rows = list(SalesService.get_sales_by_category())

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row['category_id'] for row in rows},
            {self.category.pk, other_category.pk},
        )

    def test_none_discount_rejects_nonzero_value_without_deducting_stock(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)

        response = client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '10.00'}],
            'payment_method': 'online',
            'discount_type': 'none',
            'discount_value': '-25.00',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_large_valid_line_total_is_stored_without_decimal_overflow(self):
        self.batch.unit_price = Decimal('99999999.99')
        self.batch.save(update_fields=['unit_price'])

        order = SalesService.place_order(
            self.customer, [(self.product, Decimal('2.00'), Decimal('99999999.99'))], self.staff,
            payment_method='online',
        )

        self.assertEqual(order.items.get().subtotal, Decimal('199999999.98'))
        self.assertEqual(order.transaction.total_amount, Decimal('199999999.98'))

    def test_transaction_can_only_belong_to_one_order(self):
        sale = Transaction.objects.create(handled_by=self.staff)
        Order.objects.create(
            customer=self.customer, handled_by=self.staff, transaction=sale,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                customer=self.customer, handled_by=self.staff, transaction=sale,
            )

    def test_checkout_processes_products_in_stable_primary_key_order(self):
        other = Product.objects.create(
            category=self.category, name='Second Product', unit='piece',
            shelf_life=7,
        )
        processed = []

        def record_product(product, quantity):
            processed.append(product.pk)
            return []

        with patch(
            'sales.services.checkout_service.BatchService.deduct_product_batch',
            side_effect=record_product,
        ):
            SalesService.checkout(
                [(other, Decimal('1.00'), Decimal('10.00')), (self.product, Decimal('1.00'), Decimal('10.00'))],
                self.staff, payment_method='online',
            )

        self.assertEqual(processed, sorted(processed))

    def test_transaction_history_query_count_does_not_grow_per_item(self):
        from inventory.models import FEFOConf
        FEFOConf.get_config()
        for _ in range(3):
            SalesService.checkout(
                [(self.product, Decimal('1.00'), Decimal('10.00'))], self.staff,
                payment_method='online',
            )
        client = APIClient()
        client.force_authenticate(user=self.staff)

        with CaptureQueriesContext(connection) as queries:
            response = client.get('/sales/transactions/')

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 5)

    def test_order_history_query_count_does_not_grow_per_item(self):
        for _ in range(3):
            SalesService.place_order(
                self.customer, [(self.product, Decimal('1.00'), Decimal('10.00'))], self.staff,
                payment_method='online',
            )
        client = APIClient()
        client.force_authenticate(user=self.staff)

        with CaptureQueriesContext(connection) as queries:
            response = client.get('/sales/orders/')

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 7)


# Annual sales parsing and import
# ----------------------------------------------------------------------------

class AnnualSalesParserTests(TestCase):
    def test_written_2022_is_preserved_and_truncated_223_is_corrected(self):
        self.assertEqual(source_date('3/21/22', 3)[0], date(2022, 3, 21))
        self.assertEqual(source_date('2/16/223', 2), (date(2023, 2, 16), 'corrected_truncated_year'))
        self.assertEqual(source_date(datetime(2023, 2, 1), 1)[0], date(2023, 1, 2))

    def test_cancelled_name_keeps_customer_identity(self):
        self.assertEqual(canonical_customer('PCC-UPLB(CANCELLED)'), 'PCC-UPLB')
        self.assertIsNone(canonical_customer('CANCELLED'))

    def test_discounts_and_repeated_invoice_are_not_extra_products_or_tickets(self):
        with TemporaryDirectory() as directory:
            for year in (2023, 2024, 2025):
                workbook = Workbook()
                workbook.active.title = 'JANUARY'
                for month in range(2, 13):
                    workbook.create_sheet(f'MONTH-{month}')
                if year == 2023:
                    sheet = workbook.active
                    sheet.append(['Title'])
                    sheet.append([])
                    sheet.append(['Date', 'Customer', 'Sales Invoice #', 'Products', 'Quantity', 'U/P', 'Total Amount'])
                    sheet.append(['1/1/23', 'JCG', 'SI1', 'Raw Milk 1L', 10, 105, 1039.5, None])
                    sheet.append([None, None, 'SI1', 'CML', 1, 100, 100, None])
                    sheet.append([None, None, None, 'DISCOUNT', 1, -10, -10, 1129.5])
                    sheet.append(['TOTAL', None, None, None, None, None, 1129.5])
                workbook.save(Path(directory) / f'SALES REPORT JAN-DEC {year}.xlsx')
            result = parse_workbooks(directory)
        self.assertEqual(result.summary()['tickets'], 1)
        self.assertEqual(result.summary()['line_items'], 2)
        self.assertEqual(result.summary()['discount_rows'], 1)
        self.assertEqual(result.summary()['revenue'], '1129.50')


class AnnualSalesLoaderTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create_user(
            username='importadmin', email='importadmin@example.com',
            password='testpass123!', role='admin',
        )

    def test_load_preserves_links_dates_amounts_and_zero_stock(self):
        ticket = SourceTicket('2023', 'JANUARY', 4, date(2022, 12, 31), 'Skye', 'SI-12')
        ticket.lines.append(SourceLine(4, 'Raw Milk 1000ml', Decimal('10'),
                                       Decimal('105'), Decimal('1039.50')))
        result = ParseResult(tickets=[ticket], monthly_checks=[{'difference': '0.00'}] * 36)
        summary = load(result, self.admin.username)
        self.assertEqual(summary['transactions'], 1)
        self.assertEqual(summary['revenue'], '1039.50')
        self.assertEqual(Customer.objects.get().name, 'Skye Dairy')
        sale = Transaction.objects.get()
        self.assertEqual(timezone.localtime(sale.created_at).date(), date(2022, 12, 31))
        self.assertEqual(sale.source_invoice_number, 'SI-12')
        self.assertEqual(sale.payment_method, 'unknown')
        self.assertEqual(sale.discount_amount, Decimal('10.50'))
        self.assertEqual(Order.objects.get().transaction_id, sale.pk)
        item = TransactionItem.objects.get()
        self.assertEqual(item.source_product_label, 'Raw Milk 1000ml')
        self.assertEqual(item.source_line_total, Decimal('1039.50'))
        batch = ProductBatch.objects.get()
        self.assertTrue(batch.is_historical_reference)
        self.assertEqual(batch.remaining_quantity, Decimal('0.00'))
        client = APIClient()
        client.force_authenticate(user=self.admin)
        listed = client.get('/sales/orders/?page=1&page_size=5')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['count'], 1)
        self.assertEqual(listed.data['results'][0]['transaction']['source_invoice_number'], 'SI-12')
        cancelled = client.post(f'/sales/orders/{Order.objects.get().pk}/cancel/')
        self.assertEqual(cancelled.status_code, 400)
        sale.refresh_from_db()
        self.assertFalse(sale.is_voided)

        duplicate = Customer.objects.create(name='SKYE DAIRY', created_by=self.admin)
        Transaction.objects.filter(pk=sale.pk).update(customer=duplicate)
        Order.objects.filter(transaction=sale).update(customer=duplicate)
        merged = merge_imported_customers(result)
        self.assertEqual(merged['merged'], 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().customer.name, 'Skye Dairy')
        self.assertEqual(Order.objects.get().customer_id, Transaction.objects.get().customer_id)

    def test_refuses_to_mix_with_existing_business_data(self):
        Customer.objects.create(name='Existing', created_by=self.admin)
        with self.assertRaisesRegex(ValueError, 'not empty'):
            load(ParseResult(monthly_checks=[{'difference': '0.00'}] * 36), self.admin.username)


# Customer transaction history
# ----------------------------------------------------------------------------

class CustomerTransactionHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = make_user('historyadmin', role='admin')
        self.staff = make_user('historystaff')
        self.customer = Customer.objects.create(name='Customer A', created_by=self.admin)
        self.other = Customer.objects.create(name='Customer B', created_by=self.admin)
        self.url = f'/sales/customers/{self.customer.pk}/transactions/'
        self.client.force_authenticate(self.admin)

    def sale(self, customer=None, **kwargs):
        return Transaction.objects.create(
            customer=customer or self.customer, handled_by=self.admin,
            subtotal=Decimal('100.00'), total_amount=Decimal('100.00'), **kwargs,
        )

    def test_only_selected_customer_with_full_receipt_and_newest_first(self):
        first = self.sale(source_invoice_number='SI0607')
        second = self.sale(source_invoice_number='SI0608')
        self.sale(customer=self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual([row['id'] for row in response.data['results']], [second.pk, first.pk])
        row = response.data['results'][0]
        self.assertEqual(row['customer']['id'], self.customer.pk)
        self.assertEqual(row['source_invoice_number'], 'SI0608')
        self.assertEqual(row['transaction_number'], 'SI0608')
        self.assertEqual(row['total_amount'], '100.00')
        self.assertIn('items', row)
        self.assertIn('payment_method', row)
        self.assertIn('discount_amount', row)

    def test_unrecorded_transaction_number_is_null(self):
        self.sale()
        response = self.client.get(self.url)
        self.assertIsNone(response.data['results'][0]['transaction_number'])

    def test_pagination(self):
        first = self.sale()
        self.sale()
        response = self.client.get(self.url, {'page_size': 1})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIsNotNone(response.data['next'])
        second_page = self.client.get(self.url, {'page_size': 1, 'page': 2})
        self.assertEqual(second_page.data['results'][0]['id'], first.pk)

    def test_voided_sales_are_opt_in(self):
        self.sale(is_voided=True)
        self.assertEqual(self.client.get(self.url).data['count'], 0)
        response = self.client.get(self.url, {'include_voided': 'true'})
        self.assertEqual(response.data['count'], 1)
        self.assertTrue(response.data['results'][0]['is_voided'])

    def test_inactive_customer_history_is_available_and_empty_history_is_valid(self):
        self.customer.is_active = False
        self.customer.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_unknown_customer_and_authentication(self):
        response = self.client.get('/sales/customers/999999999/transactions/')
        self.assertEqual(response.status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_staff_visibility_matches_sales_history_and_admin_receives_items(self):
        category = Category.objects.create(name='Private', is_visible_to_staff=False)
        product = Product.objects.create(
            name='Milk', category=category, unit='liter',  shelf_life=7,
        )
        batch = ProductBatch.objects.create(
            product=product, batch_number='HISTORY-PRIVATE', initial_quantity=10,
            remaining_quantity=9, expiration_date=timezone.localdate(),
        )
        hidden = self.sale()
        TransactionItem.objects.create(transaction=hidden, product_batch=batch, quantity=1, unit_price=100)
        visible = self.sale()
        self.client.force_authenticate(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], visible.pk)
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 2)
        row = next(row for row in response.data['results'] if row['id'] == hidden.pk)
        self.assertEqual(row['items'][0]['product_batch']['product']['id'], product.pk)


# Staff encoding and concurrent retries
# ----------------------------------------------------------------------------

class StaffEncodingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user('encodingstaff')
        self.admin = make_user('encodingadmin', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.category = Category.objects.create(name='Milk')
        self.product = Product.objects.create(category=self.category, name='Milk', unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='ENCODING-STOCK',
            initial_quantity=100, remaining_quantity=100,
            expiration_date=timezone.localdate() + timedelta(days=7))
        self.customer = Customer.objects.create(name='Encoding Buyer', created_by=self.staff)
        self.day = timezone.localdate() - timedelta(days=35)

    def payload(self, **overrides):
        return {'request_id': str(uuid4()), 'transaction_date': self.day.isoformat(),
            'customer_id': self.customer.pk, 'si_number': 'SI0608', 'note': 'Encoded from paper invoice',
            'payment_method': 'cash', 'payment_status': 'paid', 'amount_tendered': '400.00',
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '150.00'}],
            **overrides}

    def post(self, payload=None):
        return self.client.post('/sales/encoding/', payload or self.payload(), format='json')

    def test_staff_backdates_sale_deducts_current_stock_and_creates_linked_order(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        sale = Transaction.objects.get(pk=response.data['id'])
        self.assertEqual(timezone.localtime(sale.created_at).date(), self.day)
        self.assertEqual(timezone.localtime(sale.encoded_at).date(), timezone.localdate())
        self.assertEqual(sale.handled_by, self.staff)
        self.assertEqual(sale.customer, self.customer)
        self.assertEqual(sale.total_amount, Decimal('300'))
        self.assertEqual(sale.change_due, Decimal('100'))
        self.assertIsNone(sale.source_reference)
        self.assertEqual(sale.source_invoice_number, 'SI0608')
        self.assertEqual(response.data['transaction_number'], 'SI0608')
        self.assertEqual(response.data['transaction_date'], self.day.isoformat())
        self.assertTrue(response.data['is_manually_encoded'])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('98'))
        order = Order.objects.get(transaction=sale)
        self.assertEqual(timezone.localtime(order.created_at).date(), self.day)
        self.assertEqual(order.items.get().unit_price, Decimal('150'))
        self.assertEqual(order.items.get().total_amount, sale.total_amount)

    def test_existing_transaction_customer_order_report_and_forecast_consumers_see_same_sale(self):
        old_signature = load_snapshot().revision
        self.assertEqual(self.client.get('/sales/encoding/').data['count'], 0)
        version = data_version()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(data_version(), version)
        sale_id = response.data['id']
        for path in ('/sales/transactions/', f'/sales/customers/{self.customer.pk}/transactions/', '/sales/encoding/'):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['results'][0]['id'], sale_id)
        filtered = self.client.get('/sales/transactions/', {'start_date': self.day.isoformat(), 'end_date': self.day.isoformat()})
        self.assertEqual(filtered.data['count'], 1)
        today = self.client.get('/sales/transactions/', {'start_date': timezone.localdate().isoformat()})
        self.assertEqual(today.data['count'], 0)
        summary = self.client.get(f'/sales/customers/{self.customer.pk}/').data
        self.assertEqual(summary['transaction_count'], 1)
        self.assertEqual(timezone.localtime(datetime.fromisoformat(summary['last_sale'])).date(), self.day)
        listed_order = self.client.get('/sales/orders/').data['results'][0]
        self.assertEqual(listed_order['transaction']['id'], sale_id)
        self.assertEqual(listed_order['transaction']['transaction_date'], self.day.isoformat())
        rows = list(SalesService.get_revenue_report('daily', self.day, self.day))
        self.assertEqual(rows[0]['total'], Decimal('300'))
        self.assertEqual(list(SalesService.get_best_sellers())[0]['total_sold'], Decimal('2'))
        self.assertEqual(list(SalesService.get_sales_by_category())[0]['total_sold'], Decimal('2'))
        snapshot = load_snapshot()
        daily, signature = snapshot.daily, snapshot.revision
        self.assertEqual(daily[self.day], Decimal('300'))
        self.assertNotEqual(signature, old_signature)

    def test_retry_returns_same_receipt_without_double_deducting_or_repeating_order(self):
        payload = self.payload()
        payload['items'][0]['quantity'] = '100.00'
        payload['amount_tendered'] = '15000.00'
        first = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        again = self.post(payload)
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(first.data['id'], again.data['id'])
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 0)

    def test_request_id_conflicts_do_not_expose_other_users_receipts(self):
        payload = self.payload()
        self.assertEqual(self.post(payload).status_code, 201)
        changed = {**payload, 'si_number': 'SI-DIFFERENT'}
        self.assertEqual(self.post(changed).status_code, 409)
        self.client.force_authenticate(self.admin)
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('id', response.data)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_invalid_dates_missing_prices_or_insufficient_stock_leave_no_partial_records(self):
        for invalid in ('bad-date', '2026-02-30', (timezone.localdate() + timedelta(days=1)).isoformat(), None):
            response = self.post(self.payload(transaction_date=invalid))
            self.assertEqual(response.status_code, 400, response.data)
        missing = self.payload()
        missing['items'][0].pop('unit_price')
        self.assertEqual(self.post(missing).status_code, 400)
        shortage = self.payload(amount_tendered='200000.00')
        shortage['items'][0]['quantity'] = '1000'
        self.assertEqual(self.post(shortage).status_code, 400)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 100)

    def test_walk_in_and_today_are_supported_and_metadata_cannot_be_spoofed(self):
        payload = self.payload(customer_id=None, transaction_date=timezone.localdate().isoformat(),
            handled_by=self.admin.pk, encoded_at='2000-01-01T00:00:00Z')
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['handled_by']['id'], self.staff.pk)
        self.assertFalse(Order.objects.exists())
        self.assertIsNone(response.data['customer'])
        self.assertEqual(timezone.localtime(Transaction.objects.get().encoded_at).date(), timezone.localdate())

    def test_existing_staff_discount_permission_and_admin_item_discounts(self):
        payload = self.payload()
        payload['items'][0].update(discount_type='percent', discount_value='10')
        self.assertEqual(self.post(payload).status_code, 400)
        self.client.force_authenticate(self.admin)
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_amount'], '270.00')
        self.assertEqual(response.data['discount_amount'], '30.00')

    def test_hidden_products_are_unavailable_to_staff_and_manual_history_obeys_visibility(self):
        payload = self.payload()
        self.category.is_visible_to_staff = False
        self.category.save()
        self.assertEqual(self.post(payload).status_code, 400)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.post(payload).status_code, 201)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/sales/encoding/').data['count'], 0)

    def test_new_module_is_paginated_and_excludes_regular_pos_sales(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 201)
        Transaction.objects.create(handled_by=self.staff, total_amount=50)
        response = self.client.get('/sales/encoding/', {'page_size': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIsNotNone(response.data['next'])
        sale_id = response.data['results'][0]['id']
        self.assertEqual(self.client.get(f'/sales/encoding/{sale_id}/').status_code, 200)
        self.assertEqual(self.client.patch(f'/sales/encoding/{sale_id}/', {}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(f'/sales/encoding/{sale_id}/').status_code, 405)

    def test_voiding_linked_order_restores_stock_and_removes_sale_from_forecast(self):
        response = self.post()
        order = Order.objects.get(transaction_id=response.data['id'])
        self.client.force_authenticate(self.admin)
        voided = self.client.post(f'/sales/orders/{order.pk}/cancel/')
        self.assertEqual(voided.status_code, 200, voided.data)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 100)
        self.assertEqual(self.client.get('/sales/encoding/').data['count'], 0)
        included = self.client.get('/sales/encoding/', {'include_voided': 'true'})
        self.assertTrue(included.data['results'][0]['is_voided'])
        self.assertNotIn(self.day, load_snapshot().daily)

    def test_invalid_request_id_missing_date_and_unauthenticated_requests(self):
        self.assertEqual(self.post(self.payload(request_id='invalid')).status_code, 400)
        missing = self.payload()
        missing.pop('transaction_date')
        self.assertEqual(self.post(missing).status_code, 400)
        self.client.force_authenticate(user=None)
        self.assertIn(self.post().status_code, (401, 403))


@skipUnless(connection.vendor == 'postgresql', 'Request serialization uses PostgreSQL transaction locks.')
class ConcurrentEncodingTests(TransactionTestCase):
    def test_same_request_using_last_stock_commits_once_and_replays_successfully(self):
        cache.clear()
        user = make_user('concurrent-encoder')
        category = Category.objects.create(name='Concurrent Dairy')
        product = Product.objects.create(category=category, name='Milk', unit='L', shelf_life=7)
        batch = ProductBatch.objects.create(product=product, batch_number='LAST-ENCODING-STOCK',
            initial_quantity=1, remaining_quantity=1,
            expiration_date=timezone.localdate() + timedelta(days=7))
        payload = {'request_id': str(uuid4()), 'transaction_date': (timezone.localdate() - timedelta(days=1)).isoformat(),
            'payment_method': 'online',
            'items': [{'product_id': product.pk, 'quantity': '1', 'unit_price': '100'}]}
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                response = client.post('/sales/encoding/', payload, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(code for code, _ in results), [200, 201], results)
        self.assertEqual(results[0][1]['id'], results[1][1]['id'])
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionItem.objects.count(), 1)
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, 0)


# Encoded transaction payment updates
# ----------------------------------------------------------------------------

class EncodingPaymentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user('payment-encoder')
        self.other_staff = make_user('payment-updater')
        self.client = APIClient()
        self.client.force_authenticate(self.staff)
        self.category = Category.objects.create(name='Payment Dairy')
        self.product = Product.objects.create(category=self.category, name='Milk', unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='PAYMENT-STOCK',
            initial_quantity=100, remaining_quantity=100,
            expiration_date=timezone.localdate() + timedelta(days=7))
        self.customer = Customer.objects.create(name='Payment Buyer', created_by=self.staff)

    def payload(self, **extra):
        return {'request_id': str(uuid4()), 'transaction_date': str(timezone.localdate() - timedelta(days=1)),
            'customer_id': self.customer.pk, 'payment_method': 'cash',
            'items': [{'product_id': self.product.pk, 'quantity': '2', 'unit_price': '150'}], **extra}

    def create(self, **extra):
        response = self.client.post('/sales/encoding/', self.payload(**extra), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def patch(self, sale_id, **values):
        return self.client.patch(f'/sales/encoding/{sale_id}/payment/', values, format='json')

    def test_new_encoding_defaults_pending_without_tender_and_still_deducts_stock(self):
        response = self.create()
        self.assertEqual(response.data['payment_status'], 'pending')
        self.assertIsNone(response.data['amount_tendered'])
        self.assertIsNone(response.data['change_due'])
        self.assertEqual(response.data['payment_updated_by'], self.staff.pk)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 98)
        self.assertEqual(Order.objects.get().transaction_id, response.data['id'])

    def test_paid_on_creation_requires_enough_cash_and_rejects_partial_pending_payments(self):
        for extra in ({'payment_status': 'paid'}, {'payment_status': 'paid', 'amount_tendered': '299.99'},
                      {'payment_status': 'pending', 'amount_tendered': '100'}):
            response = self.client.post('/sales/encoding/', self.payload(**extra), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        response = self.create(payment_status='paid', amount_tendered='400')
        self.assertEqual(response.data['payment_status'], 'paid')
        self.assertEqual(response.data['change_due'], '100.00')

    def test_mark_paid_updates_shared_history_without_new_sale_stock_deduction_or_revenue(self):
        created = self.create()
        sale_id = created.data['id']
        sale = Transaction.objects.get(pk=sale_id)
        source_dates = sale.created_at, sale.encoded_at
        item_rows = list(sale.items.values())
        signature = load_snapshot().revision
        # Warm shared history before changing payment state.
        self.assertEqual(self.client.get(f'/sales/customers/{self.customer.pk}/transactions/').data['results'][0]['payment_status'], 'pending')
        version = data_version()
        self.client.force_authenticate(self.other_staff)
        with self.captureOnCommitCallbacks(execute=True):
            paid = self.patch(sale_id, payment_status='paid', amount_tendered='350')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data['payment_status'], 'paid')
        self.assertEqual(paid.data['change_due'], '50.00')
        self.assertEqual(paid.data['payment_updated_by'], self.other_staff.pk)
        self.assertIsNotNone(paid.data['payment_updated_at'])
        self.assertNotEqual(data_version(), version)
        sale.refresh_from_db()
        self.assertEqual((sale.created_at, sale.encoded_at), source_dates)
        self.assertEqual(list(sale.items.values()), item_rows)
        self.assertEqual(sale.total_amount, Decimal('300'))
        self.assertEqual(load_snapshot().revision, signature)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 98)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 1)
        for url in ('/sales/transactions/', f'/sales/customers/{self.customer.pk}/transactions/', '/sales/encoding/'):
            row = self.client.get(url).data['results'][0]
            self.assertEqual(row['payment_status'], 'paid')
        self.assertEqual(self.client.get('/sales/orders/').data['results'][0]['transaction']['payment_status'], 'paid')

    def test_mark_unpaid_clears_cash_fields_and_repeated_payment_update_is_idempotent(self):
        created = self.create(payment_status='paid', amount_tendered='400')
        sale_id = created.data['id']
        repeated = self.patch(sale_id, payment_status='paid')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['payment_updated_at'], created.data['payment_updated_at'])
        pending = self.patch(sale_id, payment_status='pending')
        self.assertEqual(pending.status_code, 200, pending.data)
        self.assertEqual(pending.data['payment_status'], 'pending')
        self.assertIsNone(pending.data['amount_tendered'])
        self.assertIsNone(pending.data['change_due'])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 98)

    def test_online_payment_can_be_marked_paid_without_cash_tender(self):
        sale_id = self.create().data['id']
        response = self.patch(sale_id, payment_status='paid', payment_method='online')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['payment_status'], 'paid')
        self.assertEqual(response.data['payment_method'], 'online')
        self.assertIsNone(response.data['amount_tendered'])
        self.assertIsNone(response.data['change_due'])

    def test_invalid_payment_updates_reject_without_modifying_status(self):
        sale_id = self.create().data['id']
        for values in ({}, {'payment_status': 'unknown'}, {'payment_status': 'paid'},
                       {'payment_status': 'paid', 'amount_tendered': '299'},
                       {'payment_status': 'paid', 'amount_tendered': '-1'},
                       {'payment_status': 'pending', 'amount_tendered': '10'},
                       {'payment_status': 'paid', 'payment_method': 'invalid'},
                       {'payment_status': 'pending', 'total_amount': '1'}):
            response = self.patch(sale_id, **values)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Transaction.objects.get(pk=sale_id).payment_status, 'pending')

    def test_original_create_retry_returns_current_payment_without_duplicating_sale(self):
        payload = self.payload()
        original = self.client.post('/sales/encoding/', payload, format='json')
        sale_id = original.data['id']
        self.assertEqual(self.patch(sale_id, payment_status='paid', amount_tendered='300').status_code, 200)
        retry = self.client.post('/sales/encoding/', payload, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['id'], sale_id)
        self.assertEqual(retry.data['payment_status'], 'paid')
        self.assertEqual(Transaction.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 98)

    def test_payment_filter_and_existing_unknown_history(self):
        self.create()
        self.create(payment_status='paid', amount_tendered='300')
        legacy = Transaction.objects.create(handled_by=self.staff, total_amount=10)
        self.assertEqual(legacy.payment_status, 'unknown')
        for value in ('pending', 'paid'):
            for url in ('/sales/encoding/', '/sales/transactions/'):
                response = self.client.get(url, {'payment_status': value})
                self.assertEqual(response.data['count'], 1)
                self.assertEqual(response.data['results'][0]['payment_status'], value)
        self.assertEqual(self.client.get('/sales/transactions/', {'payment_status': 'unknown'}).data['count'], 1)
        self.assertEqual(self.client.get('/sales/encoding/', {'payment_status': 'bad'}).status_code, 400)

    def test_visibility_voided_nonmanual_and_unauthenticated_updates_are_protected(self):
        sale_id = self.create().data['id']
        self.category.is_visible_to_staff = False
        self.category.save()
        self.assertEqual(self.patch(sale_id, payment_status='paid', amount_tendered='300').status_code, 404)
        self.category.is_visible_to_staff = True
        self.category.save()
        sale = Transaction.objects.get(pk=sale_id)
        sale.is_voided = True
        sale.save()
        self.assertEqual(self.patch(sale_id, payment_status='paid', amount_tendered='300').status_code, 400)
        normal = Transaction.objects.create(handled_by=self.staff)
        self.assertEqual(self.patch(normal.pk, payment_status='pending').status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.patch(sale_id, payment_status='pending').status_code, (401, 403))

    def test_regular_checkout_still_records_full_payment(self):
        response = self.client.post('/sales/checkout/', {
            'payment_method': 'cash', 'amount_tendered': '150',
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'unit_price': '150'}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['payment_status'], 'paid')


# History performance, caching and date boundaries
# ----------------------------------------------------------------------------

class SalesHistoryPerformanceTests(TestCase):
    def setUp(self):
        cache.clear()
        FEFOConf.get_config()
        self.user = get_user_model().objects.create_user(
            username='history-performance', email='history-performance@example.com', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        category = Category.objects.create(name='Milk')
        product = Product.objects.create(
            category=category, name='Milk', unit='L',  shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=product, batch_number='HISTORY-PERF', initial_quantity=100,
            remaining_quantity=100, expiration_date=timezone.localdate() + timedelta(days=7),
        )
        self.sale = Transaction.objects.create(handled_by=self.user, subtotal=50, total_amount=50)
        for quantity in (1, 2, 3):
            TransactionItem.objects.create(
                transaction=self.sale, product_batch=self.batch,
                quantity=quantity, unit_price=50, product_name_snapshot=f'Milk snapshot {quantity}',
            )

    def test_repeated_batch_details_are_serialized_once_without_changing_shape(self):
        original = ProdBatchSerializer.to_representation
        expected = ProdBatchSerializer(self.batch).data
        with patch.object(ProdBatchSerializer, 'to_representation', autospec=True, side_effect=original) as serialize:
            data = TransactionSerializer(self.sale).data
        self.assertEqual(serialize.call_count, 1)
        for index, item in enumerate(data['items'], start=1):
            self.assertEqual(item['product_batch'], expected)
            self.assertEqual(item['quantity'], f'{index}.00')
            self.assertEqual(item['product_name_snapshot'], f'Milk snapshot {index}')

    def test_separate_batches_and_requests_keep_current_values(self):
        another = ProductBatch.objects.create(
            product=self.batch.product, batch_number='HISTORY-OTHER', initial_quantity=30,
            remaining_quantity=30, expiration_date=self.batch.expiration_date,
        )
        TransactionItem.objects.create(transaction=self.sale, product_batch=another, quantity=1, unit_price=40)
        data = TransactionSerializer(self.sale).data
        quantities = {item['product_batch']['id']: item['product_batch']['remaining_quantity'] for item in data['items']}
        self.assertEqual(quantities, {self.batch.pk: '100.00', another.pk: '30.00'})
        self.batch.remaining_quantity = 10
        self.batch.save()
        fresh = TransactionSerializer(Transaction.objects.get(pk=self.sale.pk)).data
        self.assertEqual(fresh['items'][0]['product_batch']['remaining_quantity'], '10.00')

    def test_gzip_preserves_cached_history_data_and_plain_clients_still_work(self):
        endpoint = '/sales/transactions/'
        plain = self.client.get(endpoint)
        compressed = self.client.get(endpoint, HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', compressed['Vary'])
        self.assertLess(len(compressed.content), len(plain.content))
        self.assertEqual(json.loads(gzip.decompress(compressed.content)), json.loads(plain.content))
        repeated = self.client.get(endpoint)
        self.assertNotIn('Content-Encoding', repeated)
        self.assertEqual(repeated.content, plain.content)

    def test_fetch_shares_batch_objects_and_has_bounded_queries(self):
        view = TransactionViewSet()
        view.action = 'list'
        view.request = type('Request', (), {'user': self.user})()
        # One shared global-threshold read, independent of the number of items.
        with self.assertNumQueries(4):
            sale = list(view.get_queryset())[0]
            items = list(sale.items.all())
            data = TransactionSerializer(sale).data
        self.assertTrue(all(item.product_batch is items[0].product_batch for item in items))
        self.assertEqual(data['items'][0]['product_batch']['product']['total_stock'], 100)

    def test_warm_history_reuses_rendered_json_and_indented_clients_are_separate(self):
        original = JSONRenderer.render
        with patch.object(JSONRenderer, 'render', autospec=True, side_effect=original) as render:
            plain = self.client.get('/sales/transactions/')
            repeated = self.client.get('/sales/transactions/')
            self.assertEqual(render.call_count, 1)
            pretty = self.client.get('/sales/transactions/', HTTP_ACCEPT='application/json; indent=4')
            self.assertEqual(render.call_count, 2)
        self.assertEqual(repeated.content, plain.content)
        self.assertEqual(json.loads(pretty.content), json.loads(plain.content))
        self.assertIn(b'\n', pretty.content)

    def test_commit_invalidates_rendered_history(self):
        first = self.client.get('/sales/transactions/')
        self.assertEqual(first.json()['count'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.sale.is_voided = True
            self.sale.save()
        self.assertEqual(self.client.get('/sales/transactions/').json()['count'], 0)

    def test_timestamp_ties_are_ordered_consistently_across_pages(self):
        another = Transaction.objects.create(handled_by=self.user, total_amount=50)
        Transaction.objects.filter(pk=another.pk).update(created_at=self.sale.created_at)
        first = self.client.get('/sales/transactions/?page_size=1&page=1').json()
        second = self.client.get('/sales/transactions/?page_size=1&page=2').json()
        self.assertEqual(first['results'][0]['id'], another.pk)
        self.assertEqual(second['results'][0]['id'], self.sale.pk)

    def test_date_filter_includes_the_entire_local_day(self):
        day = timezone.localdate()
        begin = timezone.make_aware(datetime.combine(day, time.min))
        end = timezone.make_aware(datetime.combine(day, time.max))
        Transaction.objects.filter(pk=self.sale.pk).update(created_at=begin)
        included = Transaction.objects.create(handled_by=self.user, total_amount=50)
        before = Transaction.objects.create(handled_by=self.user, total_amount=50)
        after = Transaction.objects.create(handled_by=self.user, total_amount=50)
        Transaction.objects.filter(pk=included.pk).update(created_at=end)
        Transaction.objects.filter(pk=before.pk).update(created_at=begin - timedelta(microseconds=1))
        Transaction.objects.filter(pk=after.pk).update(created_at=end + timedelta(microseconds=1))
        data = self.client.get(f'/sales/transactions/?start_date={day}&end_date={day}').json()
        self.assertEqual(data['count'], 2)
        self.assertEqual({row['id'] for row in data['results']}, {self.sale.pk, included.pk})

    def test_history_stays_warm_beyond_five_minutes(self):
        started = clock_time.time()
        first = self.client.get('/sales/transactions/')
        self.assertEqual(first['X-Data-Cache'], 'MISS')
        with patch('time.time', return_value=started + 301):
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'HIT')
        with patch('time.time', return_value=started + 3601):
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'MISS')

    def test_loading_a_shared_payload_does_not_extend_its_expiry(self):
        from config.cache_payloads import _memory, get_payload, set_payload
        started = clock_time.time()
        key = 'shared-payload-expiry-test'
        with patch('time.time', return_value=started):
            set_payload(key, {'count': 1}, 10)
        _memory.delete(key)
        with patch('time.time', return_value=started + 9):
            self.assertEqual(get_payload(key), {'count': 1})
        with patch('time.time', return_value=started + 11):
            self.assertIsNone(get_payload(key))

    def test_authentication_metadata_does_not_evict_history(self):
        self.client.get('/sales/transactions/')
        with self.captureOnCommitCallbacks(execute=True):
            self.user.last_login = timezone.now()
            self.user.save(update_fields=['last_login'])
            self.user.failed_login_attempts = 0
            self.user.save(update_fields=['failed_login_attempts', 'locked_until'])
        self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'HIT')
        with self.captureOnCommitCallbacks(execute=True):
            self.user.username = 'renamed-history-user'
            self.user.save(update_fields=['username'])
        renamed = self.client.get('/sales/transactions/')
        self.assertEqual(renamed['X-Data-Cache'], 'MISS')
        self.assertEqual(renamed.json()['results'][0]['handled_by']['username'], 'renamed-history-user')

    def test_file_cache_shares_payloads_and_invalidations_across_processes(self):
        with TemporaryDirectory() as directory, override_settings(CACHES={
            'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': directory},
        }):
            first = self.client.get('/sales/transactions/')
            script = '''
import json, sys, uuid
from django.conf import settings
settings.configure()
from django.core.cache.backends.filebased import FileBasedCache
cache = FileBasedCache(sys.argv[1], {})
key = sys.argv[2]
assert cache.get(key)[1]['count'] == 1
assert json.loads(cache.get(key + ':json:None')[1])['count'] == 1
cache.set('business-data:version:v1', uuid.uuid4().hex, timeout=None)
'''
            subprocess.run([sys.executable, '-c', script, directory, first.data_cache_key], check=True)
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'MISS')

    def test_orders_reuse_rendered_json_and_shared_product_objects(self):
        customer = Customer.objects.create(name='Order Customer', created_by=self.user)
        order = Order.objects.create(customer=customer, handled_by=self.user, transaction=self.sale)
        for quantity in (1, 2, 3):
            OrderItem.objects.create(order=order, product=self.batch.product, quantity=quantity,
                                     unit_price=50, subtotal=quantity * 50)
        from sales.views.order_views import OrderViewSet
        from sales.serializers.order_serializer import OrderListSerializer
        view = OrderViewSet()
        view.action = 'list'
        view.request = type('Request', (), {'user': self.user, 'query_params': {}})()
        with self.assertNumQueries(3):
            loaded = list(view.get_queryset())[0]
            items = list(loaded.items.all())
            data = OrderListSerializer(loaded).data
        self.assertTrue(all(item.product is items[0].product for item in items))
        self.assertEqual(data['items'][0]['product']['name'], 'Milk')
        original = JSONRenderer.render
        with patch.object(JSONRenderer, 'render', autospec=True, side_effect=original) as render:
            first = self.client.get('/sales/orders/')
            repeated = self.client.get('/sales/orders/')
            self.assertEqual(render.call_count, 1)
        self.assertEqual(first['X-Data-Cache'], 'MISS')
        self.assertEqual(repeated['X-Data-Cache'], 'HIT')
        self.assertEqual(first.content, repeated.content)


# Manual item prices and line discounts
# ----------------------------------------------------------------------------

class ManualItemPricingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user('manualadmin', role='admin')
        self.staff = make_user('manualstaff')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.customer = Customer.objects.create(name='Manual Customer', created_by=self.admin)
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(name='Milk', category=self.category, unit='liter', shelf_life=7)
        self.other = Product.objects.create(name='Chocolate Milk', category=self.category, unit='liter', shelf_life=7)
        self.batch = self.stock(self.product, 'MANUAL-A', 3)
        self.stock(self.product, 'MANUAL-B', 100, days=8)
        self.stock(self.other, 'MANUAL-C', 100)

    def stock(self, product, number, quantity, days=7):
        return ProductBatch.objects.create(product=product, batch_number=number, unit_price=999,
            initial_quantity=quantity, remaining_quantity=quantity,
            expiration_date=timezone.localdate() + timedelta(days=days))

    def payload(self):
        return {'customer_id': self.customer.pk, 'payment_method': 'online', 'items': [
            {'product_id': self.product.pk, 'quantity': '10', 'unit_price': '150', 'discount_type': 'fixed', 'discount_value': '50'},
            {'product_id': self.other.pk, 'quantity': '5', 'unit_price': '160', 'discount_type': 'percent', 'discount_value': '10'},
        ]}

    def test_checkout_prices_discount_each_line_and_split_stock_once(self):
        response = self.client.post('/sales/checkout/', self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['subtotal'], '2300.00')
        self.assertEqual(response.data['discount_amount'], '130.00')
        self.assertEqual(response.data['total_amount'], '2170.00')
        self.assertEqual(response.data['discount_type'], 'none')
        milk = [i for i in response.data['items'] if i['product_id_snapshot'] == self.product.pk]
        self.assertEqual(len(milk), 2)
        self.assertEqual(sum(Decimal(i['discount_amount']) for i in milk), Decimal('50'))
        self.assertEqual(sum(Decimal(i['total_amount']) for i in milk), Decimal('1450'))
        self.assertEqual({i['unit_price'] for i in milk}, {'150.00'})
        self.assertEqual({i['line_number'] for i in milk}, {1})
        self.assertEqual(sum(Decimal(i['total_amount']) for i in response.data['items']), Decimal('2170'))

    def test_order_prices_and_item_discounts_match_checkout_and_history(self):
        response = self.client.post('/sales/orders/', self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['transaction']['total_amount'], '2170.00')
        self.assertEqual([i['total_amount'] for i in response.data['items']], ['1450.00', '720.00'])
        self.assertEqual([i['discount_value'] for i in response.data['items']], ['50.00', '10.00'])
        listed = self.client.get('/sales/orders/').data['results'][0]
        self.assertEqual(listed['items'][0]['total_amount'], '1450.00')
        history = self.client.get(f'/sales/customers/{self.customer.pk}/transactions/').data['results'][0]
        self.assertEqual(history['total_amount'], '2170.00')
        self.assertEqual(sum(Decimal(i['discount_amount']) for i in history['items']), Decimal('130'))

    def test_price_required_every_time_no_batch_fallback_and_invalid_values_rollback(self):
        for endpoint in ('/sales/checkout/', '/sales/orders/'):
            for bad in ('missing', None, '', '-1', 'NaN', 'Infinity', '1.001', '100000000', True):
                with self.subTest(endpoint=endpoint, price=bad):
                    payload = self.payload()
                    if bad == 'missing':
                        payload['items'][0].pop('unit_price')
                    else:
                        payload['items'][0]['unit_price'] = bad
                    response = self.client.post(endpoint, payload, format='json')
                    self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 3)

    def test_service_cannot_omit_entered_price(self):
        for service, args in ((SalesService.checkout, ([(self.product, Decimal('1'))], self.admin)),
                              (SalesService.place_order, (self.customer, [(self.product, Decimal('1'))], self.admin))):
            with self.assertRaisesRegex(ValueError, 'unit_price'):
                service(*args, payment_method='online')
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_each_sale_requires_its_own_price_and_keeps_previous_sale_price(self):
        def payload(price):
            item = {'product_id': self.product.pk, 'quantity': '1'}
            if price is not None:
                item['unit_price'] = price
            return {'payment_method': 'online', 'items': [item]}
        first = self.client.post('/sales/checkout/', payload('75.00'), format='json')
        self.assertEqual(first.status_code, 201, first.data)
        missing = self.client.post('/sales/checkout/', payload(None), format='json')
        self.assertEqual(missing.status_code, 400)
        second = self.client.post('/sales/checkout/', payload('95.00'), format='json')
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(Transaction.objects.get(pk=first.data['id']).total_amount, Decimal('75'))
        self.assertEqual(Transaction.objects.get(pk=second.data['id']).total_amount, Decimal('95'))

    def test_free_items_require_explicit_price_or_full_item_discount(self):
        for price, kind, value in [('0', 'none', '0'), ('150', 'percent', '100')]:
            response = self.client.post('/sales/checkout/', {'payment_method': 'online', 'items': [
                {'product_id': self.product.pk, 'quantity': '1', 'unit_price': price,
                 'discount_type': kind, 'discount_value': value}]}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['total_amount'], '0.00')

    def test_invalid_item_discounts_and_global_discounts_are_rejected(self):
        for endpoint in ('/sales/checkout/', '/sales/orders/'):
            for kind, value in [('percent', '101'), ('percent', '-1'), ('fixed', '1501'), ('fixed', 'NaN'), ('none', '1'), ('invalid', '0')]:
                payload = self.payload()
                payload['items'][0].update(discount_type=kind, discount_value=value)
                response = self.client.post(endpoint, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
            payload = self.payload()
            payload.update(discount_type='fixed', discount_value='1')
            self.assertEqual(self.client.post(endpoint, payload, format='json').status_code, 400)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_staff_can_enter_price_but_existing_discount_permission_remains(self):
        self.client.force_authenticate(self.staff)
        payload = {'payment_method': 'online', 'items': [{'product_id': self.product.pk, 'quantity': '1', 'unit_price': '12.34'}]}
        self.assertEqual(self.client.post('/sales/checkout/', payload, format='json').status_code, 201)
        payload['items'][0].update(discount_type='fixed', discount_value='1')
        self.assertEqual(self.client.post('/sales/checkout/', payload, format='json').status_code, 400)

    def test_fixed_discount_is_per_line_not_per_unit_and_cash_uses_net_total(self):
        payload = {'payment_method': 'cash', 'amount_tendered': '300', 'items': [
            {'product_id': self.product.pk, 'quantity': '2', 'unit_price': '150', 'discount_type': 'fixed', 'discount_value': '50'}]}
        response = self.client.post('/sales/checkout/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_amount'], '250.00')
        self.assertEqual(response.data['change_due'], '50.00')

    def test_fractional_quantities_reconcile_across_batches_with_rounding(self):
        ProductBatch.objects.filter(product=self.product).delete()
        self.stock(self.product, 'ROUND-A', Decimal('.01'))
        self.stock(self.product, 'ROUND-B', Decimal('.04'), days=8)
        txn = SalesService.checkout([(self.product, Decimal('.05'), Decimal('.33'), 'percent', Decimal('50'))], self.admin, payment_method='online')
        items = list(txn.items.all())
        self.assertEqual(txn.subtotal, Decimal('.02'))
        self.assertEqual(txn.discount_amount, Decimal('.01'))
        self.assertEqual(sum(i.subtotal for i in items), txn.subtotal)
        self.assertEqual(sum(i.discount_amount for i in items), txn.discount_amount)
        self.assertEqual(sum(i.total_amount for i in items), txn.total_amount)

    def test_product_catalog_has_no_selling_price_and_batch_cost_has_no_default(self):
        data = {'name': 'New Milk', 'category_id': self.category.pk, 'unit': 'liter', 'shelf_life': 7}
        response = self.client.post('/inventory/products/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('unit_price', response.data)
        self.assertNotIn('unit_price', {f.name for f in Product._meta.fields})
        data['unit_price'] = '100'
        self.assertEqual(self.client.post('/inventory/products/', data, format='json').status_code, 400)
        response = self.client.post('/inventory/product-batches/', {'product_id': self.other.pk, 'quantity': '1',
            'expiration_date': str(timezone.localdate() + timedelta(days=7))}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['unit_price'])

    def test_insufficient_stock_rolls_back_order_items_and_transaction(self):
        payload = self.payload()
        payload['items'][1]['quantity'] = '1000'
        response = self.client.post('/sales/orders/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 3)


# Demonstration-year sales command
# ----------------------------------------------------------------------------

class Populate2026SalesTests(TestCase):
    def setUp(self):
        owner = Users.objects.create_user(username='owner2026', role='admin')
        self.customer = Customer.objects.create(name='Regular customer', created_by=owner)
        category = Category.objects.create(name='Milk')
        product = Product.objects.create(name='FML', category=category, unit='L',
                                          shelf_life=7)
        self.batch = ProductBatch.objects.create(
            product=product, batch_number='HIST-TEST', initial_quantity=0, remaining_quantity=0,
            date_received=date(2024, 1, 1), expiration_date=date(2024, 1, 1),
            is_historical_reference=True, status='depleted')
        for year in (2024, 2025):
            sale = Transaction.objects.create(
                customer=self.customer, handled_by=owner, subtotal=Decimal('125.00'),
                source_reference=f'{year}:FEBRUARY:4', source_sales_segment='regular',
                discount_type='fixed', discount_amount=Decimal('5.00'),
                discount_value=Decimal('5.00'), total_amount=Decimal('120.00'))
            stamp = timezone.make_aware(datetime.combine(date(year, 2, 15), time(12)))
            Transaction.objects.filter(pk=sale.pk).update(created_at=stamp)
            TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
                                           quantity=10, unit_price=10, source_line_total=95)
            TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
                                           quantity=Decimal('2.50'), unit_price=0,
                                           source_line_total=25)
        self.original = list(Transaction.objects.order_by('id').values())

    def test_repeatable_generation_reconciles_orders_discounts_and_stock(self):
        plan = build_plan(2026)
        self.assertEqual(summary(plan), summary(build_plan(2026)))
        self.assertEqual(summary(plan)['transactions'], 1)
        self.assertEqual(plan[0]['lines'][1]['price'], Decimal('10.00'))
        with self.captureOnCommitCallbacks(execute=True):
            insert_plan(plan)
        sale = Transaction.objects.get(created_at__year=2026)
        self.assertEqual(sale.subtotal - sale.discount_amount, sale.total_amount)
        self.assertEqual(sum(item.source_line_total for item in sale.items.all()), sale.total_amount)
        self.assertEqual(sum(item.quantity * item.unit_price for item in sale.items.all()), sale.subtotal)
        order = Order.objects.get(transaction=sale)
        self.assertEqual(order.created_at, sale.created_at)
        self.assertEqual(order.customer_id, sale.customer_id)
        self.assertEqual(sum(item.total_amount for item in order.items.all()), sale.total_amount)
        self.assertEqual(sum(item.subtotal for item in order.items.all()), sale.subtotal)
        self.assertEqual(order.discount_value, sale.discount_amount)
        self.assertEqual(OrderItem.objects.count(), 2)
        self.assertIn('Demonstration data only', sale.source_note)
        self.assertIsNone(sale.source_invoice_number)
        self.assertEqual(self.original, list(Transaction.objects.exclude(pk=sale.pk).order_by('id').values()))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 0)
        with self.assertRaisesRegex(CommandError, 'already exist'):
            call_command('populate_2026_sales', apply=True, stdout=io.StringIO())

    def test_preview_does_not_write_and_backup_is_readable(self):
        output = io.StringIO()
        call_command('populate_2026_sales', stdout=output)
        self.assertFalse(json.loads(output.getvalue())['applied'])
        self.assertEqual(Transaction.objects.count(), 2)
        with TemporaryDirectory() as root:
            backup = backup_database(Path(root) / 'backup')
            with gzip.open(backup, 'rt') as stream:
                rows = json.load(stream)
            self.assertEqual(len([row for row in rows if row['model'] == 'sales.transaction']), 2)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_calendar_alignment_and_month_end(self):
        for month in range(1, 13):
            source = date(2025, month, 15)
            result = target_date(source)
            self.assertEqual(result.month, source.month)
            self.assertEqual(result.weekday(), source.weekday())
            self.assertLessEqual(abs(result.day - source.day), 3)
        self.assertEqual(target_date(date(2025, 12, 31)), date(2026, 12, 31))

    def test_live_batches_require_review(self):
        self.batch.is_historical_reference = False
        self.batch.save()
        with self.assertRaisesRegex(CommandError, 'live inventory'):
            build_plan(2026)

    def test_only_classified_imported_history_is_used(self):
        original = Transaction.objects.get(source_reference='2025:FEBRUARY:4')
        for reference, segment, customer in (
            ('2025:JANUARY:645', 'unclassified', None),
            (None, '', self.customer),
        ):
            sale = Transaction.objects.create(
                handled_by=original.handled_by, customer=customer,
                source_reference=reference, source_sales_segment=segment,
                total_amount=Decimal('2087137'))
            Transaction.objects.filter(pk=sale.pk).update(created_at=original.created_at)
        plan = build_plan(2026)
        self.assertEqual([entry['source'].pk for entry in plan], [original.pk])

    def test_stale_non_dairy_baskets_require_source_refresh(self):
        product = self.batch.product
        product.name = 'PDLX20'; product.save()
        with self.assertRaisesRegex(CommandError, 'non-dairy/unreviewed'):
            build_plan(2026)

    def test_replace_demo_preserves_actual_sales_stock_and_forecasting_source(self):
        insert_plan(build_plan(2026))
        old_demo = Transaction.objects.get(source_reference='2026:000001')
        actual = Transaction.objects.create(
            handled_by=old_demo.handled_by, customer=self.customer,
            source_reference='2026:OCT:123', encoded_at=timezone.now(),
            source_invoice_number='SI-ACTUAL', total_amount=Decimal('888'))
        before = list(Transaction.objects.exclude(pk=old_demo.pk).order_by('pk').values())
        revision = load_snapshot().revision
        with TemporaryDirectory() as root, self.settings(BASE_DIR=Path(root)):
            output = io.StringIO()
            call_command('populate_2026_sales', apply=True, replace_demo=True, stdout=output)
            result = json.loads(output.getvalue())
            manifest = json.loads((Path(result['backup']).parent / 'manifest.json').read_text())
            self.assertTrue(manifest['completed'])
            self.assertTrue(manifest['protected_verified'])
        replacement = Transaction.objects.get(source_reference='2026:000001')
        self.assertNotEqual(replacement.pk, old_demo.pk)
        self.assertFalse(Transaction.objects.filter(pk=old_demo.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=actual.pk).exists())
        self.assertEqual(before, list(Transaction.objects.exclude(pk=replacement.pk).order_by('pk').values()))
        self.assertEqual(load_snapshot().revision, revision)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 0)

    def test_replace_demo_refuses_live_inventory_and_rolls_back_failed_inserts(self):
        insert_plan(build_plan(2026))
        demo = Transaction.objects.get(source_reference='2026:000001')
        Transaction.objects.filter(pk=demo.pk).update(encoded_at=timezone.now())
        with TemporaryDirectory() as root, self.settings(BASE_DIR=Path(root)):
            with self.assertRaisesRegex(CommandError, 'manually encoded'):
                call_command('populate_2026_sales', apply=True, replace_demo=True, stdout=io.StringIO())
        Transaction.objects.filter(pk=demo.pk).update(encoded_at=None)
        live_batch = ProductBatch.objects.create(
            product=self.batch.product, batch_number='DEMO-GUARD-LIVE', initial_quantity=100,
            remaining_quantity=100, expiration_date=date(2026, 12, 31))
        demo.items.update(product_batch=live_batch)
        with TemporaryDirectory() as root, self.settings(BASE_DIR=Path(root)):
            with self.assertRaisesRegex(CommandError, 'live inventory'):
                call_command('populate_2026_sales', apply=True, replace_demo=True, stdout=io.StringIO())
        demo.items.update(product_batch=self.batch)
        before = list(Transaction.objects.order_by('pk').values())
        item_ids = list(demo.items.values_list('pk', flat=True))
        with TemporaryDirectory() as root, self.settings(BASE_DIR=Path(root)):
            with patch('sales.management.commands.populate_2026_sales.insert_plan', side_effect=ValueError('simulated insert failure')):
                with self.assertRaisesRegex(ValueError, 'simulated insert failure'):
                    call_command('populate_2026_sales', apply=True, replace_demo=True, stdout=io.StringIO())
        self.assertEqual(before, list(Transaction.objects.order_by('pk').values()))
        self.assertEqual(item_ids, list(demo.items.values_list('pk', flat=True)))


# Recorded transaction numbers
# ----------------------------------------------------------------------------

class TransactionNumberTests(SimpleTestCase):
    def test_full_and_compact_serializers_preserve_recorded_format(self):
        for serializer_class in (TransactionSerializer, OrderListTransactionSerializer):
            field = serializer_class().fields['transaction_number']
            for number in ('SI0608', 'SI0001', 'SI-12', '00123'):
                with self.subTest(serializer=serializer_class.__name__, number=number):
                    self.assertEqual(field.to_representation(Transaction(source_invoice_number=number)), number)
            self.assertIsNone(field.to_representation(Transaction(source_invoice_number='')))

    def test_transaction_number_cannot_be_overwritten_by_clients(self):
        for serializer_class in (TransactionSerializer, OrderListTransactionSerializer):
            self.assertTrue(serializer_class().fields['transaction_number'].read_only)
