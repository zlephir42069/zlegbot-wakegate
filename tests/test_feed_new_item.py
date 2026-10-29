"""feed_new_item baselines, then fires only for unseen ids."""

from datetime import datetime, timezone
from unittest.mock import patch
from xml.etree import ElementTree as ET

from wakegate.checks.feed_new_item import evaluate, parse_feed
from wakegate.errors import CheckError

from support import TempCase

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>Hello   world</title>
      <link>https://example.com/hello</link>
      <guid>urn:hello</guid>
    </item>
    <item>
      <title>Link only</title>
      <link>https://example.com/link-only</link>
    </item>
    <item>
      <title>No identity</title>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example</title>
  <entry>
    <title>Atom hello</title>
    <id>urn:atom:hello</id>
    <link href="https://example.com/ignore" rel="via"/>
    <link href="https://example.com/atom-hello" rel="alternate"/>
  </entry>
</feed>
"""


def _rss(items):
    body = ["<?xml version='1.0'?><rss><channel>"]
    for ident, title in items:
        body.append(f"<item><guid>{ident}</guid><title>{title}</title></item>")
    body.append("</channel></rss>")
    return "".join(body).encode()


class FeedParseTest(TempCase):
    def test_rss_and_atom_identity(self):
        rss = parse_feed(RSS)
        self.assertEqual(rss[0], {"id": "urn:hello", "title": "Hello world"})
        self.assertEqual(rss[1]["id"], "https://example.com/link-only")
        self.assertEqual(len(rss), 2)

        atom = parse_feed(ATOM)
        self.assertEqual(atom, [{"id": "urn:atom:hello", "title": "Atom hello"}])
        self.assertIsInstance(ET.fromstring(ATOM), ET.Element)

    def test_malformed_xml(self):
        with self.assertRaises(CheckError):
            parse_feed(b"<rss>")

    def test_http_baseline_then_new_item_then_quiet(self):
        server = self.server()
        server.httpd.routes["/rss"] = (200, RSS)
        options = {"url": server.url + "/rss", "timeout": 5}

        first = evaluate(options, {}, NOW)
        self.assertFalse(first.fired)
        self.assertEqual(first.summary, "baseline, 2 items")
        self.assertEqual(set(first.state["seen"]), {"urn:hello", "https://example.com/link-only"})

        second = evaluate(options, first.state, NOW)
        self.assertFalse(second.fired)
        self.assertEqual(second.summary, "no new items")

        extra = RSS.replace(b"</channel>", b"<item><guid>urn:new</guid><title>Fresh</title></item></channel>")
        server.httpd.routes["/rss"] = (200, extra)
        third = evaluate(options, second.state, NOW)
        self.assertTrue(third.fired)
        self.assertEqual(third.summary, "1 new item: Fresh")
        fourth = evaluate(options, third.state, NOW)
        self.assertFalse(fourth.fired)

    def test_atom_over_http(self):
        server = self.server()
        server.httpd.routes["/atom"] = (200, ATOM)
        result = evaluate({"url": server.url + "/atom", "timeout": 5}, {}, NOW)
        self.assertFalse(result.fired)
        self.assertEqual(result.state["seen"], ["urn:atom:hello"])

    def test_fetch_error_hides_url(self):
        server = self.server()
        server.httpd.routes["/SUPERSECRETTOKEN"] = (500, b"no")
        with self.assertRaises(CheckError) as caught:
            evaluate({"url": server.url + "/SUPERSECRETTOKEN", "timeout": 5}, {}, NOW)
        self.assertNotIn("SUPERSECRETTOKEN", str(caught.exception))
        self.assertIn("HTTP 500", str(caught.exception))

    def test_seen_cap_keeps_firing_for_a_new_id(self):
        server = self.server()
        options = {"url": server.url + "/rss", "timeout": 5}
        with patch("wakegate.checks.feed_new_item.MAX_SEEN", 2):
            server.httpd.routes["/rss"] = (200, _rss([("a", "A"), ("b", "B")]))
            first = evaluate(options, {}, NOW)
            self.assertFalse(first.fired)
            self.assertEqual(set(first.state["seen"]), {"a", "b"})
            server.httpd.routes["/rss"] = (200, _rss([("c", "C"), ("a", "A")]))
            second = evaluate(options, first.state, NOW)
            self.assertTrue(second.fired)
            self.assertIn("C", second.summary)
            self.assertNotIn("A", second.summary.split(":", 1)[-1])
            third = evaluate(options, second.state, NOW)
            self.assertFalse(third.fired)
