from typing import cast

from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import mixins
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from ..models import Address, Donation, Engagement, Interaction, Membership, PersonNote, Tag
from .permissions import IsCRMStaff
from .serializers import (
    AddressSerializer,
    DonationSerializer,
    EngagementSerializer,
    InteractionSerializer,
    MembershipSerializer,
    PersonNoteSerializer,
    TagSerializer,
    UnverifiedAddressSerializer,
)


class CRMStaffModelViewSet(ModelViewSet):
    permission_classes = [IsCRMStaff]


class TagViewSet(CRMStaffModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class PersonNoteViewSet(CRMStaffModelViewSet):
    serializer_class = PersonNoteSerializer

    def get_queryset(self):
        qs = PersonNote.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class InteractionViewSet(CRMStaffModelViewSet):
    serializer_class = InteractionSerializer

    def get_queryset(self):
        qs = Interaction.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class EngagementViewSet(CRMStaffModelViewSet):
    serializer_class = EngagementSerializer

    def get_queryset(self):
        qs = Engagement.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class DonationViewSet(CRMStaffModelViewSet):
    serializer_class = DonationSerializer

    def get_queryset(self):
        qs = Donation.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class AddressViewSet(CRMStaffModelViewSet):
    queryset = Address.objects.all()
    serializer_class = AddressSerializer


class MembershipViewSet(mixins.CreateModelMixin, mixins.DestroyModelMixin, GenericViewSet):
    """
    Self-service create/delete of the requesting visitor's own memberships —
    unlike every other viewset in this module, this is not a CRM-staff
    endpoint, so it carries IsAuthenticated rather than IsCRMStaff, and
    get_queryset never lets a visitor address a membership beyond their own.
    No list/retrieve/update: the RegistrationPage's memberships section is
    the visitor-facing read surface, and the page's own "Cancel membership"
    button (see views.membership.cancel_membership_view) softly expires a
    membership rather than deleting the record, so a delete through this API
    is a distinct, harder action from a page cancellation.
    """

    serializer_class = MembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Membership.objects.filter(person=self.request.user)

    def perform_create(self, serializer):
        serializer.save(person=self.request.user, started_at=timezone.now())


# ensure_csrf_cookie: the islands that call this endpoint (profile dropdown,
# donation form, subscription form) follow up with fetch() POSTs that send
# X-CSRFToken from the csrftoken cookie — but their pages are cacheable and
# never render a {% csrf_token %}, so this auth-discovery GET is what plants
# the cookie.
@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False})

    user = cast(settings.AUTH_USER_MODEL, request.user)
    data: dict = {"authenticated": True, "name": user.full_name, "email_address": user.email}

    # ?has-tag=<slug> (repeatable) lets subscription UI ask whether the
    # visitor already carries specific mailing-list tags without a page render
    # leaking user-specific state. Only tags whitelisted in
    # SELF_QUERYABLE_TAGS may be asked about — the rest of a person's tags are
    # internal CRM data and must never be exposed to them. The response lists
    # the slugs of the queried, whitelisted tags the visitor carries;
    # anything else (not carried, not whitelisted, misspelt) is simply absent.
    queried_slugs = request.GET.getlist("has-tag")
    if queried_slugs:
        data["tags"] = sorted(
            user.tags.filter(
                slug__in=queried_slugs, name__in=settings.SELF_QUERYABLE_TAGS
            ).values_list("slug", flat=True)
        )

    if request.GET.get("context") == "billing":
        billing = user.billing_address
        data.update(
            {
                "first_name": user.first_name or "",
                "middle_name": user.middle_name or "",
                "last_name": user.last_name or "",
                "phone": str(user.mobile_number or user.phone_number or ""),
                "billing_address": UnverifiedAddressSerializer(billing).data if billing else None,
            }
        )
        for attr, key in [
            ("home_address", "home_address"),
            ("mailing_address", "mailing_address"),
            ("registered_address", "registered_address"),
        ]:
            addr = getattr(user, attr)
            if addr is None:
                data[key] = None
            else:
                entry = dict(UnverifiedAddressSerializer(addr).data)
                entry["same_as_billing"] = bool(billing and addr.is_equivalent(billing))
                data[key] = entry

    return Response(data)
