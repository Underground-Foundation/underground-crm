from django.apps import AppConfig


class UndergroundCrmConfig(AppConfig):
    default_auto_field = "underground_crm.fields.UUIDAutoField"
    name = "underground_crm"
    verbose_name = "Underground CRM"

    def ready(self) -> None:
        import underground_crm.signals  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
        from underground_crm.feed_routing import (
            patch_page_routing,
        )  # pylint: disable=import-outside-toplevel

        patch_page_routing()

        from underground_crm.fixture_locale import (  # pylint: disable=import-outside-toplevel
            narrow_wagtail_locale_receiver,
        )

        narrow_wagtail_locale_receiver()
