import re
from typing import Any

import django.test
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from wagtail.models import Page

from underground_crm.forms.form_submission import _field_name
from underground_crm.forms.registration import RegistrationForm, _companion_name
from underground_crm.models.pages import RegistrationPage
from underground_crm.widgets import ADDRESS_COMPONENTS

Person = get_user_model()

# The address roles this page collects. mailing_address is a non-home role, so it
# is the one that gets a "same as home address" checkbox to collapse it.
HOME_ADDRESS_FIELD = "home_address"
MAILING_ADDRESS_FIELD = "mailing_address"

# The class a form control needs before Bootstrap will stretch it to fill its
# grid column, and the attribute address_autocomplete.js uses to find a group.
FORM_CONTROL_CLASS = "form-control"
GROUP_ATTRIBUTE = "data-structured-address"


class StructuredAddressWidgetRenderingTest(django.test.TestCase):
    """
    An address question renders as a group of component inputs
    (StructuredAddressWidget), not a single text box.

    These tests render the widget to HTML rather than only building the form.
    That distinction is the point of them: the widget names its own template,
    and a widget whose template does not exist builds perfectly happily and only
    fails when something finally asks it for markup. Every test here would pass
    against a form that could never actually be shown to a visitor.
    """

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.person = Person.objects.create_user(
            email="priya.raman@example.org",
            password="correct-horse-battery-staple",
            first_name="Priya",
            last_name="Raman",
        )
        root = Page.objects.get(id=1)
        self.page = RegistrationPage(title="Register", slug="register")
        self.page.body = [
            ("person_field", {"field": HOME_ADDRESS_FIELD, "label_override": ""}),
            ("person_field", {"field": MAILING_ADDRESS_FIELD, "label_override": ""}),
        ]
        root.add_child(instance=self.page)

        request = self.factory.get("/register/")
        request.user = self.person
        self.form = RegistrationForm(request=request, page=self.page)

    def _block(self, person_field: str) -> Any:
        return next(b for b in self.page.inputs if b.value["field"] == person_field)

    def _rendered(self, person_field: str) -> str:
        """The address group's HTML. Rendering is what proves the widget's
        template exists at all — see the class docstring."""
        return self.form[_field_name(self._block(person_field))].as_widget()

    def test_group_renders_one_labeled_input_per_address_component(self) -> None:
        html = self._rendered(HOME_ADDRESS_FIELD)
        for component in ADDRESS_COMPONENTS:
            self.assertIn(
                f'data-address-component="{component.name}"',
                html,
                f"The {component.name!r} input is missing, so a visitor could not enter "
                "that part of their address and address_autocomplete.js could not fill it.",
            )
            self.assertIn(
                str(component.label),
                html,
                f"The {component.name!r} input rendered without its label.",
            )

    def test_components_are_styled_to_fill_their_declared_columns(self) -> None:
        html = self._rendered(HOME_ADDRESS_FIELD)
        for component in ADDRESS_COMPONENTS:
            self.assertIn(
                component.column_class,
                html,
                f"The column {component.column_class!r} declared for {component.name!r} in "
                "ADDRESS_COMPONENTS is missing, so the component would not take its "
                "declared share of the row.",
            )
        visible_inputs = re.findall(r'<input(?![^>]*type="hidden")[^>]*>', html)
        self.assertEqual(
            len(visible_inputs),
            len(ADDRESS_COMPONENTS),
            "Every visible address component should render exactly one input.",
        )
        for tag in visible_inputs:
            self.assertIn(
                FORM_CONTROL_CLASS,
                tag,
                f"Without {FORM_CONTROL_CLASS!r} this component keeps its intrinsic width "
                "instead of filling its column, which breaks the address grid.",
            )

    def test_group_carries_the_hidden_gnaf_input_unstyled(self) -> None:
        html = self._rendered(HOME_ADDRESS_FIELD)
        hidden = re.findall(r'<input[^>]*type="hidden"[^>]*>', html)
        self.assertEqual(
            len(hidden),
            1,
            "The group needs exactly one hidden input, carrying the G-NAF ID of the "
            "autocomplete suggestion the visitor picked.",
        )
        self.assertIn('data-address-component="gnaf_id"', hidden[0])
        self.assertNotIn(
            FORM_CONTROL_CLASS,
            hidden[0],
            "The G-NAF input is hidden, so styling it as a visible form control would be "
            "meaningless — and, if Bootstrap ever gave the class a box model, harmful.",
        )

    def test_group_is_discoverable_by_the_autocomplete_script(self) -> None:
        self.assertIn(
            GROUP_ATTRIBUTE,
            self._rendered(HOME_ADDRESS_FIELD),
            f"address_autocomplete.js finds a component group by its {GROUP_ATTRIBUTE!r} "
            "attribute; without it, picking a suggestion could not spread the address "
            "across the group's inputs.",
        )

    def test_same_as_home_checkbox_collapses_its_own_address_group(self) -> None:
        block = self._block(MAILING_ADDRESS_FIELD)
        container_id = re.search(r'<div id="([^"]+)"', self._rendered(MAILING_ADDRESS_FIELD)).group(
            1
        )
        checkbox = self.form.fields[_companion_name(block)]

        self.assertEqual(
            checkbox.widget.attrs["data-same-as-home-controls"],
            container_id,
            "The checkbox must name the id of the address group it collapses. If it named "
            "anything else — another address role's group, or an id that is not rendered — "
            "ticking it would silently collapse the wrong fields, or none at all.",
        )
