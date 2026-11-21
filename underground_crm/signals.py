import logging

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import Signal, receiver
from django.utils import timezone
from django_q.tasks import async_task
from wagtail.models import Site

from .models.address import Address
from .models.membership import Membership
from .models.pages import EventGuest
from .models.person import Person, PersonTag, Tag

logger = logging.getLogger(__name__)

# Sent by views.subscribe.subscribe_view after a mailing-list Tag is applied.
# Kwargs: person, tag, was_authenticated, person_created (True when the
# subscription created a new placeholder Person for a previously unseen email),
# site (the Wagtail Site the visitor subscribed on, or None if none matched).
subscription_created = Signal()

_ADDRESS_CONTENT_FIELDS = ("line1", "line2", "line3", "city", "state", "postcode", "country_code")


@receiver(pre_save, sender=Address)
def on_address_pre_save(sender: type[Address], instance: Address, **kwargs) -> None:
    """Clear coordinates when address content changes so re-geocoding is triggered."""
    if not instance.pk:
        return
    try:
        old = Address.objects.get(pk=instance.pk)
    except Address.DoesNotExist:
        return
    if any(getattr(old, f) != getattr(instance, f) for f in _ADDRESS_CONTENT_FIELDS):
        instance.latitude = None
        instance.longitude = None


@receiver(post_save, sender=Address)
def on_address_post_save(sender: type[Address], instance: Address, **kwargs) -> None:
    """Queue geocoding whenever an address has content but no coordinates."""
    if getattr(instance, "_skip_geocoding", False):
        return
    if instance.latitude is not None or instance.longitude is not None:
        return
    if any(getattr(instance, f) for f in _ADDRESS_CONTENT_FIELDS):
        async_task("underground_crm.tasks.geocode_address", str(instance.pk))


def _touch_person_for_membership_change(person_id) -> None:
    """Move the owning person to the end of the integration feed.

    The feed pages by ``Person.updated_at``. A membership can be created,
    changed or deleted without saving its person, so without this the change
    would never reach an integration and a removed membership could remain
    active in the consuming system. A queryset update avoids Person.save(),
    which would rewrite unrelated fields and add a history entry for a change
    that did not touch them.
    """
    Person.objects.filter(pk=person_id).update(updated_at=timezone.now())


@receiver(post_save, sender=Membership)
def on_membership_saved(sender: type[Membership], instance: Membership, **kwargs) -> None:
    _touch_person_for_membership_change(instance.person_id)


@receiver(post_delete, sender=Membership)
def on_membership_deleted(sender: type[Membership], instance: Membership, **kwargs) -> None:
    _touch_person_for_membership_change(instance.person_id)


@receiver(post_save, sender=EventGuest)
def on_event_guest_saved(
    sender: type[EventGuest], instance: EventGuest, created: bool, **kwargs
) -> None:
    if created:
        async_task("underground_crm.tasks.record_rsvp_engagement", str(instance.pk))


@receiver(subscription_created)
def on_subscription_created(
    sender,
    person,
    tag: Tag,
    was_authenticated: bool,
    person_created: bool,
    site: Site | None,
    **kwargs,
) -> None:
    """
    When an unauthenticated visitor subscribes with a previously unseen email
    address, queue a "confirm your subscription" verification email. Known
    email addresses (matched or authenticated) skip it — the address either
    belongs to an established record or the visitor proved it by logging in.

    The site is passed by ID so that the confirmation link points back at the
    site the visitor subscribed on.
    """
    if was_authenticated or not person_created:
        return
    if site is None:
        logger.error(
            "No site matched the subscription of %s to tag %s, so no confirmation email "
            "can be sent",
            person.email,
            tag,
        )
        return
    async_task(
        "underground_crm.tasks.send_subscription_confirmation",
        str(person.pk),
        str(tag.pk),
        site.pk,
    )


@receiver(email_confirmed)
def on_email_confirmed(sender, request, email_address: EmailAddress, **kwargs) -> None:
    """
    Record on the Person what following an allauth confirmation link proves,
    then delete the EmailAddress row, which exists only to give that link
    something to point at (see send_subscription_confirmation). Person is the
    only record of whether an address works: email_is_bad=False means that it
    has received email.

    allauth sends this signal for every confirmed address, including the one
    somebody verifies after signing up for a website account. That proves the
    address works too, but it is not consent to email, so only a person who
    has subscribed to a mailing list anonymously, without logging in, is also
    opted in; subscription_view leaves their opt-in unset until now.

    The row can be deleted here because allauth does nothing more with it once
    the signal has been sent, beyond reading its address for a flash message.
    """
    person = email_address.user
    if email_address.email.casefold() == person.email.casefold():
        person.email_is_bad = False
        update_fields = ["email_is_bad"]
        if (
            not person.email_opt_in
            and PersonTag.objects.filter(content_object=person, was_authenticated=False).exists()
        ):
            person.email_opt_in = True
            update_fields.append("email_opt_in")
        person.save(update_fields=update_fields)
        logger.info("%s has confirmed their email address", person.email)
    email_address.delete()


@receiver(pre_delete, sender=Tag)
def on_tag_pre_delete(sender: type[Tag], instance: Tag, **kwargs) -> None:
    """
    Blocks deletion of protected tags (e.g. "Volunteer") through every ORM
    path — admin, the Wagtail snippet UI, the API, cascades — not just the
    admin/snippet "delete" button. An admin can still remove a protected tag
    by dropping into the Django shell/console and either flipping
    is_protected off first or deleting via raw SQL.
    """
    if instance.is_protected:
        raise PermissionDenied(f'"{instance.name}" is a protected tag and cannot be deleted.')
