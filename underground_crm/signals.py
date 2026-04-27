from django.db.models.signals import post_save
from django.dispatch import receiver
from django_q.tasks import async_task

from .models.pages import EventGuest


@receiver(post_save, sender=EventGuest)
def on_event_guest_saved(
    sender: type[EventGuest], instance: EventGuest, created: bool, **kwargs
) -> None:
    if created:
        async_task("underground_crm.tasks.record_rsvp_engagement", str(instance.pk))
