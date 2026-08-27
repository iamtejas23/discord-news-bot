"""Feed parsing and article normalization."""

from datetime import datetime, timezone
import html
import logging
import re
from typing import Any

import feedparser
from dateutil import parser
import pytz

try:
    from .config import FEEDS, KEYWORDS
    from .database import Database
except ImportError:  # Supports `python app/bot.py` from the repository root.
    from config import FEEDS, KEYWORDS
    from database import Database

LOGGER = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def format_date(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        return parser.parse(value).astimezone(IST).strftime("%d-%m-%Y %I:%M %p IST")
    except (ValueError, TypeError, OverflowError):
        return value


def priority_news(title: str) -> bool:
    return any(keyword in title.lower() for keyword in KEYWORDS)


class NewsService:
    def __init__(self, database: Database):
        self.database = database

    def get_news(self, source: str | None = None, topic: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        feeds = FEEDS if not source or source == "All" else {source: FEEDS[source]}
        result = []
        for name, feed_config in feeds.items():
            try:
                feed = feedparser.parse(feed_config["url"])
                if getattr(feed, "bozo", False):
                    LOGGER.warning("Feed %s returned a parse warning: %s", name, feed.bozo_exception)
            except Exception:
                LOGGER.exception("Unable to read feed %s", name)
                continue
            for item in feed.entries[:20]:
                url = item.get("link")
                if not url:
                    continue
                title = clean_text(item.get("title", "No title"))
                summary = clean_text(item.get("summary", ""))
                if topic and topic.lower() not in f"{title} {summary}".lower():
                    continue
                if len(summary) > 800:
                    summary = summary[:800] + "..."
                article = {
                    "url": url,
                    "source": name,
                    "title": title,
                    "summary": summary,
                    "published": item.get("published", ""),
                    "category": feed_config["category"],
                }
                if self.database.claim(article):
                    result.append(article)
                if len(result) >= limit:
                    return result
        return result

    def mark_posted(self, article: dict[str, Any]) -> None:
        self.database.mark_posted(article["url"])

    def release(self, article: dict[str, Any]) -> None:
        self.database.release(article["url"])

    def feed_status(self) -> list[str]:
        lines = ["📡 Feed Status"]
        for name, feed_config in FEEDS.items():
            try:
                feed = feedparser.parse(feed_config["url"])
                lines.append(f"✅ {name}: {len(feed.entries)}")
            except Exception:
                LOGGER.exception("Unable to check feed %s", name)
                lines.append(f"❌ {name}: unavailable")
        return lines


def ist_time() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p IST")