from django.urls import path

from .views.address import address_suggestion_view
from .views.auth import login_view, logout_view, signup_view
from .views.membership import add_membership_view, cancel_membership_view
from .views.subscribe import subscription_view

app_name = "underground_crm"

urlpatterns = [
    # Creating a website account − not necessarily a full member of the movement
    path("join/", signup_view, name="signup"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    # Joining a mailing list by applying a Tag − not a website account
    path("subscribe/", subscription_view, name="subscribe"),
    path("addresses/suggestions/", address_suggestion_view, name="address_suggestions"),
    path(
        "memberships/add/<int:page_id>/",
        add_membership_view,
        name="add_membership",
    ),
    path(
        "memberships/<uuid:membership_id>/cancel/",
        cancel_membership_view,
        name="cancel_membership",
    ),
]
