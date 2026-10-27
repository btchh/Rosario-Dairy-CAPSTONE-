"""Read-only deployment checks, enabled by check --deploy --database default."""
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import DatabaseError, connections


@register(Tags.database, deploy=True)
def check_application_database_role(app_configs, databases=None, **kwargs):
    if not settings.IS_PRODUCTION or not databases or 'default' not in databases:
        return []
    database = connections['default']
    if database.vendor != 'postgresql':
        return []
    try:
        with database.cursor() as cursor:
            cursor.execute('''
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = current_user
            ''')
            privileges = cursor.fetchone()
    except DatabaseError:
        return [Error(
            'Could not verify the production application database role.',
            hint='Check connectivity and rerun check --deploy --database default.',
            id='config.E001',
        )]
    if privileges is None or any(privileges):
        return [Error(
            'The production database role has elevated privileges or could not be verified.',
            hint='Use a dedicated role with NOSUPERUSER NOCREATEDB NOCREATEROLE '
                 'NOREPLICATION NOBYPASSRLS. Use separate migration/backup credentials.',
            id='config.E002',
        )]
    return []
