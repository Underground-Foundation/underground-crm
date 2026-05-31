from typing import cast

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from ..models import Address, Donation, Engagement, Interaction, PersonNote, Tag
from .serializers import (
    AddressSerializer,
    DonationSerializer,
    EngagementSerializer,
    InteractionSerializer,
    PersonNoteSerializer,
    TagSerializer,
    UnverifiedAddressSerializer,
)


class TagViewSet(ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class PersonNoteViewSet(ModelViewSet):
    serializer_class = PersonNoteSerializer

    def get_queryset(self):
        qs = PersonNote.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class InteractionViewSet(ModelViewSet):
    serializer_class = InteractionSerializer

    def get_queryset(self):
        qs = Interaction.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class EngagementViewSet(ModelViewSet):
    serializer_class = EngagementSerializer

    def get_queryset(self):
        qs = Engagement.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class DonationViewSet(ModelViewSet):
    serializer_class = DonationSerializer

    def get_queryset(self):
        qs = Donation.objects.all()
        person_id = self.request.query_params.get("person")
        if person_id:
            qs = qs.filter(person_id=person_id)
        return qs


class AddressViewSet(ModelViewSet):
    queryset = Address.objects.all()
    serializer_class = AddressSerializer
    permission_classes = [IsAdminUser]


@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False})

    user = cast(settings.AUTH_USER_MODEL, request.user)
    data: dict = {"authenticated": True, "name": user.full_name, "email_address": user.email}

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
