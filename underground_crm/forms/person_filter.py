import json
from collections.abc import Iterator
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from underground_crm.models.filter import (
    FIELD_OPERATORS,
    MEMBERSHIP_FIELD,
    MEMBERSHIP_SINCE_KEY,
    MEMBERSHIP_SINCE_OPERATOR,
    PERSON_FILTER_FIELDS,
    PeopleFilter,
    parse_membership_since,
    parse_membership_type_id,
)
from underground_crm.models.membership import MembershipType


class QueryBuilderWidget(forms.Textarea):
    """
    Replaces the raw JSONField textarea with a visual AND/OR query builder.

    The textarea is hidden; JavaScript reads its value on page load to
    pre-populate the builder, then serialises the builder state back into
    the textarea on form submit so Django's normal form machinery saves it.
    """

    class Media:
        css = {"all": ("underground_crm/css/query_builder.css",)}
        js = ("underground_crm/js/query_builder.js",)

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        attrs["class"] = (attrs.get("class") or "") + " query-builder-source"
        attrs["style"] = "display:none"
        attrs["data-fields"] = json.dumps(
            [{"id": f[0], "label": str(f[1]), "type": f[2]} for f in PERSON_FILTER_FIELDS]
        )
        attrs["data-operators"] = json.dumps(
            {
                ftype: [
                    {"id": op_id, "label": str(label), "has_value": has_value}
                    for op_id, label, has_value in ops
                ]
                for ftype, ops in FIELD_OPERATORS.items()
            }
        )
        attrs["data-membership-type-placeholder"] = str(_("Choose a membership type"))
        return attrs


def _membership_rules(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every membership condition in a criteria tree, however deeply nested."""
    for rule in node.get("rules", []):
        if rule.get("field") == MEMBERSHIP_FIELD:
            yield rule
        elif "rules" in rule:
            yield from _membership_rules(rule)


class PeopleFilterAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_pk = self.instance.pk if self.instance and self.instance.pk else None
        qs = PeopleFilter.objects.order_by("-updated_at")
        if current_pk:
            qs = qs.exclude(pk=current_pk)
        people_filters = [{"id": str(f.pk), "label": f.name} for f in qs]
        membership_types = [
            {"id": str(t.pk), "label": t.name} for t in MembershipType.objects.all()
        ]
        widget = QueryBuilderWidget(attrs={"rows": 6})
        widget.attrs["data-people-filters"] = json.dumps(people_filters)
        widget.attrs["data-membership-types"] = json.dumps(membership_types)
        self.fields["criteria"].widget = widget

    def clean_criteria(self) -> dict[str, Any]:
        """Require a membership condition to name an existing type, and a time if it needs one."""
        criteria: dict[str, Any] = self.cleaned_data["criteria"]
        for rule in _membership_rules(criteria):
            membership_type_id = parse_membership_type_id(rule.get("value"))
            if (
                membership_type_id is None
                or not MembershipType.objects.filter(pk=membership_type_id).exists()
            ):
                raise ValidationError(_("Choose a membership type for each membership condition."))
            if (
                rule.get("operator") == MEMBERSHIP_SINCE_OPERATOR
                and parse_membership_since(rule.get(MEMBERSHIP_SINCE_KEY)) is None
            ):
                raise ValidationError(
                    _('Choose a time for each "has active membership since" condition.')
                )
        return criteria
