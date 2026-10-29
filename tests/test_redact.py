"""Secrets and webhook URLs are removed from text we show or store."""

import unittest

from wakegate.redact import Redactor, scrub


class RedactTest(unittest.TestCase):
    def test_replaces_registered_secrets_and_webhook_urls(self):
        redactor = Redactor()
        redactor.add("SUPERSECRETTOKEN")
        redactor.add("https://example.com/hook/SUPERSECRETTOKEN")
        text = redactor.text(
            "failed https://example.com/hook/SUPERSECRETTOKEN and token SUPERSECRETTOKEN"
        )
        self.assertNotIn("SUPERSECRETTOKEN", text)
        self.assertIn("***", text)
        self.assertEqual(redactor.text("webhook is a word"), "webhook is a word")

    def test_scrubs_webhook_urls_without_registration(self):
        raw = "see https://discord.com/api/webhooks/123/abcdefghij now"
        cleaned = scrub(raw)
        self.assertNotIn("abcdefghij", cleaned)
        self.assertNotIn("discord.com/api/webhooks", cleaned)
        slack = scrub("post https://hooks.slack.com/services/T000/B000/secretvalue")
        self.assertNotIn("secretvalue", slack)

    def test_short_values_are_not_registered(self):
        redactor = Redactor()
        redactor.add("short")
        self.assertEqual(redactor.text("short"), "short")
