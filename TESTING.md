# Backend tests

Each Django app keeps its tests in one `tests.py` file. The previous standalone
`test_*.py` modules have been consolidated into these files. Test class and
method names are retained so individual checks remain easy to locate.

| File | Coverage | Tests |
| --- | --- | ---: |
| `accounts/tests.py` | Accounts, authentication, recovery and permissions | 77 |
| `config/tests.py` | Cross-app regressions, shared caches and security/concurrency | 46 |
| `inventory/tests.py` | Catalog, product consolidation, batches, FEFO, counts, adjustments and non-sale outflows | 120 |
| `sales/tests.py` | Checkout, orders, manual pricing, encoding, payments, history and imports | 144 |
| `reporting/tests.py` | Report data, exports and PDFs | 16 |
| `forecasting/tests.py` | Sales input, forecast evaluation and serving | 29 |
| `systemsetting/tests.py` | Preferences, inventory thresholds and notification inbox | 38 |
| **Total** | | **470** |

## Run the suite

From the backend root, with the project virtual environment activated:

```bash
python manage.py test
```

Tests use Django's PostgreSQL test database. Database-locking and concurrent
request checks require PostgreSQL and separate database connections.

Run an app or individual class with its current location:

```bash
python manage.py test sales
python manage.py test sales.tests.StaffEncodingTests
python manage.py test sales.tests.EncodingPaymentTests
python manage.py test inventory.tests.InventoryOutflowTests
python manage.py test config.tests.DataCacheTests
```

Old labels such as `sales.test_encoding` and `config.test_data_cache` should be
replaced by the corresponding class in `sales.tests` or `config.tests`.

For faster local fixture creation, this command changes password hashing only
inside the test process:

```bash
python - <<'PY'
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from django.core.management import call_command

settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
call_command('test', interactive=False, verbosity=1)
PY
```

## Adding or updating tests

Place new classes in the relevant app's `tests.py`, beside the related behavior.
Use `TestCase` for normal database/API checks and `TransactionTestCase` for
checks that need real commits or concurrent connections. Keep shared fixtures
within the owning app's test module rather than importing another app's tests.

During consolidation, all 463 discovered test identities were compared by app,
class and method. The moved test-method bodies and assertions were preserved.
