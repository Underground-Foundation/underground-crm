from typing import Any, NamedTuple

from django import forms
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from underground_crm.addressr import MINIMUM_QUERY_LENGTH


class AddressAutocompleteInput(forms.TextInput):
    """
    A text input that offers Addressr-backed address suggestions while the
    visitor types.

    The companion script (see Media below) watches every input carrying the
    data-address-autocomplete attribute, and once the visitor has typed at
    least data-minimum-length characters, it fetches suggestions from
    data-suggestion-url and presents them through a native <datalist>.

    On its own the widget collects a single line of text. As the line1
    component of a StructuredAddressWidget, picking a suggestion instead
    spreads the address across the group's component inputs and records the
    suggestion's G-NAF ID in the group's hidden input (see
    address_autocomplete.js).
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


class AddressComponent(NamedTuple):
    """One visitor-facing input of a StructuredAddressWidget."""

    # The Address model field this input maps to.
    name: str
    # The visitor-facing label above the input.
    label: Any  # str | lazy translation proxy
    # Bootstrap column class controlling the input's share of the row.
    column_class: str


# The visible components, in the order the widget renders them and in which
# StructuredAddressField.compress() receives them. The hidden G-NAF ID input
# is appended after these.
ADDRESS_COMPONENTS: tuple[AddressComponent, ...] = (
    AddressComponent("line1", _("Address line 1"), "col-12"),
    AddressComponent("line2", _("Address line 2"), "col-12"),
    AddressComponent("line3", _("Address line 3"), "col-12"),
    AddressComponent("city", _("Suburb"), "col-md-6"),
    AddressComponent("state", _("State"), "col-md-3"),
    AddressComponent("postcode", _("Postcode"), "col-md-3"),
)


class StructuredAddressWidget(forms.MultiWidget):
    """
    An address question rendered as its component inputs — address lines,
    suburb, state, postcode — plus a hidden input carrying the G-NAF ID of
    the autocomplete suggestion the visitor picked, if any.

    The line1 input is an AddressAutocompleteInput. Picking one of its
    suggestions makes the companion script fill every component from the
    suggestion and record its G-NAF ID in the hidden input; the ID is cleared
    again as soon as the first line, suburb, state, or postcode is edited by
    hand. Lines 2 and 3 carry supplementary delivery detail that does not
    move the property, so editing them leaves the ID in place — the server
    then keeps the suggestion's coordinates while withholding the ID itself
    (see Address.from_components). A visitor who ignores the suggestions (or
    types an address Addressr does not know) simply fills the components in
    themselves — the form works identically without a pick, and entirely
    without JavaScript.
    """

    template_name = "underground_crm/widgets/structured_address.html"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        widgets = [
            AddressAutocompleteInput(attrs={"data-address-component": ADDRESS_COMPONENTS[0].name})
        ]
        widgets += [
            forms.TextInput(attrs={"data-address-component": component.name})
            for component in ADDRESS_COMPONENTS[1:]
        ]
        widgets.append(forms.HiddenInput(attrs={"data-address-component": "gnaf_id"}))
        super().__init__(widgets=widgets, attrs=attrs)

    def decompress(self, value: Any) -> list[Any]:
        # Imported here rather than at module level so that importing widgets
        # never drags the model layer in before Django app setup completes.
        from underground_crm.models.address import Address

        if isinstance(value, Address):
            return [
                value.line1,
                value.line2,
                value.line3,
                value.city,
                value.state,
                value.postcode,
                value.gnaf_id,
            ]
        if isinstance(value, str) and value:
            # A bare one-line string (e.g. a stored raw submission): shown in
            # line1 rather than discarded.
            return [value] + [None] * len(ADDRESS_COMPONENTS)
        return [None] * (len(ADDRESS_COMPONENTS) + 1)

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        # The trailing hidden subwidget gets no label or column; zip() simply
        # stops pairing at the end of ADDRESS_COMPONENTS.
        for subwidget, component in zip(context["widget"]["subwidgets"], ADDRESS_COMPONENTS):
            subwidget["label"] = component.label
            subwidget["column_class"] = component.column_class
        return context


class SameAsHomeAddressCheckbox(forms.CheckboxInput):
    """
    The "Same as home address" checkbox that accompanies each non-home address
    input on a registration form.

    Renders as two lines: the address role's own label (e.g. "Mailing
    address") above, then the checkbox with its "Same as home address"
    caption below. The role's label is carried by the widget itself, rather
    than by the address input it accompanies, because that input's own label
    is hidden from view while the checkbox is ticked (see
    same_as_home_address.js) — without it here, a ticked checkbox would give
    no indication of which address role it governs.

    The companion script (see Media below) collapses the address input named
    by data-same-as-home-controls while the checkbox is ticked, so a visitor
    only sees the extra input after declaring their address differs.
    """

    template_name = "underground_crm/widgets/same_as_home_checkbox.html"

    def __init__(
        self,
        controlled_field_id: str,
        address_label: Any,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self.address_label = address_label
        default_attrs: dict[str, Any] = {
            "data-same-as-home-controls": controlled_field_id,
            # RegistrationForm builds this checkbox after FormSubmissionForm has
            # already stamped the Bootstrap classes onto the fields it knows
            # about, so the class is declared here instead.
            "class": "form-check-input",
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        context["widget"]["address_label"] = self.address_label
        return context

    class Media:
        js = ["underground_crm/js/same_as_home_address.js"]
