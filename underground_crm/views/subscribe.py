import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from wagtail.models import Page

from ..forms.form_submission import find_identity_conflict, get_or_create_person
from ..models.form_submission import FormSubmission
from ..models.person import Tag, NAME_FIELD_LENGTH
from ..signals import subscription_created

Person = get_user_model()

logger = logging.getLogger(__name__)


def _page_offers_tag(page, tag: Tag) -> bool:
    """
    True if the page's body contains a block whose value declares this tag in
    a `tag_to_apply` field (e.g. a theme's email-subscription block). Checked
    so this endpoint can only apply tags an editor actually placed on a live
    page, rather than acting as a general-purpose tag writer.
    """
    body = getattr(page, "body", None)
    if not body:
        return False
    for block in body:
        try:
            offered = block.value.get("tag_to_apply")
        except AttributeError:
            continue  # Not a struct-valued block (rich text, raw HTML, ...)
        if offered is not None and offered.pk == tag.pk:
            return True
    return False


@require_POST
def subscription_view(request):
    """
    Subscribe the visitor to a mailing list by applying a Tag to their Person
    record. JSON body:

        tag             str   Slug of the Tag to apply
        page_id         int   The page hosting the subscription block
        email           str   Required for unauthenticated requests
        first_name      str   Identity guard for unauthenticated requests
        preferred_name  str   Alternative to first_name; either or both may be sent
        last_name       str   Identity guard for unauthenticated requests

    Follows FormSubmissionForm's identity rules: authenticated visitors are
    tagged directly; unauthenticated visitors resolve to a placeholder or
    name-matched Person (never someone else's account) and a FormSubmission
    is recorded against the hosting page either way.
    """
    try:
        data = json.loads(request.body)
        tag_name = str(data["tag_name"])
        page_id = int(data["page_id"])
        email_address = str(data.get("email", "")).strip()
        first_name = str(data.get("first_name") or "").strip()[:NAME_FIELD_LENGTH] or None
        preferred_name = str(data.get("preferred_name") or "").strip()[:NAME_FIELD_LENGTH] or None
        last_name = str(data.get("last_name") or "").strip()[:NAME_FIELD_LENGTH] or None
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({"error": _("Invalid request body.")}, status=400)

    page = Page.objects.live().filter(pk=page_id).first()
    page = page.specific if page else None
    if not page:
        logger.error(
            "No such page %s could be found, so %s (%s) shall not be subscribed",
            page_id,
            request.user,
            email_address,
        )
        return JsonResponse({"error": _("Internal error")})
    tag = Tag.objects.filter(name=tag_name).first()
    if not tag:
        logger.error(
            "No such tag %s found, so %s (%s) shall not be subscribed",
            tag_name,
            email_address,
            request.user,
        )
        return JsonResponse({"error": _("Internal error")})
    if tag.name not in settings.SELF_QUERYABLE_TAGS and not _page_offers_tag(page, tag):
        logger.error(
            "The page %s does not offer tag %s, so %s (%s) shall not be subscribed",
            page_id,
            tag,
            request.user,
            email_address,
        )
        return JsonResponse({"error": _("This subscription is not available.")}, status=404)

    is_authenticated = request.user.is_authenticated
    should_create = False
    if is_authenticated:
        target_person: Person = request.user
    else:
        if not email_address:
            return JsonResponse({"error": _("Email address is required.")}, status=400)
        try:
            validate_email(email_address)
        except ValidationError:
            return JsonResponse({"error": _("Please enter a valid email address.")}, status=400)
        conflict = find_identity_conflict(
            email_address, first_name=first_name, last_name=last_name, preferred_name=preferred_name
        )
        if conflict:
            return JsonResponse({"error": str(conflict)}, status=409)
        email_address = Person.objects.normalize_email(email_address)
        should_create = not Person.objects.filter(email=email_address).exists()
        target_person: Person = get_or_create_person(
            email_address, first_name, last_name, preferred_name
        )

    already_subscribed = target_person.tags.filter(pk=tag.pk).exists()

    submission = FormSubmission(is_authenticated=is_authenticated, page=page)
    if is_authenticated:
        submission.person = request.user
    else:
        submission.email_address = email_address
        submission.ip_address = request.META.get("REMOTE_ADDR") or None
        submission.language_preferences = request.META.get("HTTP_ACCEPT_LANGUAGE", "")[:128]
    submission.full_clean()
    submission.save()

    target_person.tags.add(tag, through_defaults={"was_authenticated": is_authenticated})
    if not target_person.email_opt_in:
        target_person.email_opt_in = True
        target_person.save(update_fields=["email_opt_in"])

    subscription_created.send(
        sender=None,
        person=target_person,
        tag=tag,
        was_authenticated=is_authenticated,
        person_created=should_create,
    )
    logger.info("%s has subscribed to tag %s at %s", request.user, tag, email_address)

    return JsonResponse(
        {
            "subscribed": True,
            "already_subscribed": already_subscribed,
            # True when a confirm-your-subscription email is on its way (see
            # the subscription_created receiver in signals.py).
            "pending_confirmation": should_create,
        }
    )
