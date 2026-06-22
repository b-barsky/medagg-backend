import json
import logging
from datetime import UTC, datetime
from typing import Any


_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
) | {
    "asctime",
    "message",
}


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)

    return value


class JsonFormatter(logging.Formatter):
    """Format application and Celery records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue

            payload[key] = _json_safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
