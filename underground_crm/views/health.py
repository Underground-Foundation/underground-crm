"""
The /health/ endpoint, which reports whether this deployment can serve pages and run its
queued tasks.

The response formats (``?format=json``, ``?format=text`` and the HTML default) and the
status codes (200 when every check passes, 500 otherwise) are django-health-check's; see
https://codingjoe.dev/django-health-check/usage/.
"""

import datetime

from django.utils.cache import patch_cache_control
from health_check.views import HealthCheckView


class CachedHealthCheckView(HealthCheckView):
    checks = (
        "health_check.Database",
        # The default cache is Redis, which is also Django-Q's broker.
        "health_check.Cache",
        "underground_crm.health_checks.DjangoQCluster",
    )

    # How long a shared cache (Cloudflare) or a browser may reuse a result. HealthCheckView
    # marks every response as uncacheable, but a result that is a few seconds old answers
    # the question just as well, and letting a cache serve it bounds how often the checks
    # reach the database, however often the URL is requested.
    max_age = datetime.timedelta(seconds=30)

    # The statuses that report the outcome of the checks. Any other status, such as 406 for
    # an Accept header that no format satisfies, describes only the request that caused it,
    # and Cloudflare does not key its cache on Accept, so caching it would serve that error
    # to every other client.
    cacheable_statuses = frozenset({200, 500})

    async def dispatch(self, request, *args, **kwargs):
        response = await super().dispatch(request, *args, **kwargs)
        if request.method in ("GET", "HEAD") and response.status_code in self.cacheable_statuses:
            del response["Cache-Control"]
            del response["Expires"]
            patch_cache_control(response, public=True, max_age=int(self.max_age.total_seconds()))
        return response


health_check_view = CachedHealthCheckView.as_view()
