from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from django.urls import include, path

from .discord_membership import discord_membership
from .integration_views import people as integration_people
from .integration_views import person_detail as integration_person_detail
from .views import (
    AddressViewSet,
    DonationViewSet,
    EngagementViewSet,
    InteractionViewSet,
    MembershipViewSet,
    PersonNoteViewSet,
    TagViewSet,
    me,
)

router = DefaultRouter()
router.register("tags", TagViewSet, basename="tag")
router.register("notes", PersonNoteViewSet, basename="note")
router.register("interactions", InteractionViewSet, basename="interaction")
router.register("engagements", EngagementViewSet, basename="engagement")
router.register("donations", DonationViewSet, basename="donation")
router.register("addresses", AddressViewSet, basename="address")
router.register("memberships", MembershipViewSet, basename="membership")

urlpatterns = [
    path(
        "integrations/discord/membership/<str:discord_id>/",
        discord_membership,
        name="discord-membership",
    ),
    # Versioned, read-only feed for server-to-server integrations. It is
    # separate from the staff REST API so a service credential can be scoped to
    # this contract instead of a staff account's entire surface.
    path("integrations/v1/people/", integration_people, name="integration-people-v1"),
    path(
        "integrations/v1/people/<uuid:person_id>/",
        integration_person_detail,
        name="integration-person-detail-v1",
    ),
    path("", include(router.urls)),
    path("me/", me, name="me"),
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
