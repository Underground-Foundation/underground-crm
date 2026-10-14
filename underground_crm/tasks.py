import logging
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO

import requests
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

from .models.address import Address
from .models.engagement import Engagement
from .models.pages import EventGuest
from .models.person import Tag

logger = logging.getLogger(__name__)

Person = get_user_model()

PROFILE_PICTURE_TIMEOUT_SECONDS = 30
# Providers serve profile pictures well under 1 MB; anything this large is not
# a profile picture and shouldn't fill the media storage.
MAX_PROFILE_PICTURE_BYTES = 5 * 1024 * 1024
# Keyed by the format Pillow detects, not by the Content-Type the provider
# claims, so the stored file's extension matches its actual contents.
PROFILE_PICTURE_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
# Compact UTC, so a stored picture's age can be read from its filename.
PROFILE_PICTURE_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def geocode_address(address_id: str) -> None:
    """
    Call Addressr to geocode an Address and store the result, adopting
    Addressr's spelling of the street name and suburb where the address had
    them only slightly wrong (see address_correction.correct_minor_misspellings).
    """
    from . import addressr as addressr_client
    from .address_correction import correct_minor_misspellings

    try:
        address = Address.objects.get(pk=uuid.UUID(address_id))
    except Address.DoesNotExist:
        logger.warning("geocode_address: Address %s not found", address_id)
        return

    result = addressr_client.geocode(str(address))
    if result is None:
        logger.info("geocode_address: no result from Addressr for Address %s", address_id)
        return

    typed = {field: getattr(address, field) for field in ("line1", "city")}
    changed = correct_minor_misspellings(address, result.address) if result.address else []

    # Use .update() to avoid re-triggering the post_save signal. Filtering on
    # the values the correction started from means an edit saved while
    # Addressr was being queried is never overwritten; that edit has queued a
    # fresh geocode of its own.
    updated = Address.objects.filter(
        pk=address.pk, **{field: typed[field] for field in changed}
    ).update(
        latitude=result.latitude,
        longitude=result.longitude,
        geocode_reliability=result.reliability,
        gnaf_id=result.gnaf_id,
        **{field: getattr(address, field) for field in changed},
    )
    if changed and updated:
        logger.info(
            "geocode_address: corrected the spelling of %s for Address %s",
            ", ".join(changed),
            address_id,
        )


def record_rsvp_engagement(event_guest_id: str) -> None:
    """
    Create an RSVP Engagement for an EventGuest — tagged even for
    unauthenticated RSVPs (EventGuest.person is only ever set for a real
    logged-in session — see FormSubmission.person's comment — so for an
    anonymous RSVP we resolve the same placeholder/matched Person that
    FormSubmissionForm.save() already created or confirmed by email).
    TODO: once we have a UI for this, surface whether a given engagement
    came from an authenticated or anonymous RSVP.
    """
    try:
        event_guest = EventGuest.objects.select_related("person", "page").get(
            pk=uuid.UUID(event_guest_id)
        )
    except EventGuest.DoesNotExist:
        logger.warning("record_rsvp_engagement: EventGuest %s not found", event_guest_id)
        return

    if event_guest.is_authenticated:
        person = event_guest.person
    else:
        try:
            person = Person.objects.get(email=event_guest.email_address)
        except Person.DoesNotExist:
            logger.warning(
                "record_rsvp_engagement: no Person found for email %s (EventGuest %s)",
                event_guest.email_address,
                event_guest_id,
            )
            return

    Engagement.objects.create(
        person=person,
        action_type=Engagement.RSVP,
        page_url=event_guest.page.slug,
        page_title=event_guest.page.title,
    )


