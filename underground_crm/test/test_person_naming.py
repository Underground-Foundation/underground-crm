from typing import Any

import django.test
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory
from wagtail.models import Page

from underground_crm.blocks import PERSON_NAME_FIELD_COLUMNS
from underground_crm.forms.form_submission import _field_name
from underground_crm.forms.registration import RegistrationForm
from underground_crm.models.pages import RegistrationPage

Person = get_user_model()

NAMING_TEMPLATE = "underground_crm/includes/person_naming.html"

# A Person field that is no part of a person's name, so it must stay in the
# ordinary one-field-per-row layout instead of being drawn into the naming grid.
NON_NAMING_FIELD = "mobile_number"

# The naming fields this page asks for — a realistic subset, since an editor is
# free to delete any of the naming blocks. Deliberately listed in neither the
# order a name is written nor alphabetical order: the grid is expected to
# reassemble the name itself, and this ordering is what would let it get away
# with simply echoing the page's block order.
NAMING_FIELDS_ON_PAGE = ("last_name", "first_name", "prefix")

# The class Bootstrap 4 uses to mark a row of the form grid, and the class a
# form control needs before Bootstrap will stretch it to fill its column.
FORM_ROW_CLASS = "form-row"
FORM_CONTROL_CLASS = "form-control"


class PersonNamingGridTest(django.test.TestCase):
    """
    A RegistrationPage lays its naming fields out as a single Bootstrap form
    grid (see person_naming.html) rather than one field per row. The grid is
    built from the form rather than from a fixed list of field names, because a
    registration form's fields are keyed by the UUID of the page block that
    produced them and so cannot be addressed by the Person field they write to.

    These tests exercise an authenticated visitor, which is the only case that
    reaches a registration page: the page requires a login, and an anonymous
    visitor would additionally be shown FormSubmissionForm's identity fields.
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
        self.page = RegistrationPage(title="Become a member", slug="become-a-member")
        self.page.body = [
            ("person_field", {"field": "last_name", "label_override": ""}),
            ("person_field", {"field": NON_NAMING_FIELD, "label_override": ""}),
            ("person_field", {"field": "first_name", "label_override": ""}),
            ("person_field", {"field": "prefix", "label_override": ""}),
        ]
        root.add_child(instance=self.page)

    def _form(self, page: RegistrationPage | None = None) -> RegistrationForm:
        request = self.factory.get("/become-a-member/")
        request.user = self.person
        return RegistrationForm(request=request, page=page if page is not None else self.page)

    def _block(self, person_field: str) -> Any:
        """The page's input block for the named Person field."""
        return next(block for block in self.page.inputs if block.value["field"] == person_field)

    def _form_field_name(self, person_field: str) -> str:
        return _field_name(self._block(person_field))

    def _expected_naming_order(self) -> list[str]:
        """The form-field names of this page's naming blocks, in the order a name
        is written — derived from PERSON_NAME_FIELD_COLUMNS, which is the
        definition of that order, rather than restated here."""
        return [
            self._form_field_name(person_field)
            for person_field in PERSON_NAME_FIELD_COLUMNS
            if person_field in NAMING_FIELDS_ON_PAGE
        ]

    def test_grid_presents_the_name_in_reading_order(self) -> None:
        cells = self._form().naming_fields
        self.assertEqual(
            [cell.field.name for cell in cells],
            self._expected_naming_order(),
            "The naming grid must present the name in the order a name is written, "
            "as given by PERSON_NAME_FIELD_COLUMNS — not in the order the editor "
            "happened to leave the page's blocks in.",
        )

    def test_each_cell_carries_the_column_class_declared_for_its_field(self) -> None:
        cells = self._form().naming_fields
        expected_classes = [
            column_class
            for person_field, column_class in PERSON_NAME_FIELD_COLUMNS.items()
            if person_field in NAMING_FIELDS_ON_PAGE
        ]
        self.assertEqual(
            [cell.column_class for cell in cells],
            expected_classes,
            "Each cell's width must be the one declared for its Person field in "
            "PERSON_NAME_FIELD_COLUMNS, so the grid is laid out from a single "
            "source of truth rather than from widths hardcoded in the template.",
        )

    def test_naming_fields_are_withheld_from_the_ordinary_field_layout(self) -> None:
        form = self._form()
        layout_names = [bound_field.name for bound_field in form.layout_fields]

        for person_field in NAMING_FIELDS_ON_PAGE:
            self.assertNotIn(
                self._form_field_name(person_field),
                layout_names,
                f"{person_field!r} is rendered by the naming grid, so leaving it in "
                "layout_fields as well would render it on the page twice.",
            )
        self.assertIn(
            self._form_field_name(NON_NAMING_FIELD),
            layout_names,
            f"{NON_NAMING_FIELD!r} is no part of a person's name, so the grid does not "
            "render it and the ordinary field layout must.",
        )

    def test_grid_renders_one_bootstrap_column_per_naming_field(self) -> None:
        form = self._form()
        html = render_to_string(NAMING_TEMPLATE, {"form": form})

        self.assertIn(
            FORM_ROW_CLASS,
            html,
            "The naming fields must sit in a Bootstrap form row; without it they "
            "stack one per line, which is the layout the grid exists to replace.",
        )
        for cell in form.naming_fields:
            self.assertIn(
                cell.column_class,
                html,
                f"The column {cell.column_class!r} is missing, so {cell.field.name!r} "
                "would not take its declared share of the row.",
            )
            self.assertIn(
                f'id="{cell.field.auto_id}"',
                html,
                f"The input for {cell.field.name!r} is missing from the grid entirely.",
            )

    def test_naming_inputs_are_styled_as_bootstrap_form_controls(self) -> None:
        for cell in self._form().naming_fields:
            self.assertIn(
                FORM_CONTROL_CLASS,
                cell.field.field.widget.attrs.get("class", ""),
                f"Without {FORM_CONTROL_CLASS!r}, {cell.field.name!r} keeps its intrinsic "
                "width instead of filling its grid column, which defeats the layout.",
            )

    def test_page_asking_for_no_part_of_a_name_renders_no_grid(self) -> None:
        root = Page.objects.get(id=1)
        page = RegistrationPage(title="Update your phone", slug="update-your-phone")
        page.body = [("person_field", {"field": NON_NAMING_FIELD, "label_override": ""})]
        root.add_child(instance=page)

        form = self._form(page=page)
        self.assertEqual(
            form.naming_fields,
            [],
            "A page that asks for no part of a person's name has no naming grid to build.",
        )
        self.assertNotIn(
            FORM_ROW_CLASS,
            render_to_string(NAMING_TEMPLATE, {"form": form}),
            "With no naming fields the template must render nothing at all, rather "
            "than an empty row that would still take up vertical space.",
        )
