import datetime
import uuid
from typing import Any

from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.functional import Promise
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .interaction import Interaction
from .membership import Membership

# The pseudo-field of a membership condition, and its operators. Every operator takes
# a membership type's ID as the rule's "value". MEMBERSHIP_SINCE_OPERATOR also takes
# the earliest acceptable start time as the rule's MEMBERSHIP_SINCE_KEY.
# MEMBERSHIP_STARTED_BEFORE_OPERATOR and MEMBERSHIP_STARTED_BY_OPERATOR take a date
# (YYYY-MM-DD) as the rule's MEMBERSHIP_START_DATE_KEY, and compare it with the date
# on which the membership started in the site's time zone: "before" excludes that
# date, whereas "by" includes it. MEMBERSHIP_AWAITING_APPROVAL_OPERATOR takes
# nothing more; it matches a current membership that staff have yet to approve,
# where every other operator matches an approved one.
MEMBERSHIP_FIELD = "__membership__"
MEMBERSHIP_HELD_OPERATOR = "has"
MEMBERSHIP_SINCE_OPERATOR = "since"
MEMBERSHIP_SINCE_KEY = "since"
MEMBERSHIP_STARTED_BEFORE_OPERATOR = "started_before"
MEMBERSHIP_STARTED_BY_OPERATOR = "started_by"
MEMBERSHIP_START_DATE_KEY = "start_date"
MEMBERSHIP_AWAITING_APPROVAL_OPERATOR = "awaiting_approval"
# The lookup on Membership with which each start date operator compares its date.
MEMBERSHIP_START_DATE_LOOKUPS: dict[str, str] = {
    MEMBERSHIP_STARTED_BEFORE_OPERATOR: "started_at__date__lt",
    MEMBERSHIP_STARTED_BY_OPERATOR: "started_at__date__lte",
}

# The pseudo-field of an interaction condition, and its two operators. Both take the
# first date of the period as the rule's "value", in YYYY-MM-DD form;
# INTERACTION_BETWEEN_OPERATOR also takes the last date as the rule's
# INTERACTION_UNTIL_KEY. Both dates are inclusive, and are read in the site's time zone.
INTERACTION_FIELD = "__interaction__"
INTERACTION_SINCE_OPERATOR = "since"
INTERACTION_BETWEEN_OPERATOR = "between"
INTERACTION_UNTIL_KEY = "until"

# Fields that can appear in a PersonFilter rule.
# Each entry is (field_path, display_label, field_type).
# field_type must be one of: "text", "presence", "boolean", "integer", "date",
# "filter", "membership", "interaction".
PERSON_FILTER_FIELDS: list[tuple[str, str | Promise, str]] = [
    ("first_name", "First name", "text"),
    ("last_name", "Last name", "text"),
    ("email", "Email", "text"),
    ("email_is_bad", "Email address is bad", "boolean"),
    ("gender", "Gender", "text"),
    ("federal_district", "Federal district", "text"),
    ("state_upper_district", "State upper district", "text"),
    ("state_lower_district", "State lower district", "text"),
    ("council_district", "Council district", "text"),
    ("ward", "Ward", "text"),
    ("home_address__postcode", "Postcode (home)", "text"),
    ("home_address__state", "State (home)", "text"),
    # The Address model calls the suburb "city", following the G-NAF locality naming.
    ("home_address__city", "Suburb (home)", "text"),
    ("tags__name", "Tag", "text"),
    ("is_supporter", "Is supporter", "boolean"),
    ("is_prospect", "Is prospect", "boolean"),
    ("is_donor", "Is donor", "boolean"),
    ("is_fundraiser", "Is fundraiser", "boolean"),
    ("is_deceased", "Is deceased", "boolean"),
    ("is_active", "Is active", "boolean"),
    ("phone_number", _("Phone number"), "presence"),
    ("mobile_number", _("Mobile number"), "presence"),
    ("mobile_opt_in", _("Mobile opt-in"), "boolean"),
    ("is_mobile_bad", _("Mobile number is bad"), "boolean"),
    ("email_opt_in", "Email opt-in", "boolean"),
    ("unsubscribed_at", "Unsubscribed at", "date"),
    ("do_not_call", "Do not call", "boolean"),
    ("do_not_contact", "Do not contact", "boolean"),
    ("support_level", "Support level", "integer"),
    ("inferred_support_level", "Inferred support level", "integer"),
    ("priority_level", "Priority level", "integer"),
    ("donations_count", "Donations count", "integer"),
    ("__filter__", "People filter", "filter"),
    (MEMBERSHIP_FIELD, _("Membership"), "membership"),
    (INTERACTION_FIELD, _("Interaction"), "interaction"),
]

