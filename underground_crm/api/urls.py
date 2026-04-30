from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from django.urls import include, path

from .views import (
    AddressViewSet,
    DonationViewSet,
    EngagementViewSet,
    InteractionViewSet,
    PersonNoteViewSet,
    TagViewSet,
)

router = DefaultRouter()
router.register("tags", TagViewSet, basename="tag")
router.register("notes", PersonNoteViewSet, basename="note")
router.register("interactions", InteractionViewSet, basename="interaction")
router.register("engagements", EngagementViewSet, basename="engagement")
router.register("donations", DonationViewSet, basename="donation")
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = [
    path("", include(router.urls)),
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
