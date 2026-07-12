import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from wagtail.models import Page

from ..forms.form_submission import find_identity_conflict, get_or_create_person
from ..models.form_submission import FormSubmission
from ..models.person import Tag
from ..signals import subscription_created

Person = get_user_model()


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
def subscribe_view(request):
    """
    Subscribe the visitor to a mailing list by applying a Tag to their Person
    record. JSON body:

        tag         str   Slug of the Tag to apply
        page_id     int   The page hosting the subscription block
        email       str   Required for unauthenticated requests
        first_name  str   Identity guard for unauthenticated requests
        last_name   str   Identity guard for unauthenticated requests

    Follows FormSubmissionForm's identity rules: authenticated visitors are
    tagged directly; unauthenticated visitors resolve to a placeholder or
    name-matched Person (never someone else's account) and a FormSubmission
    is recorded against the hosting page either way.
    """
    try:
        data = json.loads(request.body)
        tag_slug = str(data["tag"])
        page_id = int(data["page_id"])
        email = str(data.get("email", "")).strip()
        first_name = str(data.get("first_name", "")).strip()[:100]
        last_name = str(data.get("last_name", "")).strip()[:100]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({"error": _("Invalid request body.")}, status=400)

    page = Page.objects.live().filter(pk=page_id).first()
    page = page.specific if page else None
    tag = Tag.objects.filter(slug=tag_slug).first()
    if page is None or tag is None or not _page_offers_tag(page, tag):
        return JsonResponse({"error": _("This subscription is not available.")}, status=404)

    is_authenticated = request.user.is_authenticated
    person_created = False
    if is_authenticated:
        target_person = request.user
    else:
        if not email:
            return JsonResponse({"error": _("Email address is required.")}, status=400)
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": _("Please enter a valid email address.")}, status=400)
        conflict = find_identity_conflict(email, first_name, last_name)
        if conflict:
            return JsonResponse({"error": str(conflict)}, status=409)
        email = Person.objects.normalize_email(email)
        person_created = not Person.objects.filter(email=email).exists()
        target_person = get_or_create_person(email, first_name, last_name)

    already_subscribed = target_person.tags.filter(pk=tag.pk).exists()

    submission = FormSubmission(is_authenticated=is_authenticated, page=page)
    if is_authenticated:
        submission.person = request.user
    else:
        submission.email_address = email
        submission.ip_address = request.META.get("REMOTE_ADDR") or None
        submission.language_preferences = request.META.get("HTTP_ACCEPT_LANGUAGE", "")[:128]
    submission.full_clean()
    submission.save()

    target_person.tags.add(tag, through_defaults={"was_authenticated": is_authenticated})

    subscription_created.send(
        sender=None,
        person=target_person,
        tag=tag,
        was_authenticated=is_authenticated,
        person_created=person_created,
    )

    return JsonResponse(
        {
            "subscribed": True,
            "already_subscribed": already_subscribed,
            # True when a confirm-your-subscription email is on its way (see
            # the subscription_created receiver in signals.py).
            "pending_confirmation": person_created,
        }
    )