# Operators available for each field type.
# Each entry is (operator_id, display_label, has_value).
# operator_id is either a raw Django ORM lookup suffix or a special sentinel
# handled by PersonFilter._rule_to_q().
FIELD_OPERATORS: dict[str, list[tuple[str, str | Promise, bool]]] = {
    "text": [
        ("icontains", "contains", True),
        ("exact", "is exactly", True),
        ("not_exact", "does not equal", True),
        ("istartswith", "starts with", True),
        ("isnull", "is empty", False),
        ("not_isnull", "is not empty", False),
    ],
    # A field such as a phone number, whose contents are not worth searching, so
    # only whether it holds anything at all. NULL and "" both count as empty.
    "presence": [
        ("isnull", _("is empty"), False),
        ("not_isnull", _("is not empty"), False),
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
    # Date values are entered as YYYY-MM-DD. The field may hold a full
    # timestamp, so "before" and "after" compare the date it falls on in the
    # site's time zone, and both exclude the given date itself.
    "date": [
        ("isnull", "is not set", False),
        ("not_isnull", "is set", False),
        ("date__lt", "is before", True),
        ("date__gt", "is after", True),
    ],
    "filter": [
        ("matches", "matches", True),
    ],
    # Only an active membership of the chosen type counts: one that staff have
    # approved and that has neither expired nor been suspended. The exception is
    # "awaiting approval", which counts a current membership not yet approved.
    "membership": [
        (MEMBERSHIP_HELD_OPERATOR, _("has active membership"), True),
        (MEMBERSHIP_SINCE_OPERATOR, _("has active membership since"), True),
        (
            MEMBERSHIP_STARTED_BEFORE_OPERATOR,
            _("has active membership that started before"),
            True,
        ),
        (MEMBERSHIP_STARTED_BY_OPERATOR, _("has active membership that started by"), True),
        (
            MEMBERSHIP_AWAITING_APPROVAL_OPERATOR,
            _("has active membership awaiting approval"),
            True,
        ),
    ],
    # Any interaction counts, whatever its method or outcome.
    "interaction": [
        (INTERACTION_SINCE_OPERATOR, _("had an interaction on or after"), True),
        (INTERACTION_BETWEEN_OPERATOR, _("had an interaction between"), True),
    ],
}

# Field types whose blank values may be stored as "" as well as NULL.
STRING_FIELD_TYPES = {"text", "presence"}

FIELD_TYPES: dict[str, str] = {
    field_path: field_type for field_path, _label, field_type in PERSON_FILTER_FIELDS
}

# How a group combines its conditions: a person must match all of them, any of
# them, or none of them.
LOGIC_ALL = "AND"
LOGIC_ANY = "OR"
LOGIC_NONE = "NONE"


def _is_many_valued(field_path: str) -> bool:
    """Return whether a Person field path crosses a relation holding several rows per person."""
    from .person import Person

    model: type[models.Model] = Person
    for part in field_path.split("__"):
        field = model._meta.get_field(part)
        if field.many_to_many or field.one_to_many:
            return True
        if not field.is_relation:
            return False
        model = field.related_model
    return False


def parse_membership_type_id(value: Any) -> uuid.UUID | None:
    """Return the membership type ID held by a rule, or None if it does not hold one."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def parse_membership_since(value: Any) -> datetime.datetime | None:
    """
    Return the start time held by a rule, or None if it does not hold one.

    The query builder submits a time with no UTC offset, such as "2026-07-01T09:00",
    which is read as a time in the site's time zone.
    """
    if not isinstance(value, str):
        return None
    try:
        since = parse_datetime(value)
    except ValueError:
        return None
    if since is not None and timezone.is_naive(since):
        return timezone.make_aware(since)
    return since


def parse_rule_date(value: Any) -> datetime.date | None:
    """Return the date (YYYY-MM-DD) held by a rule, or None if it does not hold one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


class PeopleFilter(models.Model):
    """
    A saved Person query, stored as a nested AND/OR/NONE tree.

    ``criteria`` is a JSON object of the form::

        {
            "logic": "AND",          // "AND" | "OR" | "NONE"
            "rules": [
                {"field": "gender", "operator": "exact", "value": "M"},
                {
                    "logic": "OR",
                    "rules": [
                        {"field": "tags__name", "operator": "exact", "value": "Volunteer"},
                        {"field": "is_donor",   "operator": "true"}
                    ]
                },
                {
                    "field": "__membership__",
                    "operator": "since",
                    "value": "<MembershipType UUID>",
                    "since": "2026-07-01T09:00"
                },
                {
                    "field": "__membership__",
                    "operator": "started_by",
                    "value": "<MembershipType UUID>",
                    "start_date": "2026-06-30"
                },
                {
                    "field": "__interaction__",
                    "operator": "between",
                    "value": "2026-07-01",
                    "until": "2026-07-31"
                }
            ]
        }

    Leaf nodes carry ``field``, ``operator``, and optionally ``value``.
    Group nodes carry ``logic`` and ``rules``. A "NONE" group matches the
    people who match none of its rules, e.g. everyone except those who have
    marked our emails as spam.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    updated_at = models.DateTimeField(auto_now=True)
    description = models.TextField(
        blank=True,
        help_text="Optional human-readable description of who this filter selects.",
    )
    criteria = models.JSONField(
        default=dict,
        help_text="Nested AND/OR/NONE rule tree built with the query builder.",
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
        User = get_user_model()

        return str(self.apply(User.objects.all()).query)

    @property
    def evaluation_url(self) -> str:
        """The admin page listing every person this filter currently matches."""
        return reverse("admin:underground_crm_peoplefilter_evaluate", args=[self.pk])

    @property
    def map_url(self) -> str:
        """The admin page plotting the matched people on a map of their addresses."""
        return reverse("admin:underground_crm_peoplefilter_map", args=[self.pk])

    @property
    def evaluation_link(self) -> str:
        return format_html('<a href="{}">{}</a>', self.evaluation_url, _("Evaluate"))

    @property
    def map_link(self) -> str:
        return format_html('<a href="{}">{}</a>', self.map_url, _("Map"))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_q(self, node: dict, seen: frozenset) -> Q:
        logic = node.get("logic", LOGIC_ALL)
        q_list: list[Q] = []
        for rule in node.get("rules", []):
            if "field" in rule:
                q_list.append(self._rule_to_q(rule, seen))
            elif "rules" in rule:
                q_list.append(self._build_q(rule, seen))
        if not q_list:
            # Matching all, any or none of no conditions excludes nobody.
            return Q()
        result = q_list[0]
        for q in q_list[1:]:
            result = (result & q) if logic == LOGIC_ALL else (result | q)
        if logic == LOGIC_NONE:
            # This excludes every person who matches any of the rules, so that the
            # negation always applies to the person and never to a row that one of
            # the rules happens to have joined.
            from .person import Person

            matching_people = Person.objects.filter(result)
            return ~Q(pk__in=matching_people.values("pk"))
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

        if field == MEMBERSHIP_FIELD:
            return self._membership_rule_to_q(rule)

        if field == INTERACTION_FIELD:
            return self._interaction_rule_to_q(rule)

        field_q = self._field_rule_to_q(field, op, value)
        if op != "not_exact" and _is_many_valued(field):
            # All the conditions of one filter() call share a single join, so "tagged
            # Volunteer and tagged Donor" would demand one tag row with both names, and
            # match nobody. A person matching through two rows would also be listed
            # twice. A subquery gives each condition its own join and yields each person
            # once. "not_exact" already tests the person rather than a join row.
            from .person import Person

            return Q(pk__in=Person.objects.filter(field_q).values("pk"))
        return field_q

    @staticmethod
    def _field_rule_to_q(field: str, op: str, value: Any) -> Q:
        is_text = FIELD_TYPES.get(field) in STRING_FIELD_TYPES
        if op == "isnull":
            empty = Q(**{f"{field}__isnull": True})
            if is_text:
                # Text fields such as email are usually stored as "" rather
                # than NULL when blank, and both should read as "is empty".
                empty |= Q(**{field: ""})
            return empty
        if op == "not_isnull":
            if is_text:
                # Every non-empty string sorts after "", and NULL compares as
                # neither, so this excludes both kinds of blank.
                return Q(**{f"{field}__gt": ""})
            return Q(**{f"{field}__isnull": False})
        if op == "true":
            return Q(**{field: True})
        if op == "false":
            return Q(**{field: False})
        if op == "not_exact":
            # A plain ~Q(**{field: value}) is wrong for many-valued relations such
            # as tags__name: Django would join in one related row per person and
            # exclude only that row, so a person with both the excluded tag and
            # some other tag would still match on the other tag's join row. This
            # excludes by person, not by join row: it excludes every person who
            # has the value at all, wherever it comes from.
            from .person import Person

            matching_people = Person.objects.filter(**{f"{field}__exact": value})
            return ~Q(pk__in=matching_people.values("pk"))
        return Q(**{f"{field}__{op}": value})

    @staticmethod
    def _membership_rule_to_q(rule: dict) -> Q:
        # This selects people through a subquery rather than with a condition such
        # as Q(memberships__type=...). All the conditions of one filter() call share
        # a single join, so "has active membership A and has active membership B"
        # would otherwise demand one membership row of both types, and match nobody.
        membership_type_id = parse_membership_type_id(rule.get("value"))
        if membership_type_id is None:
            return Q(pk__in=[])
        if rule["operator"] == MEMBERSHIP_AWAITING_APPROVAL_OPERATOR:
            memberships = Membership.objects.awaiting_approval()
        else:
            memberships = Membership.objects.active()
        memberships = memberships.filter(type_id=membership_type_id)
        if rule["operator"] == MEMBERSHIP_SINCE_OPERATOR:
            since = parse_membership_since(rule.get(MEMBERSHIP_SINCE_KEY))
            if since is None:
                return Q(pk__in=[])
            memberships = memberships.filter(started_at__gte=since)
        elif rule["operator"] in MEMBERSHIP_START_DATE_LOOKUPS:
            start_date = parse_rule_date(rule.get(MEMBERSHIP_START_DATE_KEY))
            if start_date is None:
                return Q(pk__in=[])
            # started_at__date takes the date on which the membership started in the
            # site's time zone, so a late-evening signup is not counted as the next day's.
            lookup = MEMBERSHIP_START_DATE_LOOKUPS[rule["operator"]]
            memberships = memberships.filter(**{lookup: start_date})
        return Q(pk__in=memberships.values("person"))

    @staticmethod
    def _interaction_rule_to_q(rule: dict) -> Q:
        # As with memberships, a subquery lets two interaction conditions in one
        # group match two different interactions, and lists each person once however
        # many of their interactions fall in the period.
        first_date = parse_rule_date(rule.get("value"))
        if first_date is None:
            return Q(pk__in=[])
        # created_at__date takes the date on which the interaction happened in the
        # site's time zone, so a late-evening call is not counted as the next day's.
        interactions = Interaction.objects.filter(created_at__date__gte=first_date)
        if rule["operator"] == INTERACTION_BETWEEN_OPERATOR:
            last_date = parse_rule_date(rule.get(INTERACTION_UNTIL_KEY))
            if last_date is None:
                return Q(pk__in=[])
            interactions = interactions.filter(created_at__date__lte=last_date)
        return Q(pk__in=interactions.values("person"))
