"""
Tests for the queued-task error reporter and the SENTRY_ENABLED wiring around it.

django-q2's worker turns a failed task's exception into a string in the task result and
carries on: it never re-raises and never logs at ERROR level, so neither Sentry's Django
integration nor its logging integration can see it.  ``underground_crm.error_reporting``
is the only thing that makes those failures visible at all, which is what makes a
regression here so hard to notice -- the symptom is an error tracker with nothing in it,
which is indistinguishable from a deployment that simply is not failing.
"""

import os
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path
from unittest import mock

import django_q.worker
import sentry_sdk
from django.test import SimpleTestCase
from sentry_sdk import get_global_scope
from sentry_sdk.transport import Transport

from underground_crm.error_reporting import QueuedTaskReporter

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TASK = {
    "id": "b4c1f0de-0000-4000-8000-000000000001",
    "func": "underground_email.tasks.send_campaign",
    "name": "brave-mountain-tango",
    "group": "email",
}


class _CollectingTransport(Transport):
    """Keeps envelope payloads in memory instead of sending them anywhere."""

    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            payload = item.payload.json
            if payload is not None and "exception" in payload:
                self.events.append(payload)

    def flush(self, *_args, **_kwargs):
        pass

    def kill(self):
        pass


class QueuedTaskReporterTests(SimpleTestCase):
    def setUp(self):
        self.transport = _CollectingTransport()
        # Restores whatever client the settings module built, so a test run that does have
        # a real SENTRY_DSN configured is not left reporting into this transport afterwards.
        self.addCleanup(get_global_scope().set_client, get_global_scope().client)
        sentry_sdk.init(
            dsn="https://key@localhost/1",
            transport=self.transport,
            auto_session_tracking=False,
            enable_logs=False,
        )
        self.reporter = QueuedTaskReporter()

    def _report_like_the_worker_does(self, task):  # pylint: disable=unused-argument
        """
        Calls report() the way django_q/worker.py does: from inside an ``except`` block,
        with the task dictionary held in a local variable named ``task``.  The parameter
        name matters -- it is what the reporter walks the stack looking for.
        """
        try:
            raise ValueError("queued task exploded")
        except ValueError:
            self.reporter.report()

    def test_reports_exception_with_task_identity(self):
        self._report_like_the_worker_does(_TASK)

        self.assertEqual(len(self.transport.events), 1)
        event = self.transport.events[0]
        self.assertEqual(event["exception"]["values"][-1]["type"], "ValueError")
        self.assertEqual(event["exception"]["values"][-1]["value"], "queued task exploded")
        self.assertEqual(event["tags"]["django_q_task_name"], _TASK["name"])
        self.assertEqual(event["tags"]["django_q_task_func"], _TASK["func"])
        self.assertEqual(
            event["contexts"]["django_q"],
            {
                "task_id": _TASK["id"],
                "task_name": _TASK["name"],
                "task_func": _TASK["func"],
                "task_group": _TASK["group"],
            },
        )

    def test_reports_exception_even_when_the_task_cannot_be_identified(self):
        """
        The stack walk is best-effort.  Losing the task's identity must cost only the
        tags, never the exception itself -- a stack trace with no task name is still far
        more use than silence.
        """
        try:
            raise ValueError("no task local in this frame")
        except ValueError:
            self.reporter.report()

        self.assertEqual(len(self.transport.events), 1)
        event = self.transport.events[0]
        self.assertEqual(event["exception"]["values"][-1]["value"], "no task local in this frame")
        self.assertEqual(event["tags"]["django_q_task_name"], "")
        self.assertEqual(event["contexts"]["django_q"]["task_id"], "")

    def test_does_nothing_without_an_active_exception(self):
        self.reporter.report()

        self.assertEqual(self.transport.events, [])

    def test_task_identity_does_not_leak_into_later_events(self):
        """
        A qcluster worker process is long-lived and handles many tasks in sequence, so
        tags set for one failure must not still be attached to an unrelated event
        reported minutes later.  This is what the temporary scope in report() is for.
        """
        self._report_like_the_worker_does(_TASK)

        try:
            raise RuntimeError("something unrelated, later")
        except RuntimeError:
            sentry_sdk.capture_exception()

        self.assertEqual(len(self.transport.events), 2)
        later_event = self.transport.events[1]
        self.assertEqual(
            later_event["exception"]["values"][-1]["value"], "something unrelated, later"
        )
        leaked = {k: v for k, v in later_event.get("tags", {}).items() if k.startswith("django_q")}
        self.assertEqual(leaked, {})
        self.assertNotIn("django_q", later_event.get("contexts", {}))

    def test_unknown_configuration_options_are_accepted(self):
        """
        django-q2 passes the contents of Q_CLUSTER["error_reporter"]["underground_crm"]
        to the constructor as keyword arguments.  A downstream theme that sets one must
        not take the whole cluster down at startup.
        """
        QueuedTaskReporter(dsn="ignored", some_future_option=True)

    def test_a_failure_to_report_cannot_break_the_worker(self):
        """
        django_q/worker.py calls report() from inside its ``except`` block and does not
        guard the call, so anything raised here would escape that block before the worker
        records the task's result -- killing the worker and losing the outcome, because
        reporting the failure failed.  Containment is the whole point.
        """
        with mock.patch.object(
            QueuedTaskReporter, "_capture", side_effect=RuntimeError("error tracker is down")
        ):
            with self.assertLogs("underground_crm.error_reporting", level="ERROR") as captured:
                self._report_like_the_worker_does(_TASK)

        self.assertIn("Failed to report a queued task's exception", captured.output[0])

    def test_reporter_is_registered_as_an_entry_point(self):
        """
        django-q2 resolves Q_CLUSTER["error_reporter"] keys through this entry point
        group rather than by import path, so the reporter is only reachable if
        pyproject.toml advertises it *and* the package has been reinstalled since.
        """
        registered = {
            entry.name: entry.value for entry in entry_points(group="djangoq.errorreporters")
        }
        self.assertIn("underground_crm", registered)
        self.assertEqual(
            registered["underground_crm"],
            "underground_crm.error_reporting:QueuedTaskReporter",
        )
        # Selected by name and iterated, exactly as django_q/conf.py does when it builds
        # the reporter from Q_CLUSTER["error_reporter"].
        (entry,) = entry_points(group="djangoq.errorreporters", name="underground_crm")
        self.assertIs(entry.load(), QueuedTaskReporter)


