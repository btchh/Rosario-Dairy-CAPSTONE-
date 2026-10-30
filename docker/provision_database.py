"""Prepare a dedicated production database; never repurpose elevated roles.

Run only against a fresh database or one already owned by this migration role.
The administrator credentials exist only in this one-shot service, not Django.
"""
import os
import sys
from contextlib import closing

import psycopg2
from psycopg2 import sql


def provision(environment=None):
    environment = os.environ if environment is None else environment
    required = ['DB_NAME', 'POSTGRES_ADMIN_USER', 'POSTGRES_ADMIN_PASSWORD',
                'MIGRATION_DB_USER', 'MIGRATION_DB_PASSWORD', 'DB_USER', 'DB_PASSWORD']
    if any(not environment.get(key) for key in required):
        raise ValueError('Database provisioning settings are incomplete.')
    database = environment['DB_NAME']
    admin = environment['POSTGRES_ADMIN_USER']
    migration = environment['MIGRATION_DB_USER']
    runtime = environment['DB_USER']
    if len({admin, migration, runtime}) != 3:
        raise ValueError('Administrator, migration and runtime users must be distinct.')
    passwords = [environment[key] for key in ('POSTGRES_ADMIN_PASSWORD', 'MIGRATION_DB_PASSWORD', 'DB_PASSWORD')]
    if len(set(passwords)) != 3 or any(len(value) < 20 for value in passwords):
        raise ValueError('Use three distinct database passwords of at least 20 characters.')
    if any(len(value.encode()) > 63 for value in (database, admin, migration, runtime)):
        raise ValueError('Database and role names must fit PostgreSQL identifiers.')
    with closing(psycopg2.connect(host=environment.get('DB_HOST', 'db'),
                         port=environment.get('DB_PORT', '5432'), dbname=database,
                         user=admin, password=environment['POSTGRES_ADMIN_PASSWORD'],
                         connect_timeout=5)) as connection, connection:
        with connection.cursor() as cursor:
            for role, password in [(migration, environment['MIGRATION_DB_PASSWORD']),
                                   (runtime, environment['DB_PASSWORD'])]:
                cursor.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                               'FROM pg_roles WHERE rolname=%s', [role])
                existing = cursor.fetchone()
                if existing and any(existing):
                    raise ValueError('Refusing to reuse an elevated role; choose dedicated application roles.')
                cursor.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)', [role])
                if cursor.fetchone():
                    raise ValueError('Application roles must not inherit other database roles.')
                action = 'ALTER' if existing else 'CREATE'
                cursor.execute(sql.SQL(action + ' ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                    'NOREPLICATION NOBYPASSRLS PASSWORD %s').format(sql.Identifier(role)), [password])
            cursor.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                           "WHERE n.nspname='public' AND c.relkind IN ('r','p','S','v','m','f') "
                           'AND c.relowner<>(SELECT oid FROM pg_roles WHERE rolname=%s) LIMIT 1', [migration])
            if cursor.fetchone():
                raise ValueError('Existing database objects need an operator-reviewed ownership migration.')
            cursor.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC, {}, {}').format(
                sql.Identifier(database), sql.Identifier(migration), sql.Identifier(runtime)))
            for role, permissions in [(migration, 'CONNECT, TEMPORARY'), (runtime, 'CONNECT')]:
                cursor.execute(sql.SQL('GRANT ' + permissions + ' ON DATABASE {} TO {}').format(
                    sql.Identifier(database), sql.Identifier(role)))
            cursor.execute(sql.SQL('ALTER SCHEMA public OWNER TO {}').format(sql.Identifier(migration)))
            cursor.execute(sql.SQL('REVOKE ALL ON SCHEMA public FROM PUBLIC, {}').format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public '
                'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}').format(
                sql.Identifier(migration), sql.Identifier(runtime)))
            cursor.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public '
                'GRANT USAGE, SELECT ON SEQUENCES TO {}').format(
                sql.Identifier(migration), sql.Identifier(runtime)))


if __name__ == '__main__':
    try:
        provision()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('Database provisioning failed. Check dedicated roles, ownership and connectivity.', file=sys.stderr)
        raise SystemExit(1)
    print('Production database roles and grants are ready.')
