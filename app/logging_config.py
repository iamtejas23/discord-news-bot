"""Logging setup for the bot."""

from collections import deque
from datetime import datetime, timezone
import logging
import os
from threading import Lock
from typing import Any


_LOG_BUFFER: deque[dict[str, Any]] = deque(maxlen=200)
_LOG_BUFFER_LOCK = Lock()


class RecentLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with _LOG_BUFFER_LOCK:
            _LOG_BUFFER.append(
                {
                    "created": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "formatted": formatted,
                }
            )


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=formatter._fmt,
    )
    root_logger = logging.getLogger()
    if any(isinstance(handler, RecentLogHandler) for handler in root_logger.handlers):
        return
    recent_handler = RecentLogHandler()
    recent_handler.setLevel(getattr(logging, level, logging.INFO))
    recent_handler.setFormatter(formatter)
    root_logger.addHandler(recent_handler)


def recent_logs(limit: int = 10) -> list[str]:
    with _LOG_BUFFER_LOCK:
        return [entry["formatted"] for entry in list(_LOG_BUFFER)[-limit:]]


def recent_log_records(limit: int = 10) -> list[dict[str, Any]]:
    with _LOG_BUFFER_LOCK:
        return list(_LOG_BUFFER)[-limit:]
