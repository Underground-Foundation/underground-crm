"""
Tests for fetch_pages --pages-file: fetching every page named by an all_pages.json,
skipping those already saved, and waiting out a throttled response.
"""

import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command

from underground_crm.management.commands import fetch_pages
from underground_crm.management.commands.fetch_pages import ThrottleAwareOpener

DOMAIN = "fusionparty.org.au"


def page_record(slug: str, url_path: str | None = None) -> dict:
    return {
        "id": slug,
        "attributes": {"slug": slug, "url_path": url_path or f"/{slug}"},
    }


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self) -> bytes:
        return self.body


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x/", code, "error", headers, io.BytesIO(b""))


class FakeOpener:
    """Answers each URL with the body, or the HTTPError, that was registered for it."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.requested: list[str] = []

    def open(self, url):
        self.requested.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


class TestFetchPagesFile(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.domain = str(self.directory / DOMAIN)

    def run_command(self, records, opener, **options):
        pages_file = self.directory / "all_pages.json"
        pages_file.write_text(json.dumps(records))
        with (
            mock.patch.dict(
                "os.environ",
                {"LEGACY_USER_AGENT": "test", "LEGACY_ADMIN_COOKIE_FILE": "cookies.txt"},
            ),
            mock.patch.object(fetch_pages, "_make_html_opener", return_value=opener),
            mock.patch.object(fetch_pages.time, "sleep"),
        ):
            call_command(
                "fetch_pages",
                domain=self.domain,
                pages_file=pages_file,
                stderr=io.StringIO(),
                **options,
            )

    def url(self, path: str) -> str:
        return f"https://{self.domain}/{path}"

    def test_each_page_is_saved_with_its_record_and_html(self):
        opener = FakeOpener({self.url("about"): b"<p>About</p>"})
        self.run_command([page_record("about")], opener)
        self.assertEqual(Path(self.domain, "about.html").read_bytes(), b"<p>About</p>")
        self.assertEqual(
            json.loads(Path(self.domain, "about.json").read_text()), page_record("about")
        )

    def test_a_nested_page_is_fetched_from_its_url_path_but_named_by_its_slug(self):
        opener = FakeOpener({self.url("a/b"): b"<p>Nested</p>"})
        self.run_command([page_record("b", "/a/b")], opener)
        self.assertEqual(opener.requested, [self.url("a/b")])
        self.assertTrue(Path(self.domain, "b.html").exists())

    def test_a_page_already_saved_is_not_fetched_again(self):
        opener = FakeOpener({self.url("about"): b"<p>About</p>"})
        self.run_command([page_record("about")], opener)
        self.run_command([page_record("about")], opener)
        self.assertEqual(len(opener.requested), 1)

    def test_refresh_fetches_a_saved_page_again(self):
        opener = FakeOpener({self.url("about"): b"<p>About</p>"})
        self.run_command([page_record("about")], opener)
        self.run_command([page_record("about")], opener, refresh=True)
        self.assertEqual(len(opener.requested), 2)

    def test_limit_caps_the_pages_fetched_in_one_run(self):
        opener = FakeOpener({self.url(s): b"x" for s in ("a", "b", "c")})
        self.run_command([page_record(s) for s in ("a", "b", "c")], opener, limit=2)
        self.assertEqual(opener.requested, [self.url("a"), self.url("b")])

    def test_a_failed_page_does_not_stop_the_pages_after_it(self):
        opener = FakeOpener({self.url("gone"): http_error(404), self.url("here"): b"x"})
        self.run_command([page_record("gone"), page_record("here")], opener)
        self.assertTrue(Path(self.domain, "here.html").exists())
        self.assertFalse(Path(self.domain, "gone.html").exists())

    def test_a_failed_page_is_fetched_again_on_the_next_run(self):
        opener = FakeOpener({self.url("gone"): http_error(404)})
        self.run_command([page_record("gone")], opener)
        self.run_command([page_record("gone")], opener)
        self.assertEqual(len(opener.requested), 2)

    def test_a_file_that_is_not_a_list_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command({"data": []}, FakeOpener({}))


class TestThrottleAwareOpener(unittest.TestCase):
    def setUp(self):
        sleep = mock.patch.object(fetch_pages.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_a_throttled_response_is_waited_out_and_asked_for_again(self):
        inner = mock.Mock()
        inner.open.side_effect = [http_error(429), FakeResponse(b"ok")]
        response = ThrottleAwareOpener(inner, lambda message: None).open("https://x/")
        self.assertEqual(response.read(), b"ok")
        self.sleep.assert_called_once_with(fetch_pages.THROTTLED_RETRY_BACKOFF)

    def test_retry_after_is_honored(self):
        inner = mock.Mock()
        inner.open.side_effect = [http_error(429, "7"), FakeResponse(b"ok")]
        ThrottleAwareOpener(inner, lambda message: None).open("https://x/")
        self.sleep.assert_called_once_with(7.0)

    def test_a_page_that_stays_throttled_is_reported_as_an_error(self):
        inner = mock.Mock()
        inner.open.side_effect = [http_error(429)] * fetch_pages.THROTTLED_ATTEMPTS
        with self.assertRaises(urllib.error.HTTPError):
            ThrottleAwareOpener(inner, lambda message: None).open("https://x/")

    def test_another_error_is_not_retried(self):
        inner = mock.Mock()
        inner.open.side_effect = http_error(404)
        with self.assertRaises(urllib.error.HTTPError):
            ThrottleAwareOpener(inner, lambda message: None).open("https://x/")
        self.assertEqual(inner.open.call_count, 1)
        self.sleep.assert_not_called()
