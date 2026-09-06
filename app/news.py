"""Feed parsing and article normalization."""

from datetime import datetime, timedelta, timezone
import html
from html.parser import HTMLParser
import logging
import re
from typing import Any, NamedTuple
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import difflib
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
FEED_TIMEOUT_SECONDS = 10
USER_AGENT = "CodexBot/1.0"


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
        request = Request(article_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=5) as response:
            parser = _OpenGraphParser()
            parser.feed(response.read(512 * 1024).decode(response.headers.get_content_charset() or "utf-8", "replace"))
            if parser.image_url:
                return urljoin(article_url, parser.image_url)
    except Exception:
        LOGGER.debug("Unable to extract og:image from %s", article_url, exc_info=True)
    return None


def parse_feed(url: str):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
        return feedparser.parse(response.read())


def resolve_source_name(registry: dict[str, dict[str, str]], source: str) -> str | None:
    if source.lower() == "all":
        return source
    for name in registry:
        if name.lower() == source.lower():
            return name
    return None


class NewsService:
    def __init__(self, database: Database, recent_news_hours: int = 24, devops_feeds_enabled: bool = True,
                 summary_length: int = 3, summary_min_sentences: int = 2,
                 dedup_threshold: float = 0.6):
        self.database = database
        self.recent_news_hours = recent_news_hours
        self.devops_feeds_enabled = devops_feeds_enabled
        self.dedup_threshold = dedup_threshold
        self.summarizer = ExtractiveSummarizer(
            recent_news_hours=recent_news_hours,
            summary_length=summary_length,
            min_summary_sentences=summary_min_sentences,
        )

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

    def all_sources(self, include_devops: bool = True) -> list[str]:
        registries = [FEEDS, *TOPIC_FEEDS.values()]
        if include_devops and self.devops_feeds_enabled:
            registries.append(DEVOPS_FEEDS)
        return sorted({name for registry in registries for name in registry})

    def all_categories(self, include_devops: bool = True) -> list[str]:
        registries = [FEEDS, *TOPIC_FEEDS.values()]
        if include_devops and self.devops_feeds_enabled:
            registries.append(DEVOPS_FEEDS)
        return sorted({feed["category"] for registry in registries for feed in registry.values()})

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
        if source and source.lower() != "all":
            resolved_source = resolve_source_name(feed_registry, source)
            if not resolved_source or resolved_source.lower() == "all":
                LOGGER.warning("Unknown %s source requested: %s", feed_label, source)
                return []
            feeds = {resolved_source: feed_registry[resolved_source]}
        else:
            feeds = feed_registry
        if category and category.lower() != "all":
            feeds = {name: feed for name, feed in feeds.items() if feed["category"].lower() == category.lower()}
        result = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.recent_news_hours)
        for name, feed_config in feeds.items():
            try:
                feed = parse_feed(feed_config["url"])
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
                # Generate extractive summary from the article summary text
                article_summary = self.summarizer.summarize(summary, title)
                article = {
                    "url": url,
                    "source": name,
                    "title": title,
                    "summary": article_summary,  # stored summary
                    "published": item.get("published") or item.get("updated", ""),
                    "category": feed_config["category"],
                    "image_url": extract_image_url(item, url),
                    "feed_group": feed_group,
                    "devops_only": devops_only,
                }
                if self.database.claim(article, story_dedup_threshold=self.dedup_threshold):
                    result.append(article)
                    LOGGER.info("Fetched DevOps article: %s", url) if devops_only else None
                else:
                    LOGGER.info("Skipping %s article %s: duplicate or similar article", feed_label, url)
                if len(result) >= limit:
                    return result
        return result

    def mark_posted(self, article: dict[str, Any]) -> None:
        self.database.mark_posted(article["url"])

    def release(self, article: dict[str, Any]) -> None:
        self.database.release(article["url"])

    def search_articles(
        self,
        keyword: str | None = None,
        source: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return self.database.search_articles(keyword=keyword, source=source, category=category, limit=limit)

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
                    feed = parse_feed(feed_config["url"])
                    lines.append(f"✅ {name}: {len(feed.entries)}")
                except Exception:
                    LOGGER.exception("Unable to check feed %s", name)
                    lines.append(f"❌ {name}: unavailable")
        if include_devops and not self.devops_feeds_enabled:
            lines.append("DevOps: disabled")
        return lines


def ist_time() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%d-%m-%Y %I:%M:%S %p IST")


class SentenceScore(NamedTuple):
    index: int
    score: float
    sentence: str


class ExtractiveSummarizer:
    """Deterministic extractive summarizer that scores sentences and selects top N."""

    def __init__(
        self,
        recent_news_hours: int = 24,
        summary_length: int = 3,
        keyword_weight: float = 2.0,
        number_weight: float = 1.5,
        date_weight: float = 1.5,
        position_weight: float = 1.0,
        min_summary_sentences: int = 2,
    ):
        self.recent_news_hours = recent_news_hours
        self.summary_length = summary_length
        self.keyword_weight = keyword_weight
        self.number_weight = number_weight
        self.date_weight = date_weight
        self.position_weight = position_weight
        self.min_summary_sentences = min_summary_sentences
        self._ist = pytz.timezone("Asia/Kolkata")

    # ---- sentence splitting -----------------------------------------------------

    _abbreviations = {"mr", "mrs", "ms", "dr", "prof", "jr", "sr", "st", "th", "etc", "e.g", "i.e", "vs"}

    def _split_sentences(self, text: str) -> list[str]:
        if not text:
            return []
        # Protect common abbreviations by temporarily replacing Dr./Mr./etc with placeholder
        normalized = text
        for abbr in self._abbreviations:
            normalized = re.sub(rf"\b{abbr}\.", f"{abbr}_ABBR_", normalized)
        # Split on sentence-ending punctuation followed by whitespace
        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        sentences = [s.replace("_ABBR_", ".").strip() for s in sentences if s.strip()]
        return sentences

    # ---- scoring --------------------------------------------------------------

    @staticmethod
    def _score_sentence(
        sentence: str,
        title_words: set[str],
        all_keywords: set[str],
        has_number: bool,
        has_date: bool,
        position: int,
        total: int,
        keyword_weight: float = 2.0,
        number_weight: float = 1.5,
        date_weight: float = 1.5,
        position_weight: float = 1.0,
    ) -> float:
        sent_tokens = set(re.findall(r"\b\w+\b", sentence.lower()))
        score = len(sent_tokens & title_words)
        score += len(sent_tokens & all_keywords) * keyword_weight
        if has_number:
            score += number_weight
        if has_date:
            score += date_weight
        if total > 0:
            score += (1.0 - (position / total) * 0.3) * position_weight
        return score

    # ---- summary generation ----------------------------------------------------

    def summarize(self, text: str, title: str = "") -> str:
        """Return a 2-4 sentence extractive summary of *text* based on *title*."""
        if not text:
            return "No summary available."

        clean = clean_text(text)
        sentences = self._split_sentences(clean)
        if not sentences:
            return "No summary available."

        # Build score keys
        title_words = set(re.findall(r"\b\w+\b", title.lower()))
        all_keywords = set(kw.lower() for kw in KEYWORDS)

        # Pre-compute per-sentence flags
        scored: list[SentenceScore] = []
        for idx, sentence in enumerate(sentences):
            has_number = bool(re.search(r"\d", sentence))
            has_date = bool(re.search(r"\d{1,4}", sentence))
            score = self._score_sentence(
                sentence, title_words, all_keywords, has_number, has_date, idx, len(sentences),
                self.keyword_weight, self.number_weight,
                self.date_weight, self.position_weight,
            )
            scored.append(SentenceScore(index=idx, score=score, sentence=sentence))

        # Sort by score descending, then by original position for ties
        scored.sort(key=lambda s: (-s.score, s.index))

        # Select top N, respecting min/max
        target_count = min(max(self.min_summary_sentences, self.summary_length), len(scored))
        top = scored[:target_count]

        # Preserve original order among selected
        top.sort(key=lambda s: s.index)

        summary_sentences = [s.sentence for s in top]
        # If we still don't have enough, pad with first sentences
        while len(summary_sentences) < self.min_summary_sentences and len(summary_sentences) < len(sentences):
            summary_sentences.append(sentences[len(summary_sentences)])

        summary = " ".join(summary_sentences[: self.summary_length])
        return summary[:300] if summary else "No summary available."

    # ---- story deduplication helpers (moved from top-level) -------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Lowercase, strip ASCII punctuation, collapse whitespace."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def jaccard_similarity(a: str, b: str) -> float:
        set_a = set(a.split())
        set_b = set(b.split())
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union else 0.0

    @staticmethod
    def sequence_similarity(a: str, b: str) -> float:
        return float(
            difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        )
