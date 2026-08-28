"""Logging setup for the bot."""

from collections import deque
import logging
import os
from threading import Lock


_LOG_BUFFER: deque[str] = deque(maxlen=200)
_LOG_BUFFER_LOCK = Lock()


class RecentLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with _LOG_BUFFER_LOCK:
            _LOG_BUFFER.append(message)


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=formatter._fmt,
    )
    recent_handler = RecentLogHandler()
    recent_handler.setLevel(getattr(logging, level, logging.INFO))
    recent_handler.setFormatter(formatter)
    logging.getLogger().addHandler(recent_handler)


def recent_logs(limit: int = 10) -> list[str]:
    with _LOG_BUFFER_LOCK:
        return list(_LOG_BUFFER)[-limit:]
