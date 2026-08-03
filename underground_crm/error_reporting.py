"""
Error reporting for queued (django-q2) tasks.

django-q2's worker swallows a failed task's exception into the task result and
never re-raises it or logs it at ERROR level, so neither Sentry's Django
integration nor its logging integration can see it.  The only hook is
``Q_CLUSTER["error_reporter"]``, which django-q2 resolves through the
``djangoq.errorreporters`` entry point group; ``pyproject.toml`` registers this
module's reporter under the name ``underground_crm``.

The third-party ``django-q-sentry`` package fills the same role, but its
reporter calls ``sentry_sdk.init()`` in its constructor.  django-q2 builds its
reporters at import time of ``django_q.conf``, which happens in *every* process
that loads the app registry — not just the cluster — so that constructor
replaces the SDK client configured in ``settings``, silently discarding every
option ``settings`` passed.  This reporter deliberately does no initialization
of its own: it reports to whichever client the settings module already built.
"""

import logging
import sys
from types import TracebackType
from typing import Any

import sentry_sdk

_logger = logging.getLogger(__name__)

# What sys.exc_info() returns once its first element is known not to be None.
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _task_from_traceback(traceback: TracebackType | None) -> dict[str, Any]:
    """
    Recovers django-q2's task dictionary from the stack of the exception being
    handled.  The worker holds the task in a local variable named ``task`` while
    it calls the reporter, but it passes nothing to ``report()``, so walking the
    frames is the only way to identify which task failed.

    Returns an empty dictionary when no task can be identified, which is what
    happens if django-q2 ever changes that local variable's name.
    """
    if traceback is None:
        return {}

    while traceback.tb_next is not None:
        traceback = traceback.tb_next

    frame: Any = traceback.tb_frame
    while frame is not None:
        candidate = frame.f_locals.get("task")
        if isinstance(candidate, dict) and candidate.get("id") and candidate.get("func"):
            return candidate
        frame = frame.f_back

    return {}


# pylint: disable=too-few-public-methods
# django-q2 calls exactly one method on a reporter, report(); the interface is not ours
# to widen.
class QueuedTaskReporter:
    """
    Reports a failed queued task to the error tracker (GlitchTip or Sentry).

    django-q2 instantiates this once per process from the configuration
    dictionary given in ``Q_CLUSTER["error_reporter"]``; any keys in that
    dictionary arrive here as keyword arguments.  None are needed — the DSN and
    every other option belong to the ``sentry_sdk.init()`` call in ``settings``
    — so they are accepted and ignored rather than raising, which keeps a
    downstream theme's existing configuration from breaking the cluster.
    """

    def __init__(self, **_ignored_options: Any) -> None:
        pass

    def report(self) -> None:
        exception_info = sys.exc_info()
        if exception_info[0] is None:
            return

        # django-q2 calls this from inside the ``except`` block in django_q/worker.py and
        # does not guard the call.  Anything raised here would therefore escape that block,
        # so the worker would never reach the code below it that puts the task's result on
        # the result queue: a failed task would be left with no recorded outcome and the
        # worker process would die, purely because reporting the failure went wrong.  The
        # error tracker must not be able to break the thing it is watching, so every
        # failure to report is contained and downgraded to a local log record.
        try:
            self._capture(exception_info)
        except Exception:  # pylint: disable=broad-except
            _logger.exception("Failed to report a queued task's exception to the error tracker")

    @staticmethod
    def _capture(exception_info: ExcInfo) -> None:
        task = _task_from_traceback(exception_info[2])
        # A worker process is long-lived and handles many tasks, so these tags are
        # confined to a temporary scope; setting them on the isolation scope would
        # leave the failed task's name attached to every later event from the
        # same process.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("django_q_task_name", task.get("name", ""))
            scope.set_tag("django_q_task_func", task.get("func", ""))
            scope.set_context(
                "django_q",
                {
                    "task_id": task.get("id", ""),
                    "task_name": task.get("name", ""),
                    "task_func": task.get("func", ""),
                    "task_group": task.get("group", ""),
                },
            )
            sentry_sdk.capture_exception(error=exception_info)
