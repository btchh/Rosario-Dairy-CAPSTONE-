"""Retire discount behavior and output without changing historical stored values."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch
from reporting.pdf import Report
from sales.models import Customer, Order, OrderItem, SaleSubmission, Transaction, TransactionItem
from sales.services import SalesService


class DiscountRetirementTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.admin = users.objects.create_user(username='retirement-admin', email='retirement-admin@example.test', role='admin')
        self.staff = users.objects.create_user(username='retirement-staff', email='retirement-staff@example.test', role='staff')
        category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(name='Milk', category=category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='RETIREMENT',
            initial_quantity=100, remaining_quantity=100,
            expiration_date=timezone.localdate() + timedelta(days=7))
        self.customer = Customer.objects.create(name='Buyer', created_by=self.admin)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def payload(self):
        return {'request_id': str(uuid4()), 'customer_id': self.customer.pk,
            'transaction_date': timezone.localdate().isoformat(), 'payment_method': 'online',
            'payment_status': 'paid', 'items': [
                {'product_id': self.product.pk, 'quantity': '2', 'unit_price': '50'}]}

    def assert_no_discount_fields(self, value):
        if isinstance(value, dict):
            self.assertFalse(any('discount' in key.lower() for key in value), list(value))
            for item in value.values():
                self.assert_no_discount_fields(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.assert_no_discount_fields(item)

    def test_all_sale_routes_reject_item_and_header_discounts_before_writing(self):
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            for endpoint in ('checkout', 'orders', 'encoding'):
                for at_item in (False, True):
                    for values in ({'discount_type': 'percent', 'discount_value': '10'},
                                   {'discount_type': 'fixed', 'discount_value': '5'},
                                   {'discount_type': 'percent', 'discount_value': '0'},
                                   {'discount_value': '1'}, {'discount_amount': '1'}):
                        cache.clear()
                        payload = self.payload()
                        target = payload['items'][0] if at_item else payload
                        target.update(values)
                        with self.subTest(user=user.role, endpoint=endpoint, item=at_item, values=values):
                            response = self.client.post(f'/sales/{endpoint}/', payload, format='json')
                            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(SaleSubmission.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 100)

    def test_service_callers_cannot_bypass_discount_retirement(self):
        items = [(self.product, Decimal('2'), Decimal('50'), 'percent', Decimal('10'))]
        for service, args in ((SalesService.checkout, (items, self.admin)),
                              (SalesService.place_order, (self.customer, items, self.admin))):
            with self.assertRaisesRegex(ValueError, 'no longer supported'):
                service(*args, payment_method='online')
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_neutral_old_payload_defaults_are_accepted_but_never_exposed(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            payload = self.payload()
            payload.update(discount_type='none', discount_value='0.00')
            payload['items'][0].update(discount_type='none', discount_value='0.00')
            response = self.client.post(f'/sales/{endpoint}/', payload, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assert_no_discount_fields(response.data)
            sale = response.data.get('transaction', response.data)
            self.assertEqual(sale['total_amount'], '100.00')
            stored = Transaction.objects.get(pk=sale['id'])
            self.assertEqual(stored.discount_amount, 0)
            self.assertEqual(stored.items.get().discount_amount, 0)

    def legacy_sale(self):
        sale = Transaction.objects.create(handled_by=self.admin, customer=self.customer,
            subtotal=100, discount_type='fixed', discount_value=10, discount_amount=10,
            total_amount=90, payment_method='online', payment_status='pending', encoded_at=timezone.now())
        item = TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
            quantity=2, unit_price=50, subtotal=100, discount_type='fixed', discount_value=10,
            discount_amount=10, total_amount=90)
        order = Order.objects.create(customer=self.customer, handled_by=self.admin, transaction=sale,
            discount_type='fixed', discount_value=10)
        OrderItem.objects.create(order=order, product=self.product, quantity=2, unit_price=50,
            subtotal=100, discount_type='fixed', discount_value=10, discount_amount=10, total_amount=90)
        return sale, item, order

    def test_history_and_reports_hide_discounts_and_preserve_every_legacy_row(self):
        sale, item, order = self.legacy_sale()
        models = (Transaction, TransactionItem, Order, OrderItem, ProductBatch)
        before = {model: list(model.objects.order_by('pk').values()) for model in models}
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            for url in ('/sales/transactions/', f'/sales/transactions/{sale.pk}/',
                        '/sales/encoding/', f'/sales/encoding/{sale.pk}/',
                        '/sales/orders/', f'/sales/orders/{order.pk}/',
                        f'/sales/customers/{self.customer.pk}/transactions/'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, (url, response.data))
                self.assert_no_discount_fields(response.data)
            for kind in ('daily_sales', 'weekly_sales', 'monthly_sales'):
                response = self.client.get('/api/reports/preview/', {'type': kind})
                self.assertEqual(response.status_code, 200, response.data)
                data = response.data['data']
                self.assert_no_discount_fields(data)
                self.assertEqual(Decimal(data.get('total_revenue', data.get('revenue'))), 90)
                self.assertEqual(data['product_revenue_basis'], 'recorded_line_amounts')
                self.assertEqual(Decimal(data['payment_mix'][0]['revenue']), 90)
                report = Report(kind, data)
                report.sales()
                def text(part):
                    if hasattr(part, 'text'):
                        return part.text
                    if hasattr(part, '_cellvalues'):
                        return text(part._cellvalues)
                    if isinstance(part, (list, tuple)):
                        return ' '.join(text(value) for value in part)
                    return ''
                self.assertNotIn('discount', text(report.story).lower())
                self.assertTrue(Report(kind, data).render().read().startswith(b'%PDF'))
        after = {model: list(model.objects.order_by('pk').values()) for model in models}
        self.assertEqual(before, after)

    def test_legacy_pending_payment_uses_original_total_and_keeps_stored_discount(self):
        sale, item, order = self.legacy_sale()
        response = self.client.patch(f'/sales/encoding/{sale.pk}/payment/',
            {'payment_status': 'paid', 'payment_method': 'cash', 'amount_tendered': '100'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total_amount'], '90.00')
        self.assertEqual(response.data['change_due'], '10.00')
        self.assert_no_discount_fields(response.data)
        sale.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount), (100, 10, 90))
        self.assertEqual(item.discount_amount, 10)

    def test_pre_removal_ordinary_checkout_uuid_still_replays_original_receipt(self):
        sale = SalesService.checkout([(self.product, Decimal('2'), Decimal('50'))],
            self.admin, payment_method='online', customer=self.customer)
        payload = self.payload()
        key = payload['request_id']
        old_payload = {'endpoint': 'checkout', 'customer_id': self.customer.pk,
            'payment_method': 'online', 'amount_tendered': None,
            'items': [(self.product.pk, Decimal('2.00'), Decimal('50.00'), 'none', Decimal('0.00'))]}
        SaleSubmission.objects.create(user=self.admin, request_id=key, endpoint='checkout', transaction=sale,
            fingerprint=sha256(json.dumps(old_payload, sort_keys=True, default=str).encode()).hexdigest())
        response = self.client.post('/sales/checkout/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], sale.pk)
        self.assertEqual(Transaction.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 98)
