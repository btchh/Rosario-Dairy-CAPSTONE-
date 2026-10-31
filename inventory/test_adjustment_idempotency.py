"""Stock-out replay safety, including uncertain responses and concurrent retries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone

from config.test_support import APIClient, APITestCase, APITransactionTestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment, StockAdjustmentSubmission


class AdjustmentFixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='outflow-encoder', email='outflow@example.test', role='staff')
        self.client.force_authenticate(self.user)
        self.category = Category.objects.create(name='Outflow dairy')
        self.product = Product.objects.create(
            name='Outflow milk', category=self.category, unit='L', shelf_life=7)
        self.batch = self.make_batch('OUTFLOW-1')
        self.key = str(uuid4())

    def make_batch(self, number):
        return ProductBatch.objects.create(
            product=self.product, batch_number=number, initial_quantity=100,
            remaining_quantity=100, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def payload(self, **changes):
        return {'request_id': self.key, 'product_batch_id': self.batch.pk,
                'adjustment_type': 'complimentary', 'quantity': '5.00',
                'unit_cost': '50.00', 'reason': 'Customer courtesy', **changes}

    def post(self, payload=None, **headers):
        return self.client.post('/inventory/stock-adjustments/',
                                self.payload() if payload is None else payload,
                                format='json', **headers)


class AdjustmentIdempotencyTests(AdjustmentFixtures, APITestCase):
    @override_settings(CORS_ALLOWED_ORIGINS=['https://frontend.example.test'])
    def test_browser_preflight_allows_idempotency_header(self):
        response = self.client.options('/inventory/stock-adjustments/',
            HTTP_ORIGIN='https://frontend.example.test',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,idempotency-key')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://frontend.example.test')
        self.assertIn('idempotency-key', response['Access-Control-Allow-Headers'])

    def test_retry_returns_saved_response_and_deducts_once(self):
        first = self.post()
        cache.clear()  # Recovery must use the database, not process/cache state.
        retry = self.post()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data, retry.data)
        self.assertEqual(first.data['product_batch']['remaining_quantity'], '95.00')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 1)

    def test_retry_after_last_stock_disposal_returns_original_success(self):
        payload = self.payload(quantity='100', adjustment_type='spoilage')
        first = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'disposed')
        retry = self.post(payload)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data, retry.data)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_retry_preserves_response_after_other_stock_and_profile_changes(self):
        first = self.post()
        self.assertEqual(self.post(self.payload(request_id=str(uuid4()))).status_code, 201)
        self.user.username = 'renamed-encoder'
        self.user.save(update_fields=['username'])
        retry = self.post()
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.data, retry.data)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_expired_batch_disposal_can_be_replayed_after_disposal(self):
        self.batch.expiration_date = timezone.localdate() - timedelta(days=1)
        self.batch.status = 'expired'
        self.batch.save(update_fields=['expiration_date', 'status'])
        payload = self.payload(quantity='100', adjustment_type='expired')
        first = self.post(payload)
        retry = self.post(payload)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data, retry.data)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_header_only_and_matching_header_body_share_replay_identity(self):
        payload = self.payload()
        payload.pop('request_id')
        first = self.post(payload, HTTP_IDEMPOTENCY_KEY=self.key)
        self.assertEqual(first.status_code, 201, first.data)
        for payload, headers in ((self.payload(), {}),
                                 (self.payload(), {'HTTP_IDEMPOTENCY_KEY': self.key.upper()})):
            retry = self.post(payload, **headers)
            self.assertEqual(retry.status_code, 200, retry.data)
            self.assertEqual(first.data, retry.data)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_equivalent_decimal_and_id_representations_replay(self):
        first = self.post()
        retry = self.post(self.payload(product_batch_id=str(self.batch.pk), quantity=5, unit_cost='50'))
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(first.data, retry.data)

    def test_omitted_null_and_empty_reason_replay(self):
        payload = self.payload()
        payload.pop('reason')
        first = self.post(payload)
        for reason in (None, ''):
            retry = self.post(self.payload(reason=reason))
            self.assertEqual(retry.status_code, 200, retry.data)
            self.assertEqual(first.data, retry.data)

    def test_same_id_with_any_different_adjustment_field_returns_conflict(self):
        self.assertEqual(self.post().status_code, 201)
        second_batch = self.make_batch('OUTFLOW-2')
        changes = [{'product_batch_id': second_batch.pk}, {'quantity': '7'},
                   {'unit_cost': '51'}, {'reason': 'Different reason'},
                   {'adjustment_type': 'free_taste'}]
        for change in changes:
            with self.subTest(change=change):
                response = self.post(self.payload(**change))
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['detail'].code, 'idempotency_conflict')
        self.batch.refresh_from_db()
        second_batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('95.00'))
        self.assertEqual(second_batch.remaining_quantity, Decimal('100.00'))
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_invalid_body_or_header_ids_fail_before_mutation(self):
        for key in (None, '', 'invalid', 1, True, [], {}):
            with self.subTest(key=key):
                self.assertEqual(self.post(self.payload(request_id=key)).status_code, 400)
        for key in ('', 'invalid'):
            self.assertEqual(self.post(HTTP_IDEMPOTENCY_KEY=key).status_code, 400)
        self.assertEqual(self.post(HTTP_IDEMPOTENCY_KEY=str(uuid4())).status_code, 400)
        self.assertEqual(StockAdjustment.objects.count(), 0)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))

    def test_unkeyed_legacy_requests_remain_independent(self):
        payload = self.payload()
        payload.pop('request_id')
        self.assertEqual(self.post(payload).status_code, 201)
        self.assertEqual(self.post(payload).status_code, 201)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 0)
        self.assertEqual(StockAdjustment.objects.count(), 2)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_request_ids_are_scoped_to_authenticated_account(self):
        first = self.post()
        other = get_user_model().objects.create_user(
            username='other-outflow-encoder', email='other-outflow@example.test', role='admin')
        self.client.force_authenticate(other)
        second = self.post()
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data['id'], second.data['id'])
        self.assertEqual(second.data['adjusted_by']['id'], other.pk)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 2)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_validation_failure_does_not_consume_key(self):
        self.assertEqual(self.post(self.payload(quantity='101')).status_code, 400)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 0)
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 200)

    def test_submission_storage_failure_rolls_back_stock_and_audit_record(self):
        with patch('inventory.services.adjustment_submission_service.StockAdjustmentSubmission.objects.create',
                   side_effect=RuntimeError('Simulated storage failure')):
            with self.assertRaisesMessage(RuntimeError, 'Simulated storage failure'):
                self.post()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))
        self.assertEqual(StockAdjustment.objects.count(), 0)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 0)
        self.assertEqual(self.post().status_code, 201)

    def test_replay_rechecks_staff_visibility_and_authentication(self):
        self.assertEqual(self.post().status_code, 201)
        for obj, field in ((self.category, 'is_visible_to_staff'),
                           (self.category, 'is_active'), (self.product, 'is_active')):
            with self.subTest(field=field, model=type(obj).__name__):
                setattr(obj, field, False)
                obj.save(update_fields=[field])
                self.assertEqual(self.post().status_code, 400)
                setattr(obj, field, True)
                obj.save(update_fields=[field])
        self.client.force_authenticate(user=None)
        self.assertIn(self.post().status_code, (401, 403))
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_negative_correction_retries_add_stock_only_once(self):
        self.batch.remaining_quantity = 50
        self.batch.save(update_fields=['remaining_quantity'])
        payload = self.payload(adjustment_type='correction', quantity='-5')
        self.assertEqual(self.post(payload).status_code, 201)
        self.assertEqual(self.post(payload).status_code, 200)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('55.00'))


@skipUnless(connection.vendor == 'postgresql', 'Production request locking requires PostgreSQL.')
class ConcurrentAdjustmentIdempotencyTests(AdjustmentFixtures, APITransactionTestCase):
    def submit_concurrently(self, payloads):
        barrier = Barrier(len(payloads))

        def submit(payload):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                barrier.wait(timeout=10)
                response = client.post('/inventory/stock-adjustments/', payload, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(submit, payloads))

    def test_simultaneous_last_stock_requests_deduct_and_record_once(self):
        payload = self.payload(quantity='100', adjustment_type='spoilage')
        results = self.submit_concurrently([payload, payload])
        self.assertEqual(sorted(code for code, _ in results), [200, 201], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('0.00'))
        self.assertEqual(self.batch.status, 'disposed')

    def test_simultaneous_conflicting_requests_on_different_batches_commit_only_one(self):
        other_batch = self.make_batch('OUTFLOW-CONCURRENT-2')
        payloads = [self.payload(), self.payload(product_batch_id=other_batch.pk, quantity='7')]
        results = self.submit_concurrently(payloads)
        self.assertEqual(sorted(code for code, _ in results), [201, 409], results)
        winner = next(data for code, data in results if code == 201)
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 1)
        for batch in (self.batch, other_batch):
            batch.refresh_from_db()
            expected = Decimal('100.00')
            if batch.pk == winner['product_batch']['id']:
                expected -= Decimal(winner['quantity'])
            self.assertEqual(batch.remaining_quantity, expected)

    def test_distinct_requests_still_cannot_overdraw_the_same_batch(self):
        results = self.submit_concurrently([
            self.payload(quantity='90'), self.payload(request_id=str(uuid4()), quantity='90')])
        self.assertEqual(sorted(code for code, _ in results), [201, 400], results)
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(StockAdjustmentSubmission.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('10.00'))
