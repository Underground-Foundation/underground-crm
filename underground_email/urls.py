from django.urls import path

from underground_email.views.email_click_handler import unsubscription_view

urlpatterns = [
    path("unsubscribe", unsubscription_view, name="unsubscribe"),
]
