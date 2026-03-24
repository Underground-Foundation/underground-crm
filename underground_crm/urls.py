from django.urls import path

from .views.auth import login_view, logout_view, signup_view

app_name = "underground_crm"

urlpatterns = [
    path("signup/", signup_view, name="signup"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
]
