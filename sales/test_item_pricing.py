from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, Transaction, TransactionItem
from sales.tests import make_user
from sales.services import SalesService


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
