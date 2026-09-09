import logging
import re
from typing import Callable, Generator, Optional, Tuple
from urllib.parse import urlparse

import dns
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.functions import Coalesce
from email_validator import EmailNotValidError, validate_email
import phonenumbers
from django.conf import settings
from phonenumbers import PhoneNumber, PhoneNumberType, phonenumberutil

from underground_crm.models import Address

logger = logging.getLogger(__name__)


# Number types that no personal contact record should ever hold: nobody is
# reachable on a premium-rate, shared-cost or pager number, so one of these
# arriving in an import or on a form is bad data rather than a way of calling
# the person.
FORBIDDEN_PHONE_TYPES: Tuple[int, ...] = (
    PhoneNumberType.PREMIUM_RATE,
    PhoneNumberType.SHARED_COST,
    PhoneNumberType.PAGER,
)

# Number types that may reach a handset, and therefore belong in
# Person.mobile_number rather than Person.phone_number. FIXED_LINE_OR_MOBILE
# covers the ranges libphonenumber cannot tell apart, and UNKNOWN the numbers
# it cannot classify at all; a number that is not identifiably a landline is
# more useful to us treated as a mobile, because the worst case is an SMS that
# does not arrive, whereas the reverse mistake hides a reachable mobile in the
# landline column.
MOBILE_CAPABLE_PHONE_TYPES: Tuple[int, ...] = (
    PhoneNumberType.MOBILE,
    PhoneNumberType.FIXED_LINE_OR_MOBILE,
    PhoneNumberType.UNKNOWN,
)


class InvalidPhoneNumberError(ValueError):
    # A non-empty value that is not a valid phone number.
    def __init__(self, raw_number: str):
        self.raw_number = raw_number
        super().__init__(f"{raw_number!r} is not a valid phone number")


def parse_verified_phone_number(raw_number: str) -> Optional[PhoneNumber]:
    """Parse ``raw_number`` into a valid :class:`PhoneNumber`.

    Returns ``None`` only for an empty ``raw_number`` (no number supplied). Any
    non-empty value that is not a valid phone number raises
    :class:`InvalidPhoneNumberError` rather than being discarded — see that
    class for why.
    """
    if not raw_number:
        return None
    try:
        parsed = phonenumbers.parse(raw_number, region=settings.PHONE_REGION, keep_raw_input=True)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhoneNumberError(raw_number) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumberError(raw_number)
    return parsed


def parse_phone_number_with_verified_type(
    raw_number: str,
) -> Tuple[Optional[PhoneNumber], Optional[int]]:
    phone_number = parse_verified_phone_number(raw_number)
    if not phone_number:
        return None, None
    phone_type = phonenumberutil.number_type(phone_number)
    if phone_type in FORBIDDEN_PHONE_TYPES:
        logger.warning("Phone number %s has forbidden type %s", raw_number, phone_type)
        return None, None
    return phone_number, phone_type


def is_mobile_number(phone_number: PhoneNumber) -> bool:
    """Whether an already-parsed number should be stored as a mobile number
    (see MOBILE_CAPABLE_PHONE_TYPES) rather than as a landline."""
    return phonenumberutil.number_type(phone_number) in MOBILE_CAPABLE_PHONE_TYPES


def is_forbidden_phone_number(phone_number: PhoneNumber) -> bool:
    """Whether an already-parsed number is of a type we refuse to store at all
    (see FORBIDDEN_PHONE_TYPES)."""
    return phonenumberutil.number_type(phone_number) in FORBIDDEN_PHONE_TYPES


def validate_email_with_deliverability(email_address: str):
    # Validates the syntax and also the existence of MX records for the domain
    return validate_email(email_address, check_deliverability=True, globally_deliverable=True)


def get_validated_email_address(email_address: str) -> Optional[str]:
    if not email_address:
        return None
    try:
        validate_email_with_deliverability(email_address)
    except EmailNotValidError:
        return None
    return email_address


