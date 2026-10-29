"""url_changed hashes the body, or an extracted region, and baselines once."""

from datetime import datetime, timezone

from wakegate.checks.url_changed import evaluate
from wakegate.errors import CheckError

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _page(inner, noise):
    return f"noise {noise} START {inner} END tail {noise}".encode()


class UrlChangedTest(TempCase):
    def test_full_body_change_is_idempotent(self):
        server = self.server()
        options = {"url": server.url + "/page", "timeout": 5, "extract": None}
        server.httpd.routes["/page"] = (200, b"v1")
        first = evaluate(options, {}, NOW)
        self.assertFalse(first.fired)
        self.assertTrue(first.summary.startswith("baseline "))
        self.assertFalse(evaluate(options, first.state, NOW).fired)
        server.httpd.routes["/page"] = (200, b"v2")
        changed = evaluate(options, first.state, NOW)
        self.assertTrue(changed.fired)
        self.assertIn("->", changed.summary)
        self.assertFalse(evaluate(options, changed.state, NOW).fired)

    def test_start_end_extract_ignores_surrounding_noise(self):
        server = self.server()
        options = {
            "url": server.url + "/page",
            "timeout": 5,
            "extract": {"start": "START ", "end": " END"},
        }
        server.httpd.routes["/page"] = (200, _page("one", "aaa"))
        first = evaluate(options, {}, NOW)
        server.httpd.routes["/page"] = (200, _page("one", "bbb"))
        self.assertFalse(evaluate(options, first.state, NOW).fired)
        server.httpd.routes["/page"] = (200, _page("two", "bbb"))
        changed = evaluate(options, first.state, NOW)
        self.assertTrue(changed.fired)
        self.assertFalse(evaluate(options, changed.state, NOW).fired)

    def test_regex_extract_and_missing_match(self):
        server = self.server()
        options = {
            "url": server.url + "/page",
            "timeout": 5,
            "extract": {"regex": r"(?s)START (.*?) END"},
        }
        server.httpd.routes["/page"] = (200, _page("one", "aaa"))
        first = evaluate(options, {}, NOW)
        server.httpd.routes["/page"] = (200, _page("one", "zzz"))
        self.assertFalse(evaluate(options, first.state, NOW).fired)
        server.httpd.routes["/page"] = (200, _page("two\nlines", "zzz"))
        self.assertTrue(evaluate(options, first.state, NOW).fired)

        server.httpd.routes["/page"] = (200, b"nothing here")
        with self.assertRaises(CheckError) as caught:
            evaluate(options, first.state, NOW)
        self.assertIn("did not match", str(caught.exception))

    def test_http_error_omits_the_url(self):
        server = self.server()
        server.httpd.routes["/SUPERSECRETTOKEN"] = (404, b"missing")
        with self.assertRaises(CheckError) as caught:
            evaluate({"url": server.url + "/SUPERSECRETTOKEN", "timeout": 5, "extract": None}, {}, NOW)
        self.assertNotIn("SUPERSECRETTOKEN", str(caught.exception))
