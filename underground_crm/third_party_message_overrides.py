"""
Messages from Django and allauth whose wording this project replaces.

We ask visitors politely, so "Enter a valid date." is displayed as "Please enter
a valid date." The replacement wording lives where all our other strings do, as
the msgstr of the entry in locale/en/LC_MESSAGES/django.po. Those entries work
because the catalogs of installed apps take priority over Django's own, and
because underground_crm comes before allauth in INSTALLED_APPS.

This file exists only to keep those entries alive. `makemessages` scans this
project's source rather than installed packages, and it comments out any catalog
entry whose msgid it cannot find, with `#~`. Compiling skips commented entries, so
without this list every regeneration would silently restore the original wording.
Merely mentioning each string here is what `makemessages` needs to find it.

Nothing imports this module at runtime, apart from the test that checks each
message really is replaced. gettext_noop marks a string for extraction and returns
it untouched: Django and allauth do the actual translating when they display it.

To replace another message, copy its msgid here exactly as the package defines it,
regenerate the catalog, and fill in its msgstr (see "Translations" in the README).
"""

from django.utils.translation import gettext_noop as _

THIRD_PARTY_MESSAGES: tuple[str, ...] = (
    # django/forms/fields.py
    _("This field is required."),
    _("Enter a valid date."),
    _("Enter a valid time."),
    _("Enter a valid date/time."),
    _("Enter a valid URL."),
    _("Enter a number."),
    _("Enter a whole number."),
    _("No file was submitted."),
    # django/core/validators.py
    _("Enter a valid email address."),
    _("Enter a valid integer."),
    _("Ensure this value is less than or equal to %(limit_value)s."),
    _("Ensure this value is greater than or equal to %(limit_value)s."),
    # allauth/account/forms.py
    _("You must type the same password each time."),
    _("You must type the same email each time."),
)
