from typing import Any

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.db import transaction
from django.http import HttpRequest
from django_q.tasks import async_task


class PersonSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Bridges allauth's social account flow to the Person model.

    Maps the common first_name / last_name keys from the provider's
    normalised data dict into the Person record when the provider supplies
    them, and queues an import of the provider's picture.  The parent class already handles email, so only these extra fields
    need special treatment here.
    """

    def populate_user(
        self,
        request: HttpRequest,
        sociallogin: Any,
        data: dict[str, Any],
    ) -> Any:
        person = super().populate_user(request, sociallogin, data)
        if not person.first_name:
            person.first_name = data.get("first_name") or ""
        if not person.last_name:
            person.last_name = data.get("last_name") or ""
        # Not a database field: the provider's URL is only kept long enough for
        # save_user to hand it to the task that copies the picture.
        person._profile_picture_url = (
            data.get("picture") or self._facebook_picture_url(sociallogin) or None
        )
        return person

    def save_user(
        self,
        request: HttpRequest,
        sociallogin: Any,
        form: Any = None,
    ) -> Any:
        """
        Queue a copy of the profile picture into our own media storage, since
        the provider's URL expires. This can't happen in populate_user, which
        runs before the Person has been saved; on_commit makes sure the task
        never looks for a Person whose signup was rolled back.
        """
        person = super().save_user(request, sociallogin, form)
        url = getattr(person, "_profile_picture_url", None)
        if url and not person.profile_picture_file:
            person_id = str(person.pk)
            transaction.on_commit(
                lambda: async_task("underground_crm.tasks.import_profile_picture", person_id, url)
            )
        return person

    @staticmethod
    def _facebook_picture_url(sociallogin: Any) -> str | None:
        """
        Facebook's Graph API response nests the picture URL rather than exposing
        it as a top-level "picture" key, and allauth's FacebookProvider doesn't
        surface it via extract_common_fields, so it isn't present in `data`.
        Requires "picture.type(large)" to be requested via the FACEBOOK FIELDS
        setting; see SOCIALACCOUNT_PROVIDERS in settings.py.
        """
        if sociallogin.account.provider != "facebook":
            return None
        picture = sociallogin.account.extra_data.get("picture") or {}
        return picture.get("data", {}).get("url") or None
