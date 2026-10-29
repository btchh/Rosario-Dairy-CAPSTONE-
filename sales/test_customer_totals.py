"""Customer lifetime sales totals preserve receipt values and visibility."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem


class CustomerTotalTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='total-admin', email='total-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(
            username='total-staff', email='total-staff@example.test', role='staff')
        self.customer = Customer.objects.create(name='Total Customer', created_by=self.admin)
        self.other = Customer.objects.create(name='Other Customer', created_by=self.admin)
        self.client.force_authenticate(self.admin)
        self.detail_url = f'/sales/customers/{self.customer.pk}/'
        self.history_url = self.detail_url + 'transactions/'

    def sale(self, amount, **kwargs):
        amount = Decimal(amount)
        values = {'customer': self.customer, 'handled_by': self.admin,
                  'subtotal': amount, 'total_amount': amount, **kwargs}
        return Transaction.objects.create(**values)

    def batch(self, name, **category_flags):
        category = Category.objects.create(name=name, **category_flags)
        product = Product.objects.create(name=name, category=category, unit='L', shelf_life=7)
        return ProductBatch.objects.create(
            product=product, batch_number=name, initial_quantity=100,
            remaining_quantity=100, unit_price=5,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def listed(self, **params):
        response = self.client.get('/sales/customers/', params)
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in response.data if row['id'] == self.customer.pk)

    def test_list_and_detail_sum_saved_net_totals_without_deduplicating_equal_amounts(self):
        self.sale('10.10', subtotal=Decimal('12.10'), discount_amount=Decimal('2.00'),
                  amount_tendered=Decimal('20.00'), payment_status='paid')
        self.sale('10.10', payment_status='paid')
        self.sale('0.20', payment_status='paid')
        self.sale('5.25', payment_status='pending')
        imported = self.sale('14.35', source_reference='historical-total',
                             source_invoice_number='SI-HISTORICAL', payment_status='unknown')
        Transaction.objects.filter(pk=imported.pk).update(
            created_at=timezone.now() - timedelta(days=800))
        self.sale('999.00', is_voided=True)
        self.sale('100.00', customer=self.other)
        self.sale('200.00', customer=None)
        for summary in (self.listed(), self.client.get(self.detail_url).data):
            self.assertEqual(summary['transaction_count'], 5)
            self.assertEqual(summary['total_spent'], '40.00')

    def test_customers_without_sales_or_with_only_voided_sales_return_decimal_zero(self):
        self.assertEqual(self.listed()['total_spent'], '0.00')
        self.sale('100.00', is_voided=True)
        for summary in (self.listed(), self.client.get(self.detail_url).data):
            self.assertEqual(summary['transaction_count'], 0)
            self.assertEqual(summary['total_spent'], '0.00')

    def test_total_includes_receipts_beyond_the_requested_history_page(self):
        Transaction.objects.bulk_create([
            Transaction(customer=self.customer, handled_by=self.admin,
                        subtotal=Decimal('0.10'), total_amount=Decimal('0.10'))
            for _ in range(51)
        ])
        history = self.client.get(self.history_url, {'page_size': 1})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data['count'], 51)
        self.assertEqual(len(history.data['results']), 1)
        self.assertIsNotNone(history.data['next'])
        self.assertEqual(self.listed()['total_spent'], '5.10')
        self.assertEqual(self.client.get(self.detail_url).data['total_spent'], '5.10')

    def test_staff_total_excludes_entire_hidden_inactive_and_mixed_receipts(self):
        visible_batch = self.batch('Total Visible')
        hidden_batch = self.batch('Total Hidden', is_visible_to_staff=False)
        inactive_batch = self.batch('Total Inactive', is_active=False)
        visible = self.sale('10.00')
        hidden = self.sale('100.00')
        mixed = self.sale('200.00')
        inactive = self.sale('300.00')
        for sale, batch in ((visible, visible_batch), (visible, visible_batch),
                            (hidden, hidden_batch), (mixed, visible_batch),
                            (mixed, hidden_batch), (inactive, inactive_batch)):
            TransactionItem.objects.create(transaction=sale, product_batch=batch,
                                           quantity=1, unit_price=5)
        self.assertEqual(self.listed()['total_spent'], '610.00')
        self.client.force_authenticate(self.staff)
        for summary in (self.listed(), self.client.get(self.detail_url).data):
            self.assertEqual(summary['transaction_count'], 1)
            self.assertEqual(summary['total_spent'], '10.00')

    def test_cached_total_refreshes_after_a_sale_and_its_cancellation(self):
        self.assertEqual(self.listed()['total_spent'], '0.00')
        self.assertEqual(self.client.get('/sales/customers/')['X-Data-Cache'], 'HIT')
        with self.capture_commit_callbacks(execute=True):
            sale = self.sale('34.56')
        self.assertEqual(self.listed()['total_spent'], '34.56')
        self.assertEqual(self.client.get('/sales/customers/')['X-Data-Cache'], 'HIT')
        with self.capture_commit_callbacks(execute=True):
            response = self.client.post(f'/sales/transactions/{sale.pk}/cancel/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.listed()['total_spent'], '0.00')
        self.assertEqual(self.listed()['transaction_count'], 0)

    def test_inactive_customers_retain_their_total(self):
        self.sale('17.00')
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active'])
        self.assertNotIn(self.customer.pk, [row['id'] for row in self.client.get('/sales/customers/').data])
        self.assertEqual(self.listed(include_inactive='true')['total_spent'], '17.00')
        self.assertEqual(self.client.get(self.detail_url).data['total_spent'], '17.00')

    def test_total_is_read_only_on_create_and_update(self):
        response = self.client.post('/sales/customers/', {
            'name': 'New Total Customer', 'total_spent': '999.99'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_spent'], '0.00')
        self.sale('21.30')
        response = self.client.patch(self.detail_url, {
            'name': 'Renamed Total Customer', 'total_spent': '999.99'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total_spent'], '21.30')

    def test_lifetime_total_can_exceed_a_single_receipts_amount_precision(self):
        self.sale('999999999999999999.99')
        self.sale('999999999999999999.99')
        self.assertEqual(self.listed()['total_spent'], '1999999999999999999.98')
