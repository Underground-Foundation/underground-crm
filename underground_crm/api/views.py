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
    if request.user.is_authenticated:
        user = cast(settings.AUTH_USER_MODEL, request.user)
        return Response(
            {"authenticated": True, "name": user.full_name, "email_address": user.email}
        )
    return Response({"authenticated": False})
