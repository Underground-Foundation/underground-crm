import uuid

from django.db.models import BigAutoField, UUIDField
from django.utils.functional import cached_property


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

    @cached_property
    def validators(self):
        # IntegerField.validators tries to look up integer range bounds by internal
        # type name, but get_internal_type() returns 'UUIDField', which is not in
        # that table. UUIDs have no numeric range, so return plain field validators.
        return list(self._validators)

    def _check_max_length_warning(self):
        # UUIDField.__init__ always sets max_length=32 for internal storage.
        # IntegerField._check_max_length_warning would flag this as an error,
        # but it is intentional here, not a user mistake.
        return []