def send_subscription_confirmation(person_id: str, tag_id: str) -> None:
    """
    Email a "confirm your subscription" verification link to a Person created
    by an unauthenticated mailing-list subscription (queued by the
    subscription_created receiver in signals.py).

    Verification state lives in allauth's EmailAddress model: the link points
    at allauth's account_confirm_email view, which marks the address verified
    on confirmation. The message goes out through SMTP2Go's HTTP API like all
    other outbound email — Django's SMTP email backend is not configured in
    deployments.
    """
    from allauth.account.models import EmailAddress, EmailConfirmationHMAC
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.urls import reverse
    from smtp2go.core import Smtp2goClient

    try:
        person = Person.objects.get(pk=uuid.UUID(person_id))
        tag = Tag.objects.get(pk=uuid.UUID(tag_id))
    except (Person.DoesNotExist, Tag.DoesNotExist):
        logger.warning(
            "send_subscription_confirmation: Person %s or Tag %s not found", person_id, tag_id
        )
        return

    email_address, _created = EmailAddress.objects.get_or_create(
        user=person, email=person.email, defaults={"primary": True, "verified": False}
    )
    if email_address.verified:
        logger.info(
            "send_subscription_confirmation: %s is already verified; not emailing", person.email
        )
        return

    confirm_url = settings.WAGTAILADMIN_BASE_URL + reverse(
        "account_confirm_email", args=[EmailConfirmationHMAC(email_address).key]
    )
    html = render_to_string(
        "underground_crm/email/subscription_confirmation.html",
        {"person": person, "tag": tag, "confirm_url": confirm_url},
    )

    api_key = os.environ.get("SMTP_KEY")
    if not api_key:
        raise RuntimeError("SMTP_KEY environment variable is not set")
    response = Smtp2goClient(api_key=api_key).send(
        sender=settings.DEFAULT_FROM_EMAIL,
        recipients=[person.email],
        subject="Confirm your subscription",
        html=html,
    )
    if not response.success:
        logger.error(
            "send_subscription_confirmation: failed to send to %s: %s",
            person.email,
            response.errors,
        )


def import_profile_picture(person_id: str, url: str, now: datetime | None = None) -> None:
    """
    Copy a Person's profile picture from the identity provider's `url` into
    our own media storage, under profiles/, before the provider's signed URL
    expires (queued by PersonSocialAccountAdapter.save_user and
    pre_social_login). The URL itself
    isn't kept.

    A URL that has already expired, or that serves something other than an
    image, is logged and the person is left without a picture.

    The file is named after the person and the time of the import (`now`,
    which defaults to the current time), so a later import never collides
    with an earlier one and the picture's age is visible in its name.
    """
    try:
        person = Person.objects.get(pk=uuid.UUID(person_id))
    except Person.DoesNotExist:
        logger.warning("import_profile_picture: Person %s not found", person_id)
        return
    if person.profile_picture_file:
        logger.info("import_profile_picture: Person %s already has a stored picture", person_id)
        return
    try:
        content = _download_profile_picture(url)
    except (requests.RequestException, ValueError) as error:
        logger.warning(
            "import_profile_picture: could not fetch the picture for Person %s: %s",
            person_id,
            error,
        )
        return

    try:
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        logger.warning(
            "import_profile_picture: the picture for Person %s is not a readable image: %s",
            person_id,
            error,
        )
        return
    extension = PROFILE_PICTURE_EXTENSIONS.get(image_format or "")
    if extension is None:
        logger.warning(
            "import_profile_picture: the picture for Person %s is in the unsupported format %s",
            person_id,
            image_format,
        )
        return

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    person.profile_picture_file.save(
        f"{person.pk}_{timestamp.strftime(PROFILE_PICTURE_TIMESTAMP_FORMAT)}.{extension}",
        ContentFile(content),
        save=False,
    )
    # update_fields keeps this from overwriting anything edited in the admin
    # while the picture was downloading, while still recording a history entry.
    # todo: deleting deprecated image
    person.save(update_fields=["profile_picture_file"])


def _download_profile_picture(url: str) -> bytes:
    """Fetch the picture, refusing anything that isn't served as an image or
    that exceeds MAX_PROFILE_PICTURE_BYTES."""
    with requests.get(url, timeout=PROFILE_PICTURE_TIMEOUT_SECONDS, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError(f"served as {content_type or 'an unknown type'!r}, not an image")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_PROFILE_PICTURE_BYTES:
                raise ValueError(f"larger than {MAX_PROFILE_PICTURE_BYTES} bytes")
    return bytes(content)
