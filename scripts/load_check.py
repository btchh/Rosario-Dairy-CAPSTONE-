#!/usr/bin/env python3
"""Authenticated HTTP read checks. Run against an isolated development stack.

Supply one access token per concurrent client in a private JSON array. Tokens
and response bodies are never written to the report. Only GET requests run.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import http.client
import json
import math
from pathlib import Path
from threading import Barrier
import time
from urllib.parse import urlsplit


DEFAULT_PATHS = (
    '/inventory/products/',
    '/sales/customers/',
    '/sales/transactions/?page_size=50',
    '/settings/',
    '/api/forecasting/graph/?period=monthly',
)


def percentile(values, fraction):
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] * 1000, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18000')
    parser.add_argument('--tokens-file', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=10)
    run_length = parser.add_mutually_exclusive_group()
    run_length.add_argument('--duration', type=float, default=None, help='Seconds per client.')
    run_length.add_argument('--requests-per-user', type=int, default=None)
    parser.add_argument('--think-time', type=float, default=0.5, help='Seconds between each client request.')
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--max-p95-ms', type=float, default=2000)
    parser.add_argument('--path', action='append', dest='paths')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.base_url)
    if (origin.scheme not in ('http', 'https') or not origin.hostname or origin.username
            or origin.password or origin.path not in ('', '/') or origin.query or origin.fragment):
        parser.error('--base-url must be an HTTP(S) origin without credentials or a path.')
    if (args.concurrency < 1 or args.concurrency > 100 or args.think_time < 0
            or args.timeout <= 0 or args.max_p95_ms <= 0
            or args.duration is not None and args.duration <= 0
            or args.requests_per_user is not None and args.requests_per_user < 1):
        parser.error('Use 1–100 clients, positive limits, and nonnegative think time.')
    tokens = json.loads(args.tokens_file.read_text())
    if (not isinstance(tokens, list) or len(tokens) < args.concurrency
            or any(not isinstance(t, str) or not t.strip() for t in tokens)):
        parser.error('The private token file must contain at least one access token per client.')
    paths = args.paths or DEFAULT_PATHS
    if any(not p.startswith('/') or p.startswith('//') or '\r' in p or '\n' in p for p in paths):
        parser.error('Each --path must be a relative API path beginning with a single slash.')
    duration = args.duration if args.duration is not None else (60 if args.requests_per_user is None else None)
    connection_class = http.client.HTTPSConnection if origin.scheme == 'https' else http.client.HTTPConnection
    barrier = Barrier(args.concurrency)

    def client(index):
        connection = connection_class(origin.hostname, origin.port, timeout=args.timeout)
        observations = []
        barrier.wait()
        deadline = time.monotonic() + duration if duration is not None else None
        sequence = 0
        try:
            while (deadline is not None and time.monotonic() < deadline
                   or deadline is None and sequence < args.requests_per_user):
                path = paths[sequence % len(paths)]
                started = time.monotonic()
                status, error, retry_after = 0, None, None
                try:
                    connection.request('GET', path, headers={
                        'Authorization': 'Bearer ' + tokens[index],
                        'Accept': 'application/json', 'Accept-Encoding': 'gzip',
                    })
                    response = connection.getresponse()
                    status = response.status
                    retry_after = response.getheader('Retry-After')
                    body = response.read(16 * 1024 * 1024 + 1)
                    if len(body) > 16 * 1024 * 1024:
                        raise ValueError('Response exceeded 16 MiB.')
                    if response.getheader('Content-Encoding') == 'gzip':
                        body = gzip.decompress(body)
                    if 'application/json' not in (response.getheader('Content-Type') or ''):
                        error = 'non_json_response'
                    else:
                        json.loads(body)
                except (OSError, http.client.HTTPException, ValueError, EOFError):
                    # Do not include exception text: it may contain private request details.
                    error = 'request_or_response_error'
                    connection.close()
                observations.append((path, status, time.monotonic() - started, error, retry_after))
                sequence += 1
                if args.think_time:
                    time.sleep(min(args.think_time, max(0, deadline-time.monotonic()))
                               if deadline is not None else args.think_time)
        finally:
            connection.close()
        return observations

    print(f'Running {args.concurrency} authenticated read clients against {args.base_url}.', flush=True)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        observations = [row for rows in pool.map(client, range(args.concurrency)) for row in rows]
    elapsed = time.monotonic() - started
    failures = [r for r in observations if r[1] != 200 or r[3]]
    latencies = [r[2] for r in observations]
    p95 = percentile(latencies, .95)
    endpoints = {}
    for path in paths:
        rows = [r for r in observations if r[0] == path]
        endpoints[path] = {
            'requests': len(rows), 'statuses': dict(Counter(str(r[1]) for r in rows)),
            'p95_ms': percentile([r[2] for r in rows], .95),
        }
    report = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'base_url': args.base_url, 'concurrent_clients': args.concurrency,
        'duration_seconds': round(elapsed, 2), 'think_time_seconds': args.think_time,
        'requests': len(observations), 'requests_per_second': round(len(observations)/elapsed, 2),
        'statuses': dict(Counter(str(r[1]) for r in observations)),
        'errors': dict(Counter(r[3] for r in observations if r[3])),
        'failed_requests': len(failures), 'p50_ms': percentile(latencies, .5),
        'p95_ms': p95, 'p99_ms': percentile(latencies, .99),
        'max_ms': round(max(latencies)*1000, 2) if latencies else None,
        'throttled_with_retry_after': sum(r[1] == 429 and bool(r[4]) for r in observations),
        'max_p95_ms': args.max_p95_ms, 'endpoints': endpoints,
        'passed': bool(observations) and not failures and p95 <= args.max_p95_ms,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
