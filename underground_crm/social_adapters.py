from typing import Any

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.http import HttpRequest


class PersonSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Bridges allauth's social account flow to the Person model.

    Maps the common first_name / last_name keys from the provider's normalised
    data dict into the Person record when the provider supplies them.  The
    parent class already handles email, so only name fields need special
    treatment here.
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
        return person
