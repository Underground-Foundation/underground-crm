from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import Signal, receiver
from django_q.tasks import async_task

from .models.address import Address
from .models.pages import EventGuest
from .models.person import Tag

# Sent by views.subscribe.subscribe_view after a mailing-list Tag is applied.
# Kwargs: person, tag, was_authenticated, person_created (True when the
# subscription created a new placeholder Person for a previously unseen email).
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


@receiver(post_save, sender=EventGuest)
def on_event_guest_saved(
    sender: type[EventGuest], instance: EventGuest, created: bool, **kwargs
) -> None:
    if created:
        async_task("underground_crm.tasks.record_rsvp_engagement", str(instance.pk))


@receiver(subscription_created)
def on_subscription_created(
    sender, person, tag: Tag, was_authenticated: bool, person_created: bool, **kwargs
) -> None:
    """
    When an unauthenticated visitor subscribes with a previously unseen email
    address, queue a "confirm your subscription" verification email. Known
    email addresses (matched or authenticated) skip it — the address either
    belongs to an established record or the visitor proved it by logging in.
    """
    if was_authenticated or not person_created:
        return
    async_task("underground_crm.tasks.send_subscription_confirmation", str(person.pk), str(tag.pk))


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
