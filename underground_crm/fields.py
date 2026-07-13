import uuid
from typing import Any, Optional

from django.db.models import BigAutoField, UUIDField
from django.utils.functional import cached_property
from wagtail.blocks.definition_lookup import BlockDefinitionLookupBuilder
from wagtail.fields import StreamField


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


class DeclaredBlocksStreamField(StreamField):
    """A StreamField whose migrations describe only the blocks declared in this library.

    Wagtail offers no hook for adding blocks to an existing page body, so a theme
    project extends one by writing into the field's `stream_block.child_blocks` at
    startup (fusion-underground does this in `FusionConfig.ready()`). Wagtail's
    StreamField.deconstruct() reads that very dictionary, so `makemigrations` run from
    a theme project sees the theme's blocks on this library's field, decides the field
    has changed, and writes the theme's block classes into a library migration. That
    migration then raises ModuleNotFoundError anywhere the theme is not installed —
    in this library's own CI, or in any other party's theme.

    Deconstructing from the declared block list instead makes the library's migrations
    independent of whatever is installed alongside it. Nothing is lost: a StreamField
    stores JSON, so the set of available blocks is a render-time concern with no
    bearing on the database schema.
    """

    def _declared_block_names(self) -> Optional[set[str]]:
        """The block names this field was declared with, or None when the field was
        given a single top-level block rather than a list of named children — in that
        case there is no child list for a theme to extend, so there is nothing to filter."""
        block_types: Any = self.block_types_arg
        if not isinstance(block_types, (list, tuple)):
            return None
        return {name for name, _block in block_types}

    def deconstruct(self) -> tuple[str, str, list, dict]:
        name, path, args, kwargs = super().deconstruct()
        declared_names = self._declared_block_names()
        if declared_names is None:
            return name, path, args, kwargs

        lookup = BlockDefinitionLookupBuilder()
        block_types = [
            (block_name, lookup.add_block(block))
            for block_name, block in self.stream_block.child_blocks.items()
            if block_name in declared_names
        ]
        kwargs["block_lookup"] = lookup.get_lookup_as_dict()
        return name, path, [block_types], kwargs
