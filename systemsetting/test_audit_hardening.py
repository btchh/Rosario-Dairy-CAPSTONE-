"""Regressions for concurrent runtime-settings cache population."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.core.cache import cache
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings

from systemsetting.models import SystemSettings
from systemsetting.runtime import get_runtime_settings


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RuntimeSettingsCacheRaceTests(TransactionTestCase):
    def test_initial_singleton_creation_stays_cached_after_commit(self):
        cache.clear()
        first = get_runtime_settings()
        with self.assertNumQueries(0):
            self.assertEqual(get_runtime_settings(), first)

    def test_late_old_reader_cannot_overwrite_newly_committed_preferences(self):
        config = SystemSettings.get_config()
        config.business_name = 'Before update'
        config.save()
        cache.clear()
        read_finished, commit_reader = Event(), Event()

        def read_old_snapshot():
            try:
                with transaction.atomic():
                    value = get_runtime_settings()['business_name']
                    read_finished.set()
                    if not commit_reader.wait(timeout=10):
                        raise AssertionError('Reader was not released')
                return value
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(read_old_snapshot)
            try:
                self.assertTrue(read_finished.wait(timeout=10))
                config.business_name = 'After update'
                config.save()
                self.assertEqual(get_runtime_settings()['business_name'], 'After update')
            finally:
                commit_reader.set()
            self.assertEqual(reader.result(timeout=10), 'Before update')
        self.assertEqual(get_runtime_settings()['business_name'], 'After update')
