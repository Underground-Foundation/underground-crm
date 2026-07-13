from typing import Any

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.http import HttpRequest


class PersonSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Bridges allauth's social account flow to the Person model.

    Maps the common first_name / last_name / picture keys from the provider's
    normalised data dict into the Person record when the provider supplies
    them.  The parent class already handles email, so only these extra fields
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
        if not person.profile_picture_url:
            person.profile_picture_url = (
                data.get("picture") or self._facebook_picture_url(sociallogin) or None
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
