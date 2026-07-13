from django.urls import path

from .views.address import address_suggestion_view
from .views.auth import login_view, logout_view, signup_view
from .views.membership import cancel_membership_view
from .views.subscribe import subscribe_view

app_name = "underground_crm"

urlpatterns = [
    # Creating a website account − not necessarily a full member of the movement
    path("join/", signup_view, name="signup"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    # Joining a mailing list by applying a Tag − not a website account
    path("subscribe/", subscribe_view, name="subscribe"),
    path("addresses/suggestions/", address_suggestion_view, name="address_suggestions"),
    path(
        "memberships/<uuid:membership_id>/cancel/",
        cancel_membership_view,
        name="cancel_membership",
    ),
]
