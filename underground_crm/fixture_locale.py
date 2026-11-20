"""
Lets the rows of multi-table Page subclasses be loaded before Wagtail's locales.

Wagtail's set_locale_on_new_instance (a pre_save receiver) gives a raw-loaded
translatable object without a locale the default Locale, which it looks up in
the database. When a fixture, or the snapshot of the database that a test with
serialized_rollback restores, holds a row of a page subclass such as FormPage,
that row is loaded on its own, as an instance of the subclass carrying only its
own table's fields, so its locale_id is None. Django restores such a snapshot in
INSTALLED_APPS order, and underground_crm precedes Wagtail, so the subclass's
row arrives before any Locale exists and the lookup fails.

A raw save writes nothing but the model's own table, though, and the locale is
stored in the parent wagtailcore_page row, which is loaded separately with a
locale of its own. The lookup is therefore pointless for such a row, and is
skipped; every other case is handed to Wagtail's receiver unchanged.
"""

from typing import Any

from django.db.models.signals import pre_save
from wagtail.models.i18n import set_locale_on_new_instance


def _owns_locale_field(model: type) -> bool:
    return any(field.name == "locale" for field in model._meta.local_fields)


def set_locale_unless_loading_a_subclass_row(sender: type, instance: Any, **kwargs: Any) -> None:
    if kwargs.get("raw") and instance._meta.parents and not _owns_locale_field(type(instance)):
        return
    set_locale_on_new_instance(sender, instance, **kwargs)


def narrow_wagtail_locale_receiver() -> None:
    if pre_save.disconnect(set_locale_on_new_instance):
        pre_save.connect(set_locale_unless_loading_a_subclass_row)
