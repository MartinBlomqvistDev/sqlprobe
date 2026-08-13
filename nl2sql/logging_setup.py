"""Single-line JSON logging with a per-request correlation id.

Every record carries the current request_id, propagated through a ContextVar so
that log lines emitted deep inside the agent loop can be tied back to the HTTP
request that caused them without threading an id through every signature.

Call setup_logging() once at startup, then use logging as normal:

    logger.info("query executed", extra={"operation": "sql_ok", "row_count": 12})
"""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# Optional keys a caller may pass through extra={...}. Anything not listed here
# is ignored, which keeps log lines to a predictable shape.
_EXTRA_FIELDS = (
    "operation",
    "duration_ms",
    "provider",
    "status_code",
    "row_count",
    "question_preview",
)


class JsonFormatter(logging.Formatter):
    """Render one log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise a record, including any whitelisted extra fields."""
        payload: dict[str, Any] = {
            "time": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            value = record.__dict__.get(field)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Route this package's loggers through the JSON formatter.

    Args:
        level: Log level name, for example "INFO" or "DEBUG".
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    package_logger = logging.getLogger("nl2sql")
    package_logger.setLevel(numeric_level)
    package_logger.handlers = [handler]
    package_logger.propagate = False

    root = logging.getLogger()
    root.setLevel(numeric_level)
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        root.addHandler(handler)
