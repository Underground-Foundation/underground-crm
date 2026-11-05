"""
Default root URL configuration for underground_crm deployments.

Theme projects may override ROOT_URLCONF if they need to add their own URL
patterns, or they can include this module from their own urls.py.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from wagtail import urls as wagtail_urls
from wagtail.contrib.sitemaps.views import sitemap
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

from underground_crm.views.robots_txt import robots_txt_view

urlpatterns = [
    # robots.txt advertises the sitemap by reversing this name (ROBOTS_SITEMAP_VIEW_NAME).
    path("sitemap.xml", sitemap, name="sitemap"),
    path("robots.txt", robots_txt_view, name="robots_rule_list"),
    path("api/", include("underground_crm.api.urls")),
    path("django-admin/", admin.site.urls),
    path("cms/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("account/", include("allauth.urls")),
    path("", include("underground_crm.urls")),
    path("", include("underground_email.urls")),
    path("payments/", include("underground_payments.urls")),
    path("", include(wagtail_urls)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