def validate_domain_name(url: str) -> None:
    hostname = urlparse(url).hostname or url
    dns.resolver.resolve(hostname, "A")


def get_validated_domain_name(domain_name: str) -> Optional[str]:
    if not domain_name:
        return None
    try:
        validate_domain_name(domain_name)
    except dns.exception.DNSException:
        return None
    return domain_name


def get_full_name_options(full_name: str) -> Generator[Tuple[str, Optional[str]], None, None]:
    name_parts = full_name.split(" ")
    end = max(len(name_parts), 2)  # Ensure the loop runs at least once
    for i in range(1, end):
        # Any middle words shall initially be interpreted as parts of the last name
        first_half = " ".join(name_parts[:i])
        second_half = " ".join(name_parts[i:]) or None
        yield first_half, second_half


def get_user_by_full_name(
    full_name: str, ordering_function: Optional[Callable] = None, allow_ambiguity=False
) -> Optional[settings.AUTH_USER_MODEL]:
    User = get_user_model()
    for pair in get_full_name_options(full_name):
        first, last = pair
        logger.debug("Trying name %s … %s", first, last)
        users = User.objects.annotate(search_name=Coalesce("preferred_name", "first_name")).filter(
            # todo: use more than 2 words of a name
            Q(search_name__startswith=first)
        )
        if last:
            users = users.filter(last_name__endswith=last)
        if ordering_function:
            users = ordering_function(users)
        if users.count() == 0:
            continue
        if users.count() > 1:
            logger.warning("Name «%s» is ambiguous", full_name)
            if not allow_ambiguity:
                return None
        return users.first()
    logger.warning(
        "User «%s» could not be found in our database. Have all users been imported?", full_name
    )
    return None


def get_ambiguous_admin_by_full_name(full_name: str):
    return get_user_by_full_name(
        full_name,
        # Admins first
        ordering_function=lambda x: x.order_by("-is_admin", "-is_staff", "-is_active"),
        allow_ambiguity=True,
    )


_UNIT_NUMBER_RE = re.compile(r"^(\d+)/(.+)$")


def _expand_unit_address(segment: str) -> list[str]:
    """Expand an Australian unit/street segment into two lines.

    "701/5 Ovens Street" → ["Unit 701", "5 Ovens Street"]
    A segment without a unit prefix is returned as a single-element list.
    """
    m = _UNIT_NUMBER_RE.match(segment)
    if m:
        return [f"Unit {m.group(1)}", m.group(2).strip()]
    return [segment]


def parse_address(label: str) -> Optional[Address]:
    """Parse a venue string into an unsaved Address instance.

    Expects comma-separated parts. The final segment is treated as
    "City [STATE] Postcode" for Australian addresses (e.g. "Brunswick 3056"
    or "Brunswick VIC 3056"). Any preceding segments become address lines;
    a segment in "unit/street_number name" format is expanded into two lines
    ("Unit N" and "street_number name"). Returns None for an empty label.

    If this proves insufficient, we shall use https://pypi.org/project/deepparse/
    """
    if not label:
        return None

    parts = [part.strip() for part in label.split(",")]
    last = parts[-1].strip()

    # Try "City STATE Postcode", then fall back to "City Postcode"
    m = re.match(r"^(.*?)\s+([A-Z]{2,3})\s+(\d{4,5})$", last)
    if m:
        city, state, postcode = m.group(1).strip(), m.group(2), m.group(3)
    else:
        m = re.match(r"^(.*?)\s+(\d{4,5})$", last)
        city = m.group(1).strip() if m else last
        state = ""
        postcode = m.group(2) if m else ""

    lines: list[str] = []
    for segment in parts[:-1]:
        lines.extend(_expand_unit_address(segment))

    return Address(
        line1=lines[0] if len(lines) > 0 else "",
        line2=lines[1] if len(lines) > 1 else "",
        line3=lines[2] if len(lines) > 2 else "",
        city=city,
        state=state,
        postcode=postcode,
    )
