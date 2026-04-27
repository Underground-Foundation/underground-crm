from django.apps import AppConfig


class UndergroundCrmConfig(AppConfig):
    default_auto_field = "underground_crm.fields.UUIDAutoField"
    name = "underground_crm"
    verbose_name = "Underground CRM"

    def ready(self) -> None:
        import underground_crm.signals  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
