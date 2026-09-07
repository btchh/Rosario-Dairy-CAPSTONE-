from django.apps import AppConfig


class SystemsettingConfig(AppConfig):
    name = 'systemsetting'

    def ready(self):
        from django.db.models.signals import post_delete, post_save
        from .models import SystemSettings
        from .runtime import invalidate_runtime_settings

        post_save.connect(
            invalidate_runtime_settings,
            sender=SystemSettings,
            dispatch_uid='systemsetting.invalidate_runtime_on_save',
        )
        post_delete.connect(
            invalidate_runtime_settings,
            sender=SystemSettings,
            dispatch_uid='systemsetting.invalidate_runtime_on_delete',
        )
