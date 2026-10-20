from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem
from sales.tests import make_user


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
