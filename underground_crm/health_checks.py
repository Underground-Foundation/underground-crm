"""
Health checks for services that django-health-check does not cover itself.

The checks that the /health/ endpoint runs are listed on
``underground_crm.views.health.CachedHealthCheckView``.
"""

import dataclasses

from django_q.status import Stat
from health_check.base import HealthCheck
from health_check.exceptions import ServiceUnavailable
from redis.exceptions import RedisError


@dataclasses.dataclass
class DjangoQCluster(HealthCheck):
    """
    Passes while at least one Django-Q cluster is running against this deployment's broker.

    A running cluster rewrites its status in the broker every ``guard_cycle`` seconds, each
    time with an expiry of three seconds (``django_q.status.Stat.save``). The status of a
    cluster that has stopped, or whose process has died, is therefore gone from the broker
    within three seconds, so the presence of any status is evidence of a live cluster.
    """

    def run(self) -> None:
        try:
            clusters = Stat.get_all()
        except RedisError as error:
            raise ServiceUnavailable("Unable to reach the Django-Q broker") from error
        if not clusters:
            raise ServiceUnavailable("No Django-Q cluster is running")
