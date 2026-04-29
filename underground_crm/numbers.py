from typing import Optional

from django.conf import settings
from django.utils import formats
from django.utils.translation import override
from decimal import Decimal, InvalidOperation


def parse_localized_number(value_str, locale=settings.LANGUAGE_CODE) -> Optional[Decimal]:
    """
    Parse a localized number string into a Decimal.

    Handles locale-specific thousands separators and decimal separators.
    E.g. "1.234,56" in "fr-FR" or "de-DE" → Decimal("1234.56")
         "1,234.56" in "en-AU" or "en-US" → Decimal("1234.56")
    """
    if value_str is None:
        return None

    value_str = value_str.strip()
    if not value_str:
        return None

    with override(locale):
        decimal_sep = formats.get_format("DECIMAL_SEPARATOR")
        thousand_sep = formats.get_format("THOUSAND_SEPARATOR")

    # Remove thousand separators, then normalize decimal separator to "."
    normalized = value_str.replace(thousand_sep, "").replace(decimal_sep, ".")

    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None
