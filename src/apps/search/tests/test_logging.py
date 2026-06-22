import json
import logging

from django.test import SimpleTestCase

from config.logging import JsonFormatter


class JsonFormatterTests(SimpleTestCase):
    def test_formatter_preserves_structured_context(self):
        record = logging.LogRecord(
            name="apps.search.tasks",
            level=logging.INFO,
            pathname=__file__,
            lineno=12,
            msg="Provider search completed.",
            args=(),
            exc_info=None,
        )
        record.event = "search.provider.succeeded"
        record.search_run_id = "run-id"
        record.provider_run_id = 17

        payload = json.loads(
            JsonFormatter().format(record)
        )

        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(
            payload["event"],
            "search.provider.succeeded",
        )
        self.assertEqual(
            payload["search_run_id"],
            "run-id",
        )
        self.assertEqual(payload["provider_run_id"], 17)
        self.assertIn("timestamp", payload)
