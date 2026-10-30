"""Block unsafe startup while keeping deliberate HSTS scope choices advisory."""
import os
from pathlib import Path
import sys


def check():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.core import checks
    from django.core.management import call_command

    # Enabling these requires an operator decision about every subdomain and
    # browser preload registration. Positive HSTS itself remains mandatory.
    advisory_ids = {'security.W005', 'security.W021'}
    issues = checks.run_checks(include_deployment_checks=True, databases=['default'])
    for issue in issues:
        print(str(issue), file=sys.stderr)
    if any(issue.level >= checks.WARNING and issue.id not in advisory_ids for issue in issues):
        raise RuntimeError('Production deployment checks failed.')
    call_command('migrate', check=True)
    print('Production preflight passed; HSTS scope advisories, if listed, require operator review.')


if __name__ == '__main__':
    try:
        check()
    except Exception:
        print('Production preflight failed. Review deployment checks and migration status.', file=sys.stderr)
        raise SystemExit(1)
