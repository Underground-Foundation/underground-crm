"""
The /health/ endpoint (underground_crm/views/health.py) and the Django-Q check that it
runs alongside django-health-check's own database and cache checks.

These tests talk to the Redis server named by REDIS_URL, as the deployment does: the
broker's record of running clusters is what the Django-Q check reads, so a test that
simulated the broker would only be testing the simulation.
"""

import uuid
from http import HTTPStatus

from django.test import TestCase
from django.urls import reverse
from django.utils.cache import get_max_age
from django_q.brokers import get_broker
from django_q.signing import SignedPackage
from django_q.status import Stat, Status
from health_check.exceptions import ServiceUnavailable

from underground_crm.health_checks import DjangoQCluster
from underground_crm.views.health import CachedHealthCheckView

# A process ID that a worker's sentinel process could plausibly have.
CLUSTER_SENTINEL_PID = 4127
# The lifetime that django_q.status.Stat.save gives every status it writes.
CLUSTER_STATUS_EXPIRY_SECONDS = 3
# What django-health-check's JSON format reports for a check that passed.
PASSING_CHECK = "OK"
# An Accept header that none of django-health-check's formats satisfies.
IMAGE_ACCEPT_HEADER = "image/avif,image/webp"


class DjangoQClusterCheckTest(TestCase):
    def setUp(self) -> None:
        self.broker = get_broker()
        self.cluster_id = str(uuid.uuid4())
        self.status_key = Stat.get_key(self.cluster_id)
        self.addCleanup(self.broker.connection.delete, self.status_key)

    def report_running_cluster(self) -> None:
        """Writes a cluster status to the broker as a running cluster's guard loop does."""
        status = Status(pid=CLUSTER_SENTINEL_PID, cluster_id=self.cluster_id)
        self.broker.set_stat(
            self.status_key,
            SignedPackage.dumps(status, True),
            CLUSTER_STATUS_EXPIRY_SECONDS,
        )

    def test_passes_while_a_cluster_reports_its_status(self) -> None:
        self.report_running_cluster()
        DjangoQCluster().run()

    def test_fails_when_no_cluster_reports_its_status(self) -> None:
        if Stat.get_all():
            self.skipTest(
                "A Django-Q cluster is running against this Redis server, so the absence "
                "of one cannot be observed."
            )
        with self.assertRaises(ServiceUnavailable):
            DjangoQCluster().run()

    def test_health_endpoint_passes_while_a_cluster_reports_its_status(self) -> None:
        self.report_running_cluster()
        response = self.client.get(reverse("health_check"), {"format": "json"})
        self.assertEqual(
            response.status_code,
            HTTPStatus.OK,
            f"The database, Redis and a Django-Q cluster are all available: {response.json()}",
        )
        self.assertEqual(
            set(response.json().values()),
            {PASSING_CHECK},
            "Every check should pass while every service is available.",
        )


class HealthEndpointCachingTest(TestCase):
    def test_result_may_be_cached_for_max_age(self) -> None:
        response = self.client.get(reverse("health_check"), {"format": "json"})
        self.assertIn(
            response.status_code,
            CachedHealthCheckView.cacheable_statuses,
            "The status reports the outcome of the checks, whichever way they went.",
        )
        self.assertEqual(get_max_age(response), int(CachedHealthCheckView.max_age.total_seconds()))
        self.assertIn("public", response["Cache-Control"])
        self.assertNotIn(
            "Expires",
            response,
            "HealthCheckView's Expires header, which is already in the past, must not "
            "survive alongside the max-age that replaces it.",
        )

    def test_unacceptable_format_is_not_cached(self) -> None:
        response = self.client.get(reverse("health_check"), HTTP_ACCEPT=IMAGE_ACCEPT_HEADER)
        self.assertEqual(response.status_code, HTTPStatus.NOT_ACCEPTABLE)
        self.assertIn(
            "no-store",
            response["Cache-Control"],
            "A 406 describes only the request that caused it, so no cache may serve it to "
            "other clients.",
        )
