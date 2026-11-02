"""Staff history authorization holds for direct IDs, totals, exports and caches."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment
from reporting.services import generate_pdf, get_report
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from sales.visibility import visible_transactions
from systemsetting.notifications import live_notifications


class StaffHistoryOwnershipTests(APITestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.admin = users.objects.create_user(username='ownership-admin', email='ownership-admin@example.test', role='admin')
        self.alice = users.objects.create_user(username='ownership-alice', email='ownership-alice@example.test', role='staff')
        self.bob = users.objects.create_user(username='ownership-bob', email='ownership-bob@example.test', role='staff')
        self.category = Category.objects.create(name='Ownership dairy')
        self.product = Product.objects.create(name='Ownership milk', category=self.category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='OWNERSHIP',
            initial_quantity=100, remaining_quantity=97, expiration_date=timezone.localdate()+timedelta(days=7))
        self.records = {}
        for user, total in [(self.alice, '11.00'), (self.bob, '22.00'), (self.admin, '33.00')]:
            total = Decimal(total)
            customer = Customer.objects.create(name=f'{user.username} customer', created_by=user)
            sale = Transaction.objects.create(handled_by=user, customer=customer, subtotal=total,
                total_amount=total, payment_status='pending', encoded_at=timezone.now())
            TransactionItem.objects.create(transaction=sale, product_batch=self.batch, quantity=1,
                unit_price=total, subtotal=total, total_amount=total)
            order = Order.objects.create(handled_by=user, customer=customer, transaction=sale, status='fulfilled')
            OrderItem.objects.create(order=order, product=self.product, quantity=1, unit_price=total, subtotal=total)
            adjustment = StockAdjustment.objects.create(product_batch=self.batch, adjusted_by=user,
                adjustment_type='complimentary', quantity=1, unit_cost=2, reason=f'{user.username} private reason')
            self.records[user.pk] = (sale, order, adjustment)

    def get(self, user, path, params=None):
        self.client.force_authenticate(user)
        response = self.client.get(path, params or {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_lists_details_and_summaries_enforce_ownership_before_filters(self):
        for viewer in (self.alice, self.bob, self.admin):
            allowed = self.records if viewer.role == 'admin' else {viewer.pk: self.records[viewer.pk]}
            for resource, index, count_key in [('orders', 1, 'order_count'), ('transactions', 0, 'transaction_count'), ('encoding', 0, 'transaction_count')]:
                url = f'/sales/{resource}/'
                data = self.get(viewer, url, {'include_voided': 'true'})
                self.assertEqual({row['id'] for row in data['results']}, {record[index].pk for record in allowed.values()})
                summary = self.get(viewer, url+'summary/')
                self.assertEqual(summary[count_key], len(allowed))
                self.assertEqual(Decimal(summary['total_revenue']), sum((record[0].total_amount for record in allowed.values()), Decimal(0)))
                for owner_id, record in self.records.items():
                    response = self.client.get(url+str(record[index].pk)+'/')
                    self.assertEqual(response.status_code, 200 if owner_id in allowed else 404)
                if viewer.role == 'staff':
                    self.assertEqual(self.get(viewer, url, {'handled_by': self.admin.pk})['count'], 0)
                    self.assertEqual(self.get(viewer, url+'summary/', {'handled_by': self.admin.pk})[count_key], 0)

    def test_customer_history_and_totals_only_include_own_sales(self):
        for viewer in (self.alice, self.bob):
            for owner_id, (sale, _, _) in self.records.items():
                expected = 1 if owner_id == viewer.pk else 0
                url = f'/sales/customers/{sale.customer_id}/'
                history = self.get(viewer, url+'transactions/')
                customer = self.get(viewer, url)
                self.assertEqual(history['count'], expected)
                self.assertEqual(customer['transaction_count'], expected)
                self.assertEqual(Decimal(customer['total_spent']), sale.total_amount if expected else 0)

    def test_reports_and_warm_caches_are_per_staff_user(self):
        for viewer in (self.alice, self.bob, self.admin, self.alice):
            expected = Decimal('66.00') if viewer.role == 'admin' else self.records[viewer.pk][0].total_amount
            for kind in ('daily_sales', 'weekly_sales', 'monthly_sales', 'customer'):
                data = self.get(viewer, '/api/reports/preview/', {'type': kind})['data']
                field = 'total_revenue' if kind in ('daily_sales', 'customer') else 'revenue'
                self.assertEqual(Decimal(data[field]), expected)
                if kind == 'customer' and viewer.role == 'staff':
                    self.assertEqual({row['customer_id'] for row in data['customers']}, {self.records[viewer.pk][0].customer_id})

    def test_custom_report_ranges_keep_the_same_ownership(self):
        today = str(timezone.localdate())
        for viewer in (self.alice, self.bob):
            for kind in ('daily_sales', 'weekly_sales', 'monthly_sales', 'customer'):
                data = self.get(viewer, '/api/reports/preview/', {'type': kind, 'start_date': today, 'end_date': today})['data']
                field = 'total_revenue' if kind in ('daily_sales', 'customer') else 'revenue'
                self.assertEqual(Decimal(data[field]), self.records[viewer.pk][0].total_amount)

    def test_pdf_uses_the_requesting_staff_members_data(self):
        for viewer in (self.alice, self.bob):
            self.client.force_authenticate(viewer)
            with patch('reporting.views.generate_pdf', wraps=generate_pdf) as render:
                response = self.client.get('/api/reports/export-pdf/', {'type': 'daily_sales'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Decimal(render.call_args.args[1]['total_revenue']), self.records[viewer.pk][0].total_amount)
                self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))

    def test_order_notifications_do_not_disclose_other_handlers(self):
        for viewer in (self.alice, self.bob):
            notifications = [row for row in live_notifications(viewer) if row['title'] == 'Order Fulfilled']
            self.assertEqual({row['id'] for row in notifications}, {f'fulfilled-{self.records[viewer.pk][1].pk}'})

    def test_adjustment_history_and_direct_ids_are_owned(self):
        for viewer in (self.alice, self.bob):
            rows = self.get(viewer, '/inventory/stock-adjustments/')
            self.assertEqual({row['id'] for row in rows}, {self.records[viewer.pk][2].pk})
            response = self.client.get(f'/inventory/stock-adjustments/{self.records[self.admin.pk][2].pk}/')
            self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.get(self.admin, '/inventory/stock-adjustments/')), 3)

    def test_payment_updates_cannot_access_another_staff_members_encoding(self):
        from sales.services.payment_service import update_encoding_payment
        self.client.force_authenticate(self.alice)
        sale = self.records[self.bob.pk][0]
        response = self.client.patch(f'/sales/encoding/{sale.pk}/payment/', {'payment_status': 'paid'}, format='json')
        self.assertEqual(response.status_code, 404)
        with self.assertRaisesMessage(ValueError, 'unavailable'):
            update_encoding_payment(sale.pk, self.alice, payment_status='paid')
        sale.refresh_from_db()
        self.assertEqual(sale.payment_status, 'pending')

    def test_staff_with_no_own_activity_cannot_read_existing_history(self):
        viewer = get_user_model().objects.create_user(username='ownership-new-staff', role='staff')
        for resource, key in [('orders', 'order_count'), ('transactions', 'transaction_count'), ('encoding', 'transaction_count')]:
            self.assertEqual(self.get(viewer, f'/sales/{resource}/')['count'], 0)
            self.assertEqual(self.get(viewer, f'/sales/{resource}/summary/')[key], 0)

    def test_global_inventory_reports_are_admin_only_but_current_stock_is_shared(self):
        for viewer in (self.alice, self.bob):
            self.client.force_authenticate(viewer)
            for endpoint in ('preview', 'export-pdf'):
                response = self.client.get(f'/api/reports/{endpoint}/', {'type': 'inventory'})
                self.assertEqual(response.status_code, 403)
            stock = self.get(viewer, '/inventory/products/')
            self.assertEqual(Decimal(stock[0]['total_stock']), 97)
        self.get(self.admin, '/api/reports/preview/', {'type': 'inventory'})

    def test_mismatched_order_and_receipt_owners_cannot_leak_nested_history(self):
        order = Order.objects.create(handled_by=self.alice, customer=self.records[self.bob.pk][0].customer,
            transaction=Transaction.objects.create(handled_by=self.bob, total_amount=99), status='fulfilled')
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.get(f'/sales/orders/{order.pk}/').status_code, 404)

    def test_category_visibility_still_restricts_owned_history(self):
        self.category.is_visible_to_staff = False
        self.category.save()
        for viewer in (self.alice, self.bob):
            self.assertEqual(self.get(viewer, '/sales/orders/')['count'], 0)
            self.assertEqual(self.get(viewer, '/sales/transactions/')['count'], 0)

    def test_missing_staff_identity_fails_closed(self):
        self.assertFalse(visible_transactions(True).exists())
        with self.assertRaises(PermissionDenied):
            get_report('daily_sales', visible_to_staff=True)
