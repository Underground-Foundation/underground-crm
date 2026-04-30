"""
Default root URL configuration for underground_crm deployments.

Theme projects may override ROOT_URLCONF if they need to add their own URL
patterns, or they can include this module from their own urls.py.
"""

from django.contrib import admin
from django.urls import include, path
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

urlpatterns = [
    path("api/", include("underground_crm.api.urls")),
    path("django-admin/", admin.site.urls),
    path("cms/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("account/", include("underground_crm.urls")),
    path("", include(wagtail_urls)),
]
