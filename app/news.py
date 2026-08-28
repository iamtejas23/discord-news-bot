"""Feed parsing and article normalization."""

from datetime import datetime, timedelta, timezone
import html
from html.parser import HTMLParser
import logging
import re
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import feedparser
from dateutil import parser
import pytz

try:
    from .config import DEVOPS_FEEDS, FEEDS, KEYWORDS, TOPIC_FEEDS
    from .database import Database
except ImportError:  # Supports `python app/bot.py` from the repository root.
    from config import DEVOPS_FEEDS, FEEDS, KEYWORDS, TOPIC_FEEDS
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


def parse_published_date(item: Any) -> datetime | None:
    """Parse RSS dates as timezone-aware UTC datetimes."""
    for parsed_value in (item.get("published_parsed"), item.get("updated_parsed")):
        if parsed_value:
            try:
                return datetime(*parsed_value[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                LOGGER.warning("Invalid structured published date: %r", parsed_value)

    raw_value = item.get("published") or item.get("updated")
    if not raw_value:
        return None
    try:
        parsed_date = parser.parse(raw_value)
        if parsed_date.tzinfo is None:
            parsed_date = IST.localize(parsed_date)
        return parsed_date.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, parser.ParserError):
        return None


class _OpenGraphParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image_url = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.image_url:
            return
        attributes = {name.lower(): value for name, value in attrs}
        if attributes.get("property", "").lower() == "og:image":
            self.image_url = attributes.get("content")


def _media_url(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("url") or value.get("href")
    return getattr(value, "url", None) or getattr(value, "href", None)


def _enclosure_image_url(item: Any) -> str | None:
    enclosures = item.get("enclosures", [])
    if isinstance(enclosures, dict):
        enclosures = [enclosures]
    for enclosure in enclosures:
        image_url = _media_url(enclosure)
        media_type = enclosure.get("type", "") if isinstance(enclosure, dict) else getattr(enclosure, "type", "")
        if image_url and (media_type.startswith("image/") or not media_type):
            return image_url
    return None


def extract_image_url(item: Any, article_url: str) -> str | None:
    """Return the preferred feed image, falling back to the page's og:image."""
    for field in ("media_content", "media_thumbnail"):
        values = item.get(field, [])
        if isinstance(values, dict):
            values = [values]
        for value in values:
            image_url = _media_url(value)
            if image_url:
                return urljoin(article_url, image_url)

    enclosure_url = _enclosure_image_url(item)
    if enclosure_url:
        return urljoin(article_url, enclosure_url)

    try:
        request = Request(article_url, headers={"User-Agent": "CodexBot/1.0"})
        with urlopen(request, timeout=5) as response:
            parser = _OpenGraphParser()
            parser.feed(response.read(512 * 1024).decode(response.headers.get_content_charset() or "utf-8", "replace"))
            if parser.image_url:
                return urljoin(article_url, parser.image_url)
    except Exception:
        LOGGER.debug("Unable to extract og:image from %s", article_url, exc_info=True)
    return None


def resolve_source_name(registry: dict[str, dict[str, str]], source: str) -> str | None:
    if source == "All":
        return source
    for name in registry:
        if name.lower() == source.lower():
            return name
    return None


class NewsService:
    def __init__(self, database: Database, recent_news_hours: int = 24, devops_feeds_enabled: bool = True):
        self.database = database
        self.recent_news_hours = recent_news_hours
        self.devops_feeds_enabled = devops_feeds_enabled

    def feed_registry(self, devops_only: bool = False, feed_group: str | None = None) -> dict[str, dict[str, str]]:
        if feed_group:
            return TOPIC_FEEDS.get(feed_group, {})
        return DEVOPS_FEEDS if devops_only else FEEDS

    def sources(self, devops_only: bool = False, feed_group: str | None = None) -> list[str]:
        registry = self.feed_registry(devops_only=devops_only, feed_group=feed_group)
        return sorted(registry)

    def categories(self, devops_only: bool = False, feed_group: str | None = None) -> list[str]:
        registry = self.feed_registry(devops_only=devops_only, feed_group=feed_group)
        return sorted({feed["category"] for feed in registry.values()})

    def get_news(
        self,
        source: str | None = None,
        topic: str | None = None,
        category: str | None = None,
        limit: int = 10,
        devops_only: bool = False,
        feed_group: str | None = None,
    ) -> list[dict[str, Any]]:
        if devops_only and not self.devops_feeds_enabled:
            LOGGER.info("DevOps feeds are disabled; no DevOps articles fetched")
            return []
        feed_registry = self.feed_registry(devops_only=devops_only, feed_group=feed_group)
        feed_label = feed_group or ("DevOps" if devops_only else "news")
        if not feed_registry:
            LOGGER.warning("Unknown feed group requested: %s", feed_group)
            return []
        if source and source != "All":
            resolved_source = resolve_source_name(feed_registry, source)
            if not resolved_source or resolved_source == "All":
                LOGGER.warning("Unknown %s source requested: %s", feed_label, source)
                return []
            feeds = {resolved_source: feed_registry[resolved_source]}
        else:
            feeds = feed_registry
        if category and category != "All":
            feeds = {name: feed for name, feed in feeds.items() if feed["category"].lower() == category.lower()}
        result = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.recent_news_hours)
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
                published_date = parse_published_date(item)
                if published_date is None:
                    LOGGER.info("Skipping %s article %s: missing date", feed_label, url)
                    continue
                if published_date < cutoff:
                    LOGGER.info("Skipping %s article %s: old article (%s)", feed_label, url, published_date.isoformat())
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
                    "published": item.get("published") or item.get("updated", ""),
                    "category": feed_config["category"],
                    "image_url": extract_image_url(item, url),
                }
                if self.database.claim(article):
                    result.append(article)
                    LOGGER.info("Fetched DevOps article: %s", url) if devops_only else None
                else:
                    LOGGER.info("Skipping %s article %s: duplicate article", feed_label, url)
                if len(result) >= limit:
                    return result
        return result

    def mark_posted(self, article: dict[str, Any]) -> None:
        self.database.mark_posted(article["url"])

    def release(self, article: dict[str, Any]) -> None:
        self.database.release(article["url"])

    def feed_status(self, include_devops: bool = False, feed_groups: list[str] | None = None) -> list[str]:
        registries = [("News", FEEDS)]
        if include_devops and self.devops_feeds_enabled:
            registries.append(("DevOps", DEVOPS_FEEDS))
        for feed_group in feed_groups or []:
            registry = TOPIC_FEEDS.get(feed_group)
            if registry:
                registries.append((feed_group, registry))
        lines = ["📡 Feed Status"]
        for group, registry in registries:
            lines.append(f"{group}:")
            for name, feed_config in registry.items():
                try:
                    feed = feedparser.parse(feed_config["url"])
                    lines.append(f"✅ {name}: {len(feed.entries)}")
                except Exception:
                    LOGGER.exception("Unable to check feed %s", name)
                    lines.append(f"❌ {name}: unavailable")
        if include_devops and not self.devops_feeds_enabled:
            lines.append("DevOps: disabled")
        return lines


def ist_time() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p IST")
