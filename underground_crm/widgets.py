from typing import Any

from django import forms
from django.urls import reverse_lazy

from underground_crm.addressr import MINIMUM_QUERY_LENGTH


class AddressAutocompleteInput(forms.TextInput):
    """
    A text input that offers Addressr-backed address suggestions while the
    visitor types.

    The companion script (see Media below) watches every input carrying the
    data-address-autocomplete attribute, and once the visitor has typed at
    least data-minimum-length characters, it fetches suggestions from
    data-suggestion-url and presents them through a native <datalist>.
    """

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        default_attrs: dict[str, Any] = {
            "data-address-autocomplete": "true",
            "data-suggestion-url": reverse_lazy("underground_crm:address_suggestions"),
            "data-minimum-length": MINIMUM_QUERY_LENGTH,
            # Suppress the browser's own history-based dropdown, which would
            # otherwise fight with the datalist suggestions.
            "autocomplete": "off",
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        js = ["underground_crm/js/address_autocomplete.js"]


class SameAsHomeAddressCheckbox(forms.CheckboxInput):
    """
    The "Same as home address" checkbox that accompanies each non-home address
    input on a registration form.

    The companion script (see Media below) collapses the address input named
    by data-same-as-home-controls while the checkbox is ticked, so a visitor
    only sees the extra input after declaring their address differs.
    """

    def __init__(self, controlled_field_id: str, attrs: dict[str, Any] | None = None) -> None:
        default_attrs: dict[str, Any] = {"data-same-as-home-controls": controlled_field_id}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        js = ["underground_crm/js/same_as_home_address.js"]
