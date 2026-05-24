"""
Tests for the django-q2 cluster configuration.

Verifies that the Q_CLUSTER setting is structured correctly for process
separation between CRM and email workloads.
"""

import unittest

from django.conf import settings


class QClusterConfigTest(unittest.TestCase):
    """Verify the django-q2 cluster configuration in settings."""

    def test_default_cluster_is_named_underground_crm(self):
        self.assertEqual(settings.Q_CLUSTER["name"], "underground_crm")

    def test_default_cluster_has_required_keys(self):
        required = {"name", "redis", "workers", "queue_limit", "timeout", "retry"}
        self.assertTrue(
            required.issubset(settings.Q_CLUSTER.keys()),
            f"Missing keys in Q_CLUSTER: {required - settings.Q_CLUSTER.keys()}",
        )

    def test_alt_clusters_contains_email(self):
        alt = settings.Q_CLUSTER.get("ALT_CLUSTERS", {})
        self.assertIn("email", alt, "Expected an 'email' entry in ALT_CLUSTERS.")

    def test_email_cluster_is_named_underground_email(self):
        email = settings.Q_CLUSTER["ALT_CLUSTERS"]["email"]
        self.assertEqual(email["name"], "underground_email")

    def test_email_cluster_has_required_keys(self):
        required = {"name", "redis", "workers", "queue_limit", "timeout", "retry"}
        email = settings.Q_CLUSTER["ALT_CLUSTERS"]["email"]
        self.assertTrue(
            required.issubset(email.keys()),
            f"Missing keys in email cluster: {required - email.keys()}",
        )

    def test_email_cluster_timeout_exceeds_crm_timeout(self):
        """Email tasks (campaign dispatch) need a much longer timeout than CRM tasks."""
        crm_timeout = settings.Q_CLUSTER["timeout"]
        email_timeout = settings.Q_CLUSTER["ALT_CLUSTERS"]["email"]["timeout"]
        self.assertGreater(
            email_timeout,
            crm_timeout,
            "Email cluster timeout should be greater than CRM cluster timeout.",
        )

    def test_database_connection_max_age_is_set(self):
        if "sqlite3" in settings.DATABASES["default"]["ENGINE"]:
            self.skipTest("Skipping DB connection checks for SQLite")
        conn_max_age = settings.DATABASES["default"].get("CONN_MAX_AGE")
        self.assertIsNotNone(conn_max_age, "CONN_MAX_AGE should be set on the default database.")
        self.assertGreaterEqual(conn_max_age, 0)

    def test_database_health_checks_enabled(self):
        if "sqlite3" in settings.DATABASES["default"]["ENGINE"]:
            self.skipTest("Skipping DB connection checks for SQLite")
        self.assertTrue(
            settings.DATABASES["default"].get("CONN_HEALTH_CHECKS"),
            "CONN_HEALTH_CHECKS should be True.",
        )

