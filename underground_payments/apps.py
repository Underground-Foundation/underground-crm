from django.apps import AppConfig


class UndergroundPaymentsConfig(AppConfig):
    default_auto_field = "underground_crm.fields.UUIDAutoField"
    name = "underground_payments"
    verbose_name = "Underground Payments"
