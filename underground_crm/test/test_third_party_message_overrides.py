from django import forms
from django.conf import settings
from django.test import SimpleTestCase
from django.utils import translation

from underground_crm.third_party_message_overrides import THIRD_PARTY_MESSAGES

POLITE_INVALID_DATE_MESSAGE = "Please enter a valid date."
POLITE_REQUIRED_FIELD_MESSAGE = "Please fill in this field."
UNPARSEABLE_DATE = "the first Tuesday after the election"


class ThirdPartyMessageOverridesTest(SimpleTestCase):
    """These read the compiled django.mo, so they also fail if the catalog was
    changed without being compiled."""

    def test_every_listed_message_is_replaced_in_the_site_language(self) -> None:
        # The site's language is an Australian variant, whereas the catalog is
        # filed under plain "en", so this also proves that the one reaches the other.
        with translation.override(settings.LANGUAGE_CODE):
            for message in THIRD_PARTY_MESSAGES:
                with self.subTest(message):
                    self.assertNotEqual(
                        translation.gettext(message),
                        message,
                        "This message is listed for replacement, so its catalog entry "
                        "must be active, with a msgstr, and compiled into django.mo.",
                    )

    def test_django_form_errors_use_the_replacement_wording(self) -> None:
        with translation.override(settings.LANGUAGE_CODE):
            with self.assertRaises(forms.ValidationError) as invalid:
                forms.DateField().clean(UNPARSEABLE_DATE)
            with self.assertRaises(forms.ValidationError) as missing:
                forms.CharField().clean("")

            self.assertEqual(invalid.exception.messages, [POLITE_INVALID_DATE_MESSAGE])
            self.assertEqual(missing.exception.messages, [POLITE_REQUIRED_FIELD_MESSAGE])
