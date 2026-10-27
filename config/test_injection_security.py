"""SQL injection and XSS probes over loopback HTTP and a disposable test database.

Run with Django's test runner, never as a script against a running business API.
Optional ROSARIO_PENTEST_EVIDENCE writes a JSONL request/result log, without JWTs.
"""
import json
import os
import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from accounts.models import PasswordResetChallenge
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Transaction


SQL_PAYLOADS = (
    "' OR '1'='1' --",
    "' UNION SELECT NULL --",
    "'; SELECT pg_sleep(0.05); --",
    '1 OR 1=1',
)
XSS_PAYLOADS = (
    '<script>window.__rosario_xss=1</script>',
    '"><svg onload=window.__rosario_xss=1>',
    '<img src=x onerror=window.__rosario_xss=1>',
    '</script><script>window.__rosario_xss=1</script>',
    'javascript:window.__rosario_xss=1',
)


class ExecutableMarkup(HTMLParser):
    """Find test markers in executable markup rather than escaped text."""
    def __init__(self):
        super().__init__()
        self.hits = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        self.in_script = tag == 'script'
        for key, value in attrs:
            executable = (key.startswith('on') or key == 'srcdoc'
                or (tag == 'script' and key == 'src')
                or (key in ('src', 'href') and (value or '').lstrip().lower().startswith(
                    ('javascript:', 'data:text/html'))))
            if value and '__rosario_xss' in value and executable:
                self.hits.append((tag, key, value))

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and '__rosario_xss' in data:
            self.hits.append(('script', data))


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                        'LOCATION': 'isolated-injection-http-tests'}},
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CORS_ALLOWED_ORIGINS=['http://localhost:5173'],
    CORS_ALLOWED_ORIGIN_REGEXES=[],
)
class InjectionHTTPSecurityTests(LiveServerTestCase):
    host = '127.0.0.1'

    def setUp(self):
        self.assertTrue(connection.settings_dict['NAME'].startswith('test_'),
                        'Injection probes require a disposable Django test database.')
        cache.clear()
        self.http = build_opener(ProxyHandler({}))
        users = get_user_model()
        self.admin = users.objects.create_user(username='pentest-admin',
            email='admin@example.test', password='Test-admin!497', role='admin')
        self.staff = users.objects.create_user(username='pentest-staff',
            email='staff@example.test', password='Test-staff!497', role='staff')
        self.category = Category.objects.create(name='Injection sentinel')
        self.product = Product.objects.create(name='Sentinel milk', category=self.category,
            unit='bottle', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='PENTEST-001',
            initial_quantity=10, remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))
        self.sentinel = Customer.objects.create(name='Unchanged sentinel', created_by=self.admin)
        self.admin_token = self.login('pentest-admin', 'Test-admin!497')
        self.staff_token = self.login('pentest-staff', 'Test-staff!497')

    def http_request(self, method, path, data=None, token=None, headers=None):
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if token:
            request_headers['Authorization'] = f'Bearer {token}'
        body = None
        if data is not None:
            body = json.dumps(data).encode('utf-8')
            request_headers['Content-Type'] = 'application/json'
        request = Request(self.live_server_url + path, data=body,
                          headers=request_headers, method=method)
        try:
            response = self.http.open(request, timeout=20)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            status = response.status
            response_headers = {key.title(): value for key, value in response.headers.items()}
        payload = json.loads(content) if 'application/json' in response_headers.get('Content-Type', '') else None
        evidence = os.environ.get('ROSARIO_PENTEST_EVIDENCE')
        if evidence:
            safe_data = {key: '<redacted>' if 'password' in key else value
                         for key, value in data.items()} if isinstance(data, dict) else data
            safe_payload = {key: '<redacted>' if key in ('access', 'refresh') else value
                            for key, value in payload.items()} if isinstance(payload, dict) else payload
            with Path(evidence).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'test': self._testMethodName, 'method': method,
                    'path': path, 'authenticated': bool(token), 'request': safe_data,
                    'status': status, 'headers': response_headers,
                    'response': safe_payload if payload is not None else content[:200].decode('utf-8', 'replace')}) + '\n')
        return status, response_headers, payload, content

    def login(self, username, password):
        status, _, payload, _ = self.http_request('POST', '/accounts/login/',
                                                {'username': username, 'password': password})
        self.assertEqual(status, 200, payload)
        return payload['access']

    def assert_sentinel_unchanged(self):
        self.sentinel.refresh_from_db()
        self.assertEqual(self.sentinel.name, 'Unchanged sentinel')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)
        self.assertFalse(Transaction.objects.exists())

    def test_sql_payloads_cannot_bypass_authentication(self):
        for payload in SQL_PAYLOADS:
            with self.subTest(payload=payload):
                status, _, data, _ = self.http_request('POST', '/accounts/login/',
                    {'username': payload, 'password': 'wrong-password'})
                self.assertEqual(status, 401, data)
                self.assertNotIn('access', data)
        self.assert_sentinel_unchanged()

    def test_sql_payloads_cannot_issue_or_consume_reset_challenges(self):
        identity = {'username': self.staff.username, 'email': self.staff.email}
        status, _, data, _ = self.http_request('POST', '/accounts/forgot-password/', identity)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(mail.outbox), 1)
        otp_match = re.search(r'code is: (\d{6})', mail.outbox[0].body)
        self.assertIsNotNone(otp_match)
        otp = otp_match.group(1)
        for attack in SQL_PAYLOADS:
            with self.subTest(attack=attack):
                cache.clear()
                injected = {**identity, 'username': attack}
                status, _, data, _ = self.http_request('POST', '/accounts/forgot-password/', injected)
                self.assertEqual(status, 200, data)
                status, _, data, _ = self.http_request('POST', '/accounts/reset-password/',
                    {**injected, 'otp': otp, 'new_password': 'Replacement!497'})
                self.assertEqual(status, 400, data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PasswordResetChallenge.objects.count(), 1)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('Test-staff!497'))
        self.assertEqual(self.staff.token_version, 0)
        self.assert_sentinel_unchanged()

    def test_sql_payloads_are_rejected_in_filters_and_report_options(self):
        queries = (
            ('/sales/transactions/', 'customer_id', {}),
            ('/sales/transactions/', 'handled_by', {}),
            ('/sales/transactions/', 'start_date', {}),
            ('/sales/orders/', 'customer_id', {}),
            ('/sales/reports/revenue/', 'period', {}),
            ('/sales/reports/best-sellers/', 'limit', {}),
            ('/api/reports/preview/', 'type', {}),
            ('/api/reports/preview/', 'start_date', {'type': 'daily_sales', 'end_date': '2020-01-01'}),
            ('/api/forecasting/chart/', 'period', {}),
            ('/api/forecasting/graph/', 'as_of', {}),
        )
        for endpoint, field, defaults in queries:
            for payload in SQL_PAYLOADS:
                with self.subTest(endpoint=endpoint, field=field, payload=payload):
                    # Keep each probe independent of prior cache/throttle history.
                    cache.clear()
                    path = endpoint + '?' + urlencode({**defaults, field: payload})
                    status, _, data, _ = self.http_request('GET', path, token=self.admin_token)
                    self.assertEqual(status, 400, data)
        self.assert_sentinel_unchanged()

    def test_sql_statements_in_customer_names_remain_literal_data(self):
        attack = f"O'Brien'; UPDATE sales_customer SET name='INJECTED' WHERE id={self.sentinel.pk}; -- probe"
        status, _, data, _ = self.http_request('POST', '/sales/customers/',
                                             {'name': attack}, self.staff_token)
        self.assertEqual(status, 201, data)
        customer_id = data['id']
        self.assertEqual(Customer.objects.get(pk=customer_id).name, attack)
        status, _, data, _ = self.http_request('GET', f'/sales/customers/{customer_id}/', token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(data['name'], attack)
        self.assert_sentinel_unchanged()

    def test_injected_ids_cannot_mutate_sales_or_stock(self):
        for payload in SQL_PAYLOADS:
            with self.subTest(payload=payload):
                cache.clear()
                checkout = {'items': [{'product_id': payload, 'quantity': '1', 'unit_price': '50'}],
                            'payment_method': 'cash', 'amount_tendered': '50'}
                status, _, data, _ = self.http_request('POST', '/sales/checkout/', checkout, self.staff_token)
                self.assertEqual(status, 400, data)
                status, _, data, _ = self.http_request('POST', '/inventory/stock-adjustments/',
                    {'product_batch_id': payload, 'adjustment_type': 'add', 'quantity': '1',
                     'unit_cost': '0', 'reason': 'Injection probe'}, self.staff_token)
                self.assertEqual(status, 400, data)
                status, _, _, _ = self.http_request('GET',
                    '/sales/customers/' + quote(payload, safe='') + '/', token=self.admin_token)
                self.assertEqual(status, 404)
        self.assert_sentinel_unchanged()

    def test_stored_xss_customer_payloads_are_json_and_escaped_html(self):
        for index, attack in enumerate(XSS_PAYLOADS):
            with self.subTest(attack=attack):
                status, headers, data, _ = self.http_request('POST', '/sales/customers/',
                    {'name': f'XSS fixture {index}', 'address': attack}, self.staff_token)
                self.assertEqual(status, 201, data)
                self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
                customer_id = data['id']
                self.assertEqual(Customer.objects.get(pk=customer_id).address, attack)
                status, headers, data, _ = self.http_request('GET', f'/sales/customers/{customer_id}/', token=self.admin_token)
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith('application/json'))
                self.assertEqual(data['address'], attack)
                status, headers, _, body = self.http_request('GET', f'/sales/customers/{customer_id}/',
                    token=self.admin_token, headers={'Accept': 'text/html'})
                self.assertEqual(status, 200)
                parser = ExecutableMarkup()
                parser.feed(body.decode('utf-8'))
                self.assertEqual(parser.hits, [])
                if '<' in attack:
                    self.assertIn(b'&lt;', body)
        self.assert_sentinel_unchanged()

    def test_stored_catalog_xss_does_not_create_active_pdf_content(self):
        attack = '<img src="file:///__rosario_xss" onerror="window.__rosario_xss=1">'
        status, _, data, _ = self.http_request('PATCH', f'/inventory/products/{self.product.pk}/',
                                             {'name': attack}, self.admin_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data['name'], attack)
        for kind in ('inventory', 'customer'):
            status, headers, _, content = self.http_request('GET',
                '/api/reports/export-pdf/?' + urlencode({'type': kind}), token=self.admin_token)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'application/pdf')
            self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
            self.assertTrue(content.startswith(b'%PDF'))
            for marker in (b'/JavaScript', b'/JS ', b'/URI', b'/Launch', b'file:///__rosario_xss'):
                self.assertNotIn(marker, content)

    def test_reflected_payloads_and_errors_do_not_render_executable_markup(self):
        for attack in XSS_PAYLOADS:
            for accept in ('application/json', 'text/html'):
                with self.subTest(attack=attack, accept=accept):
                    cache.clear()
                    path = '/api/reports/preview/?' + urlencode({'type': attack})
                    status, headers, _, content = self.http_request('GET', path,
                        token=self.admin_token, headers={'Accept': accept})
                    self.assertEqual(status, 400)
                    self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
                    if accept == 'text/html':
                        parser = ExecutableMarkup()
                        parser.feed(content.decode('utf-8'))
                        self.assertEqual(parser.hits, [])

    def test_injection_payloads_do_not_cross_auth_and_origin_boundaries(self):
        for path in ('/accounts/users/', '/inventory/products/', '/sales/customers/',
                     '/api/reports/preview/?type=inventory'):
            status, _, _, _ = self.http_request('GET', path)
            self.assertEqual(status, 401)
        for path in ('/accounts/users/', '/api/forecasting/graph/', '/settings/system/'):
            status, _, _, _ = self.http_request('GET', path, token=self.staff_token)
            self.assertEqual(status, 403)
        status, headers, _, _ = self.http_request('GET', '/inventory/products/',
            token=self.admin_token, headers={'Origin': 'https://attacker.invalid'})
        self.assertEqual(status, 200)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        status, headers, _, _ = self.http_request('GET', '/inventory/products/',
            token=self.admin_token, headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Access-Control-Allow-Origin'), 'http://localhost:5173')
        status, headers, _, _ = self.http_request('GET', '/inventory/products/',
            token='forged.' + 'x' * 20 + '.signature')
        self.assertEqual(status, 401)
