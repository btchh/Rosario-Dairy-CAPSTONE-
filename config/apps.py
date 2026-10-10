from django.apps import AppConfig


class BackendConfig(AppConfig):
    name = 'config'

    def ready(self):
        from .data_cache import connect_invalidation_signals
        connect_invalidation_signals()
