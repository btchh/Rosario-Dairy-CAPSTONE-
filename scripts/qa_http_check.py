#!/usr/bin/env python3
"""Destructive HTTP QA for a disposable database, never the working stack.

Run inside the isolated QA API container with ROSARIO_QA_ALLOW_WRITES set to
isolated-qa-only and DB_NAME=rosario_full_qa. Uses real HTTP, authentication,
database assertions and concurrent requests. Outputs no tokens or response data.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import gzip
import http.client
import json
import os
from pathlib import Path
import secrets
from threading import Barrier
import time
from urllib.parse import urlsplit
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://gateway:8080')
    parser.add_argument('--output', default='/tmp/qa-http-result.json')
    parser.add_argument('--tokens-output', default='/tmp/qa-read-tokens.json')
    args = parser.parse_args()
    if os.getenv('ROSARIO_QA_ALLOW_WRITES') != 'isolated-qa-only' or os.getenv('DB_NAME') != 'rosario_full_qa':
        parser.error('Requires the explicitly enabled disposable rosario_full_qa database.')
    origin = urlsplit(args.base_url)
    if origin.scheme != 'http' or origin.hostname not in {'gateway', 'frontend'} or origin.path not in {'', '/'}:
        parser.error('QA requests must target the private gateway or frontend service.')

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connection, close_old_connections
    from django.utils import timezone
    from rest_framework_simplejwt.tokens import AccessToken
    from inventory.models import Category, Product, ProductBatch, StockAdjustment, StockCount
    from sales.models import Customer, Order, Transaction, SaleSubmission, StockReturn

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        assert cursor.fetchone() == ('rosario_full_qa',), 'Wrong database; refusing QA writes.'
    run_id = uuid4().hex[:10]
    users = get_user_model()
    admins = [users.objects.create_user(username=f'qa-{run_id}-admin-{index}',
        role='admin', email=f'qa-{run_id}-admin-{index}@example.test') for index in range(20)]
    staff = [users.objects.create_user(username=f'qa-{run_id}-staff-{index}',
        role='staff', email=f'qa-{run_id}-staff-{index}@example.test') for index in range(10)]
    def token(user):
        value = AccessToken.for_user(user)
        value['token_version'] = user.token_version
        return str(value)
    admin_tokens, staff_tokens = list(map(token, admins)), list(map(token, staff))
    category = Category.objects.create(name='QA ' + run_id)
    customer = Customer.objects.create(name='QA customer ' + run_id, created_by=admins[0])
    def product(label, stock=10):
        item = Product.objects.create(name=f'QA {label} {run_id}', category=category, unit='L', shelf_life=7)
        batch = ProductBatch.objects.create(product=item, batch_number=f'QA-{run_id}-{label}',
            initial_quantity=stock, remaining_quantity=stock, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))
        return item, batch
    item, batch = product('validation', 100)
    results, phases = [], []
    def request(method, path, payload=None, auth=None, raw=None, content_type='application/json'):
        client = http.client.HTTPConnection(origin.hostname, origin.port, timeout=25)
        headers = {'Host': 'localhost', 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
        if auth:
            headers['Authorization'] = 'Bearer ' + auth
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        if data is not None:
            headers['Content-Type'] = content_type
        try:
            client.request(method, path, body=data, headers=headers)
            response = client.getresponse()
            body = response.read(16 * 1024 * 1024 + 1)
            assert len(body) <= 16 * 1024 * 1024, 'Oversized response'
            if response.getheader('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            try:
                value = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                value = None
            return response.status, value, response.getheader('Content-Type', ''), response.getheader('Retry-After')
        finally:
            client.close()
    def check(name, method, path, payload=None, expected=(400,), auth=None, **kwargs):
        status, data, content_type, _ = request(method, path, payload, auth, **kwargs)
        passed = status in expected and content_type.startswith('application/json')
        results.append({'case': name, 'status': status, 'expected': list(expected), 'passed': passed})
        return status, data
    def snapshot():
        return {'counts': [model.objects.count() for model in (users, Category, Product, ProductBatch,
            Customer, Order, Transaction, SaleSubmission, StockAdjustment, StockCount, StockReturn)],
            'stock': list(ProductBatch.objects.order_by('pk').values_list('pk', 'remaining_quantity', 'status'))}
    def phase(name, function):
        started = time.monotonic()
        try:
            details = function()
            phases.append({'phase': name, 'passed': True, 'seconds': round(time.monotonic() - started, 3), **(details or {})})
        except Exception as exc:
            # Exception text from HTTP/database libraries can contain private
            # data. Only our explicit assertion messages belong in the report.
            phases.append({'phase': name, 'passed': False,
                'error': str(exc)[:200] if isinstance(exc, AssertionError) else type(exc).__name__})
    def cart(chosen=item, **extra):
        return {'items': [{'product_id': chosen.pk, 'quantity': '1', 'unit_price': '50'}],
                'payment_method': 'online', **extra}

    def malformed():
        before = snapshot()
        routes = ['/sales/checkout/', '/sales/orders/', '/sales/encoding/', '/sales/customers/',
            '/inventory/products/', '/inventory/categories/', '/inventory/product-batches/',
            '/inventory/stock-adjustments/', '/inventory/stock-counts/', '/accounts/register/',
            '/accounts/change-password/', '/accounts/email-change/', '/api/reports/refresh/']
        for index, path in enumerate(routes):
            for case_index, body in enumerate(([], [1], 'invalid', True, 1, None)):
                check('non-object:' + path, 'POST', path, raw=json.dumps(body).encode(),
                    auth=admin_tokens[(index + case_index) % len(admin_tokens)])
        for raw, content_type in [(b'{', 'application/json'), (b'\xff', 'application/json'),
                                   (b'{"items":NaN}', 'application/json'), (b'{}', 'text/plain')]:
            check('malformed-parser', 'POST', '/sales/checkout/', raw=raw, content_type=content_type,
                auth=admin_tokens[19], expected=(400, 415))
        assert snapshot() == before, 'Malformed bodies mutated business records.'
        return {'requests': len(routes) * 6 + 4, 'business_state': 'unchanged'}
    phase('malformed JSON and body types', malformed)

    def invalid_sales():
        before = snapshot()
        cases = []
        for field, values in {
            'product_id': [True, [], {}, -1, 0, '1.5', 2**63, 'x' * 1000],
            'quantity': [True, None, [], {}, 0, -1, 'NaN', 'Infinity', '1e1000', '0.001', '1000000000000'],
            'unit_price': [True, [], {}, -1, 'NaN', 'Infinity', '1e1000', '0.001'],
        }.items():
            for value in values:
                payload = cart(); payload['items'][0][field] = value
                cases.append((field, payload))
        for field, values in {'customer_id': [True, [], {}, 'bad', '1.1', 2**63],
            'amount_tendered': [True, [], {}, '-1', 'NaN', '1e1000'],
            'payment_method': ['', 'crypto', [], {}], 'request_id': ['', 'bad', True, {}, []]}.items():
            for value in values:
                cases.append((field, cart(**{field: value})))
        for value in ([], [None], [1], [{}], 'bad', {}, None):
            cases.append(('items', {**cart(), 'items': value}))
        for index, (field, payload) in enumerate(cases):
            check('invalid-sale:' + field, 'POST', '/sales/checkout/', payload,
                auth=admin_tokens[index % len(admin_tokens)])
        assert snapshot() == before, 'Invalid sale values mutated business records.'
        return {'requests': len(cases), 'business_state': 'unchanged'}
    phase('invalid sale values and decimal boundaries', invalid_sales)

    def invalid_reads():
        before = snapshot()
        paths = ['/sales/transactions/?handled_by=true', '/sales/transactions/?customer_id=-1',
            '/sales/transactions/?customer_id=9223372036854775808',
            '/sales/transactions/?start_date=2026-02-30', '/sales/transactions/?start_date=99999-01-01',
            '/sales/transactions/?start_date=2026-12-01&end_date=2026-01-01',
            '/sales/transactions/?payment_method=crypto', '/sales/orders/?customer_id=1.5',
            '/sales/reports/best-sellers/?limit=-1', '/sales/reports/best-sellers/?limit=not-a-number',
            '/api/reports/preview/?type=daily_sales&start_date=invalid']
        for path in paths:
            check('invalid-filter:' + path.split('?')[0], 'GET', path, auth=admin_tokens[0])
        for path in ['/inventory/products/bad/reactivate/', '/inventory/categories/bad/reactivate/',
                     '/inventory/product-batches/9223372036854775808/', '/sales/transactions/-1/']:
            method = 'POST' if 'reactivate' in path else 'GET'
            check('invalid-path-id', method, path, {} if method == 'POST' else None,
                expected=(404,), auth=admin_tokens[0])
        check('overlong-customer', 'POST', '/sales/customers/', {'name': 'x' * 256}, auth=admin_tokens[0])
        assert snapshot() == before, 'Invalid filters or identifiers mutated business records.'
    phase('filters, identifiers and text length', invalid_reads)

    def permissions():
        hidden = Category.objects.create(name='QA hidden ' + run_id, is_visible_to_staff=False)
        hidden_product = Product.objects.create(name='QA hidden milk ' + run_id, category=hidden, unit='L', shelf_life=7)
        ProductBatch.objects.create(product=hidden_product, batch_number='QA-hidden-' + run_id,
            initial_quantity=10, remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate()+timedelta(days=7))
        before = snapshot()
        for path in ['/sales/transactions/', '/inventory/products/', '/accounts/user/', '/events/']:
            check('anonymous-read', 'GET', path, expected=(401,))
            check('invalid-jwt', 'GET', path, auth='invalid.jwt.value', expected=(401,))
        for path, method, payload in [('/inventory/products/', 'POST', {}),
            (f'/inventory/product-batches/{batch.pk}/', 'PATCH', {'unit_price': '1'}),
            ('/accounts/register/', 'POST', {}), ('/settings/inventory/', 'PATCH', {})]:
            check('staff-admin-boundary', method, path, payload, expected=(403,), auth=staff_tokens[0])
        check('hidden-product-checkout', 'POST', '/sales/checkout/', cart(hidden_product), auth=staff_tokens[0])
        status, items = check('staff-visible-catalog', 'GET', '/inventory/products/', expected=(200,), auth=staff_tokens[0])
        assert status == 200 and hidden_product.pk not in {row['id'] for row in items}
        assert snapshot() == before, 'Permission denial mutated business records.'
    phase('authentication, permissions and hidden inventory', permissions)

    def atomic_cart():
        chosen, exhausted = product('atomic-rollback', 0)
        payload = cart(request_id=str(uuid4()))
        payload['items'].append({'product_id': chosen.pk, 'quantity': '1', 'unit_price': '50'})
        before = snapshot()
        check('multi-item-insufficient-stock', 'POST', '/sales/checkout/', payload, auth=staff_tokens[6])
        assert snapshot() == before, 'Failed multi-item checkout left partial writes.'
        assert not SaleSubmission.objects.filter(user=staff[6], request_id=payload['request_id']).exists()
        exhausted.initial_quantity = exhausted.remaining_quantity = 1
        exhausted.save(update_fields=['initial_quantity', 'remaining_quantity'])
        status, _, _, _ = request('POST', '/sales/checkout/', payload, staff_tokens[6])
        assert status == 201, 'Corrected stock could not reuse the failed submission UUID.'
        return {'failed_cart': 'rolled back completely', 'same_uuid_after_stock_correction': 201}
    phase('multi-item rollback and corrected retry', atomic_cart)

    def concurrent(payloads, path='/sales/checkout/', identities=None):
        barrier = Barrier(len(payloads))
        identities = identities or [staff_tokens[1]] * len(payloads)
        def one(pair):
            index, payload = pair
            barrier.wait()
            return request('POST', path, payload, identities[index])
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(one, enumerate(payloads)))
    def real_password_authentication():
        password = 'Qa9!' + secrets.token_urlsafe(24)
        identity = users.objects.create_user(username=f'qa-{run_id}-authentication',
            role='staff', email=f'qa-{run_id}-authentication@example.test', password=password)
        from django.contrib.auth.hashers import identify_hasher
        algorithm = identify_hasher(identity.password).algorithm
        assert algorithm != 'md5', 'HTTP QA must use the real application password hasher.'
        status, pair, _, _ = request('POST', '/accounts/login/',
            {'username': identity.username, 'password': password})
        assert status == 200, 'Password login failed.'
        assert request('GET', '/accounts/user/', auth=pair['access'])[0] == 200
        refreshes = concurrent([{'refresh': pair['refresh']}] * 2,
            '/accounts/refresh/', identities=[None, None])
        assert Counter(value[0] for value in refreshes) == {200: 1, 401: 1}, 'Refresh token rotated more than once.'
        rotated = next(value[1] for value in refreshes if value[0] == 200)
        assert rotated['refresh'] != pair['refresh']
        assert request('POST', '/accounts/logout/', {'refresh_token': rotated['refresh']}, rotated['access'])[0] == 200
        assert request('GET', '/accounts/user/', auth=rotated['access'])[0] == 401
        assert request('GET', '/accounts/user/', auth=pair['access'])[0] == 401
        assert request('POST', '/accounts/refresh/', {'refresh': rotated['refresh']})[0] == 401
        return {'password_hasher': algorithm, 'login': 200, 'concurrent_refresh_statuses': [200, 401],
            'logout': 200, 'old_and_new_access_after_logout': 401, 'refresh_after_logout': 401}
    phase('real password hashing, refresh rotation and logout revocation', real_password_authentication)

    def rejected_burst():
        before = snapshot()
        payloads = [cart(items=[]) for _ in range(10)]
        statuses = []
        for _ in range(10):
            statuses.extend(value[0] for value in concurrent(payloads, identities=staff_tokens))
        assert statuses == [400] * 100, 'Invalid concurrent writes did not all return 400.'
        assert snapshot() == before, 'Rejected burst changed records or stock.'
        return {'requests': 100, 'concurrent_clients': 10, 'statuses': dict(Counter(statuses)), 'business_state': 'unchanged'}
    phase('100 invalid writes across ten clients', rejected_burst)

    def idempotency():
        chosen, stock = product('idempotency', 10)
        before = Transaction.objects.count()
        payload = cart(chosen, request_id=str(uuid4()))
        responses = concurrent([deepcopy(payload) for _ in range(10)])
        statuses = [value[0] for value in responses]
        assert Counter(statuses) == {201: 1, 200: 9}, 'Retry statuses did not identify exactly one creation.'
        assert len({value[1]['id'] for value in responses}) == 1
        stock.refresh_from_db()
        assert stock.remaining_quantity == 9 and Transaction.objects.count() == before + 1
        changed = deepcopy(payload); changed['items'][0]['quantity'] = '2'
        check('conflicting-retry', 'POST', '/sales/checkout/', changed, expected=(409,), auth=staff_tokens[1])
        return {'requests': 10, 'statuses': dict(Counter(statuses)), 'receipts_created': 1, 'stock_deductions': 1}
    phase('ten simultaneous retries with one request UUID', idempotency)

    def order_idempotency():
        chosen, stock = product('order-idempotency', 10)
        before = Transaction.objects.count()
        payload = cart(chosen, customer_id=customer.pk, request_id=str(uuid4()))
        responses = concurrent([deepcopy(payload) for _ in range(10)], '/sales/orders/',
            identities=[staff_tokens[3]] * 10)
        statuses = [value[0] for value in responses]
        assert Counter(statuses) == {201: 1, 200: 9}, 'Order retries did not create exactly one order.'
        assert len({value[1]['id'] for value in responses}) == 1
        stock.refresh_from_db()
        assert stock.remaining_quantity == 9 and Transaction.objects.count() == before + 1
        return {'requests': 10, 'statuses': dict(Counter(statuses)), 'orders_created': 1, 'receipts_created': 1}
    phase('ten simultaneous order retries with one request UUID', order_idempotency)

    def overselling():
        chosen, stock = product('last-stock', 3)
        before = Transaction.objects.count()
        responses = concurrent([cart(chosen, request_id=str(uuid4())) for _ in range(10)], identities=staff_tokens)
        statuses = [value[0] for value in responses]
        assert Counter(statuses) == {201: 3, 400: 7}, 'Last-stock competition did not produce three sales.'
        stock.refresh_from_db()
        assert stock.remaining_quantity == 0 and Transaction.objects.count() == before + 3
        return {'requests': 10, 'statuses': dict(Counter(statuses)), 'receipts_created': 3, 'stock_remaining': '0'}
    phase('ten buyers competing for three units', overselling)

    def cancellations():
        chosen, stock = product('cancellation', 10)
        status, sale, _, _ = request('POST', '/sales/checkout/', cart(chosen), staff_tokens[2])
        assert status == 201
        path = f"/sales/transactions/{sale['id']}/cancel/"
        responses = concurrent([{'reason': 'Isolated QA cancellation'} for _ in range(10)], path,
            identities=[admin_tokens[18]] * 10)
        statuses = [value[0] for value in responses]
        assert Counter(statuses) == {200: 1, 400: 9}, 'Concurrent cancellation repeated stock return.'
        stock.refresh_from_db()
        assert stock.remaining_quantity == 10
        assert StockReturn.objects.filter(sale_item__transaction_id=sale['id']).count() == 1
        return {'requests': 10, 'statuses': dict(Counter(statuses)), 'stock_returns': 1}
    phase('ten simultaneous cancellation attempts', cancellations)

    def rate_limit():
        rate_user = users.objects.create_user(username=f'qa-{run_id}-rate',
            role='admin', email=f'qa-{run_id}-rate@example.test')
        rate_token = token(rate_user)
        before = snapshot()
        status_codes = []
        for _ in range(32):
            status, _, _, retry_after = request('POST', '/sales/checkout/', {'items': []}, rate_token)
            status_codes.append(status)
            if status == 429:
                assert retry_after and float(retry_after) > 0, 'Missing positive Retry-After.'
        assert status_codes == [400] * 30 + [429] * 2, 'Write quota was not enforced.'
        assert snapshot() == before
        return {'requests': 32, 'statuses': dict(Counter(status_codes)), 'retry_after': 'positive'}
    phase('write throttle and retry instructions', rate_limit)

    # Separate unused accounts keep subsequent read-load quotas independent.
    load_users = [users.objects.create_user(username=f'qa-{run_id}-load-{index}',
        role='admin', email=f'qa-{run_id}-load-{index}@example.test') for index in range(20)]
    os.umask(0o077)
    Path(args.tokens_output).write_text(json.dumps([token(user) for user in load_users]))
    result = {'run_id': run_id, 'database': 'disposable QA database',
        'passed': all(row['passed'] for row in results + phases),
        'http_cases': len(results), 'http_statuses': dict(Counter(row['status'] for row in results)),
        'unexpected_responses': [row for row in results if not row['passed']], 'phases': phases,
        'read_load_accounts': len(load_users)}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    close_old_connections()
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
