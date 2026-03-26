import uuid

from django.db.models import BigAutoField, UUIDField


class UUIDAutoField(UUIDField, BigAutoField):
    """UUID-based auto field for use as an app's default_auto_field.

    Inherits from BigAutoField so that Django's issubclass(cls, AutoField) check
    passes (via AutoFieldMeta). UUIDField is listed first so its get_internal_type,
    db_type, and get_prep_value take precedence over BigAutoField's integer behavior.

    UUID is preferred as the default PK type because it supports federated data
    merging: independent CRM instances run by different groups can share and merge
    records without integer PK collisions.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default", uuid.uuid4)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)
