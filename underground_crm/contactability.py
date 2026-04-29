from typing import Callable, Generator, Optional, Tuple

import dns
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.functions import Coalesce
from email_validator import EmailNotValidError, validate_email
import phonenumbers
from django.conf import settings
from phonenumbers import PhoneNumber, PhoneNumberType, phonenumberutil


def parse_verified_phone_number(raw_number: str) -> Optional[PhoneNumber]:
    if not raw_number:
        return None
    try:
        return phonenumbers.parse(raw_number, region=settings.PHONE_REGION)
    except phonenumbers.NumberParseException:
        return None


def parse_phone_number_with_verified_type(
    raw_number: str,
) -> Tuple[Optional[PhoneNumber], Optional[int]]:
    phone_number = parse_verified_phone_number(raw_number)
    if not phone_number:
        return None, None
    phone_type = phonenumberutil.number_type(phone_number)
    if phone_type in (
        PhoneNumberType.PREMIUM_RATE,
        PhoneNumberType.SHARED_COST,
        PhoneNumberType.PAGER,
    ):
        print(f"Phone number {raw_number} has forbidden type {phone_type}")
        return None, None
    return phone_number, phone_type


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


def validate_domain_name(domain_name: str) -> None:
    # This might throw a dns.resolver.NXDOMAIN
    dns.resolver.resolve(domain_name, "A")


def get_validated_domain_name(domain_name: str) -> Optional[str]:
    if not domain_name:
        return None
    try:
        validate_domain_name(domain_name)
    except dns.exception.DNSException:
        return None
    return domain_name


def get_full_name_options(full_name: str) -> Generator[Tuple[str, Optional[str]]]:
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
            print(f"Name «{full_name}» is ambiguous")
            if not allow_ambiguity:
                return None
        return users.first()
    print(
        f"User «{full_name}» could not be found in our database. Please import users before pages"
    )
    return None


def get_ambiguous_admin_by_full_name(full_name: str):
    return get_user_by_full_name(
        full_name,
        # Admins first
        ordering_function=lambda x: x.order_by("-is_admin", "-is_staff", "-is_active"),
        allow_ambiguity=True,
    )