class DjangoQInternalsCanaryTests(SimpleTestCase):
    """
    Guards the one assumption the reporter makes about django-q2's internals.

    django-q2 passes nothing to report(), so the only way to identify which task failed
    is to walk the worker's stack for a local variable named ``task``.  That is an
    internal detail of django_q/worker.py, not a public API, which is why pyproject.toml
    pins django-q2 below 2.0.  If this test fails after an upgrade the reporter has not
    broken -- exceptions still reach the tracker -- but every event will have lost its
    task name, id and function, so fix the stack walk before shipping the upgrade.
    """

    def test_worker_still_holds_the_task_in_a_local_named_task(self):
        self.assertIn(
            "task",
            django_q.worker.worker.__code__.co_varnames,
            "django_q.worker.worker no longer keeps the task in a local named 'task'; "
            "underground_crm.error_reporting._task_from_traceback needs updating to match.",
        )


def _import_settings_with(**env_overrides):
    """
    Imports underground_crm.settings in a fresh interpreter under the given environment.

    A subprocess is the only honest way to test this: the settings module runs
    sentry_sdk.init() as an import-time side effect, and it has already been imported
    once by the time any test runs.
    """
    env = dict(os.environ)
    for name in ("SENTRY_DSN", "SENTRY_ENABLED"):
        env.pop(name, None)
    env["DJANGO_SETTINGS_MODULE"] = "underground_crm.settings"
    # Nothing here asserts on log delivery, and leaving forwarding on would have the SDK
    # queue the settings module's own import-time INFO records against an unreachable DSN.
    env["SENTRY_LOGS_LEVEL"] = "off"
    env.update(env_overrides)

    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup()\n"
            "import sentry_sdk\n"
            "from django.conf import settings\n"
            "print('CLIENT_ACTIVE', sentry_sdk.get_client().is_active())\n"
            "print('ERROR_REPORTER', bool(settings.Q_CLUSTER.get('error_reporter')))\n",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )


class SentryEnabledSettingsTests(SimpleTestCase):
    """
    SENTRY_ENABLED is the master switch a deployment reaches for, so what it does -- and
    what it refuses to do quietly -- is worth asserting directly.
    """

    def test_enabled_with_a_dsn_activates_the_client_and_the_task_reporter(self):
        # Port 1 is refused immediately rather than resolved over DNS; nothing is sent
        # here, this only checks what the settings module configured.
        result = _import_settings_with(
            SENTRY_ENABLED="true", SENTRY_DSN="http://publickey@127.0.0.1:1/1"
        )

        self.assertIn("CLIENT_ACTIVE True", result.stdout)
        self.assertIn("ERROR_REPORTER True", result.stdout)

    def test_enabled_without_a_dsn_says_so_loudly(self):
        """
        The one combination that would otherwise fail silently: error tracking is
        switched on, no client is ever built, and the deployment looks healthy purely
        because nothing is arriving.
        """
        result = _import_settings_with(SENTRY_ENABLED="true")

        self.assertIn("CLIENT_ACTIVE False", result.stdout)
        self.assertIn("ERROR_REPORTER False", result.stdout)
        self.assertIn("SENTRY_ENABLED is set but SENTRY_DSN is empty", result.stderr)

    def test_disabled_with_a_dsn_is_silent(self):
        """Switching error tracking off deliberately is not a misconfiguration."""
        result = _import_settings_with(
            SENTRY_ENABLED="false", SENTRY_DSN="http://publickey@127.0.0.1:1/1"
        )

        self.assertIn("CLIENT_ACTIVE False", result.stdout)
        self.assertNotIn("SENTRY_ENABLED is set but SENTRY_DSN is empty", result.stderr)

    def test_unset_without_a_dsn_is_silent(self):
        """Ordinary local development and CI: no DSN, no complaint."""
        result = _import_settings_with()

        self.assertIn("CLIENT_ACTIVE False", result.stdout)
        self.assertNotIn("SENTRY_ENABLED is set but SENTRY_DSN is empty", result.stderr)
