from django.apps import AppConfig


class MobileConfig(AppConfig):
    name = 'apps.mobile'

    def ready(self):
        import apps.mobile.signals  # noqa
