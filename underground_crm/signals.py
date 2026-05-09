from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django_q.tasks import async_task

from .models.address import Address
from .models.pages import EventGuest

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
