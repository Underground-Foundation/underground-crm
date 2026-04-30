import uuid
from typing import Any

from django.db import models
from django.db.models import Q


# Fields that can appear in a PersonFilter rule.
# Each entry is (field_path, display_label, field_type).
# field_type must be one of: "text", "boolean", "integer", "filter".
PERSON_FILTER_FIELDS: list[tuple[str, str, str]] = [
    ("first_name", "First name", "text"),
    ("last_name", "Last name", "text"),
    ("email", "Email", "text"),
    ("gender", "Gender", "text"),
    ("federal_district", "Federal district", "text"),
    ("state_upper_district", "State upper district", "text"),
    ("state_lower_district", "State lower district", "text"),
    ("council_district", "Council district", "text"),
    ("ward", "Ward", "text"),
    ("primary_address__postcode", "Postcode (primary)", "text"),
    ("primary_address__state", "State (primary)", "text"),
    ("primary_address__suburb", "Suburb (primary)", "text"),
    ("tags__name", "Tag", "text"),
    ("is_supporter", "Is supporter", "boolean"),
    ("is_volunteer", "Is volunteer", "boolean"),
    ("is_prospect", "Is prospect", "boolean"),
    ("is_donor", "Is donor", "boolean"),
    ("is_fundraiser", "Is fundraiser", "boolean"),
    ("is_deceased", "Is deceased", "boolean"),
    ("email_opt_in", "Email opt-in", "boolean"),
    ("do_not_call", "Do not call", "boolean"),
    ("do_not_contact", "Do not contact", "boolean"),
    ("support_level", "Support level", "integer"),
    ("inferred_support_level", "Inferred support level", "integer"),
    ("priority_level", "Priority level", "integer"),
    ("donations_count", "Donations count", "integer"),
    ("__filter__", "People filter", "filter"),
]

# Operators available for each field type.
# Each entry is (operator_id, display_label, has_value).
# operator_id is either a raw Django ORM lookup suffix or a special sentinel
# handled by PersonFilter._rule_to_q().
FIELD_OPERATORS: dict[str, list[tuple[str, str, bool]]] = {
    "text": [
        ("icontains", "contains", True),
        ("exact", "is exactly", True),
        ("istartswith", "starts with", True),
        ("isnull", "is empty", False),
        ("not_isnull", "is not empty", False),
    ],
    "boolean": [
        ("true", "is true", False),
        ("false", "is false", False),
    ],
    "integer": [
        ("exact", "equals", True),
        ("gte", "at least", True),
        ("lte", "at most", True),
        ("gt", "greater than", True),
        ("lt", "less than", True),
        ("isnull", "is not set", False),
    ],
    "filter": [
        ("matches", "matches", True),
    ],
}


class PeopleFilter(models.Model):
    """
    A saved Person query, stored as a nested AND/OR tree.

    ``criteria`` is a JSON object of the form::

        {
            "logic": "AND",          // "AND" | "OR"
            "rules": [
                {"field": "gender", "operator": "exact", "value": "M"},
                {
                    "logic": "OR",
                    "rules": [
                        {"field": "is_volunteer", "operator": "true"},
                        {"field": "is_donor",     "operator": "true"}
                    ]
                }
            ]
        }

    Leaf nodes carry ``field``, ``operator``, and optionally ``value``.
    Group nodes carry ``logic`` and ``rules``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(
        blank=True,
        help_text="Optional human-readable description of who this filter selects.",
    )
    criteria = models.JSONField(
        default=dict,
        help_text="Nested AND/OR rule tree built with the query builder.",
    )

    class Meta:
        verbose_name = "People filter"
        verbose_name_plural = "People filters"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, queryset, _seen: frozenset = frozenset()):
        """Return the queryset narrowed by this filter's criteria tree."""
        seen = _seen | {self.pk}
        return queryset.filter(self._build_q(self.criteria, seen))

    @property
    def sql(self) -> str:
        """Return the SQL that this filter would generate against the Person table."""
        from .person import Person

        return str(self.apply(Person.objects.all()).query)

    @property
    def evaluation_link(self) -> str:
        from django.urls import reverse
        from django.utils.html import format_html

        url = reverse("admin:underground_crm_peoplefilter_evaluate", args=[self.pk])
        return format_html('<a href="{}">Evaluate</a>', url)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_q(self, node: dict, seen: frozenset) -> Q:
        logic = node.get("logic", "AND")
        q_list: list[Q] = []
        for rule in node.get("rules", []):
            if "field" in rule:
                q_list.append(self._rule_to_q(rule, seen))
            elif "rules" in rule:
                q_list.append(self._build_q(rule, seen))
        if not q_list:
            return Q()
        result = q_list[0]
        for q in q_list[1:]:
            result = (result & q) if logic == "AND" else (result | q)
        return result

    def _rule_to_q(self, rule: dict, seen: frozenset) -> Q:
        field: str = rule["field"]
        op: str = rule["operator"]
        value: Any = rule.get("value")

        if field == "__filter__":
            try:
                sub_filter = PeopleFilter.objects.get(pk=value)
            except PeopleFilter.DoesNotExist:
                return Q(pk__in=[])
            if sub_filter.pk in seen:
                return Q(pk__in=[])
            from .person import Person

            sub_qs = sub_filter.apply(Person.objects.all(), _seen=seen)
            return Q(pk__in=sub_qs)

        if op == "isnull":
            return Q(**{f"{field}__isnull": True})
        if op == "not_isnull":
            return Q(**{f"{field}__isnull": False})
        if op == "true":
            return Q(**{field: True})
        if op == "false":
            return Q(**{field: False})
        return Q(**{f"{field}__{op}": value})
