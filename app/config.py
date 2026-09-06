"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()

FEEDS = {
    "BBC": {"url": "https://feeds.bbci.co.uk/news/rss.xml", "category": "World"},
    "Times of India": {"url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "category": "India"},
    "AWS": {"url": "https://aws.amazon.com/blogs/aws/feed/", "category": "Cloud"},
    "Hacker News": {"url": "https://hnrss.org/frontpage", "category": "Technology"},
}

DEVOPS_FEEDS = {
    "Kubernetes": {"url": "https://kubernetes.io/feed.xml", "category": "DevOps"},
    "AWS Cloud": {"url": "https://aws.amazon.com/blogs/aws/feed/", "category": "Cloud"},
    "Azure": {"url": "https://azure.microsoft.com/en-us/blog/feed/", "category": "Cloud"},
    "GCP": {"url": "https://cloud.google.com/feeds/blog.xml", "category": "Cloud"},
    "Terraform": {"url": "https://www.hashicorp.com/blog/products/terraform/feed.xml", "category": "DevOps"},
    "Docker": {"url": "https://www.docker.com/blog/feed/", "category": "DevOps"},
    "Linux": {"url": "https://www.linuxfoundation.org/blog/rss.xml", "category": "DevOps"},
    "CI/CD": {"url": "https://github.blog/tag/ci-cd/feed/", "category": "DevOps"},
    "DevSecOps": {"url": "https://owasp.org/feed.xml", "category": "DevSecOps"},
    "Cloud Security": {"url": "https://aws.amazon.com/blogs/security/feed/", "category": "Cloud Security"},
}

CRICKET_FEEDS = {
    "ESPN Cricinfo": {
        "url": "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        "category": "Cricket",
    },
}

GEO_POLITICS_FEEDS = {
    "The Diplomat": {"url": "https://thediplomat.com/feed/", "category": "Geo-Politics"},
    "Google News Geopolitics": {
        "url": "https://news.google.com/rss/search?q=geopolitics%20OR%20%22foreign%20policy%22&hl=en-US&gl=US&ceid=US:en",
        "category": "Geo-Politics",
    },
}

SPACE_FEEDS = {
    "NASA": {"url": "https://www.nasa.gov/feed/", "category": "Space"},
    "Space.com": {"url": "https://www.space.com/feeds/all", "category": "Space"},
    "Astronomy Magazine": {"url": "https://www.astronomy.com/feed/", "category": "Astronomy"},
}

TOPIC_FEEDS = {
    "Cricket": CRICKET_FEEDS,
    "Geo-Politics": GEO_POLITICS_FEEDS,
    "Space": SPACE_FEEDS,
}

KEYWORDS = ("aws", "eks", "kubernetes", "terraform", "security", "vulnerability", "hack", "cloud")
BREAKING_KEYWORDS = ("breaking", "urgent", "alert", "emergency", "outage", "attack", "earthquake", "war")


@dataclass(frozen=True)
class Config:
    token: str
    channel_id: int
    database_path: str = "/data/news.db"
    news_interval_minutes: int = 30
    recent_news_hours: int = 24
    publish_batch_size: int = 5
    command_default_limit: int = 10
    devops_feeds_enabled: bool = True
    summary_length: int = 3
    summary_min_sentences: int = 2
    dedup_threshold: float = 0.6
    dedup_lookback_hours: int = 168
    digest_enabled: bool = False
    digest_hour_utc: int = 8
    breaking_alerts_enabled: bool = True
    breaking_interval_minutes: int = 5
    breaking_keywords: tuple[str, ...] = BREAKING_KEYWORDS
    article_retention_days: int = 7
    preference_retention_days: int = 90

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")
        if not token or not channel_id:
            raise RuntimeError("DISCORD_TOKEN and DISCORD_CHANNEL_ID are required")
        try:
            parsed_channel_id = int(channel_id)
            interval = int(os.getenv("NEWS_INTERVAL_MINUTES", "30"))
            recent_news_hours = int(os.getenv("NEWS_RECENT_HOURS", "24"))
            publish_batch_size = int(os.getenv("NEWS_PUBLISH_BATCH_SIZE", "5"))
            command_default_limit = int(os.getenv("NEWS_COMMAND_DEFAULT_LIMIT", "10"))
            summary_length = int(os.getenv("NEWS_SUMMARY_LENGTH", "3"))
            summary_min_sentences = int(os.getenv("NEWS_SUMMARY_MIN_SENTENCES", "2"))
            dedup_threshold = float(os.getenv("NEWS_DEDUP_THRESHOLD", "0.6"))
            dedup_lookback_hours = int(os.getenv("NEWS_DEDUP_LOOKBACK_HOURS", "168"))
            digest_hour_utc = int(os.getenv("NEWS_DIGEST_HOUR_UTC", "8"))
            breaking_interval_minutes = int(os.getenv("NEWS_BREAKING_INTERVAL_MINUTES", "5"))
            article_retention_days = int(os.getenv("NEWS_ARTICLE_RETENTION_DAYS", "7"))
            preference_retention_days = int(os.getenv("NEWS_PREFERENCE_RETENTION_DAYS", "90"))
        except ValueError as exc:
            raise RuntimeError(
                "DISCORD_CHANNEL_ID, NEWS_INTERVAL_MINUTES, NEWS_RECENT_HOURS, "
                "NEWS_PUBLISH_BATCH_SIZE, NEWS_COMMAND_DEFAULT_LIMIT, NEWS_DIGEST_HOUR_UTC, "
                "NEWS_BREAKING_INTERVAL_MINUTES, NEWS_ARTICLE_RETENTION_DAYS, and "
                "NEWS_PREFERENCE_RETENTION_DAYS must be integers"
            ) from exc
        if interval < 1 or recent_news_hours < 1 or publish_batch_size < 1 or command_default_limit < 1:
            raise RuntimeError(
                "NEWS_INTERVAL_MINUTES, NEWS_RECENT_HOURS, NEWS_PUBLISH_BATCH_SIZE, "
                "and NEWS_COMMAND_DEFAULT_LIMIT must be at least 1"
            )
        if summary_length < 1:
            summary_length = 1
        if summary_min_sentences < 1 or summary_min_sentences > summary_length:
            summary_min_sentences = 2
        if not (0.0 <= dedup_threshold <= 1.0):
            dedup_threshold = 0.6
        if dedup_lookback_hours < 1:
            dedup_lookback_hours = 168
        if not 0 <= digest_hour_utc <= 23:
            digest_hour_utc = 8
        if breaking_interval_minutes < 1:
            breaking_interval_minutes = 5
        if article_retention_days < 1:
            article_retention_days = 7
        if preference_retention_days < 1:
            preference_retention_days = 90
        breaking_keywords = tuple(
            keyword.strip().lower()
            for keyword in os.getenv("NEWS_BREAKING_KEYWORDS", ",".join(BREAKING_KEYWORDS)).split(",")
            if keyword.strip()
        ) or BREAKING_KEYWORDS
        return cls(
            token=token,
            channel_id=parsed_channel_id,
            database_path=os.getenv("NEWS_DATABASE_PATH", "/data/news.db"),
            news_interval_minutes=interval,
            recent_news_hours=recent_news_hours,
            publish_batch_size=publish_batch_size,
            command_default_limit=command_default_limit,
            devops_feeds_enabled=os.getenv("DEVOPS_FEEDS_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
            summary_length=summary_length,
            summary_min_sentences=summary_min_sentences,
            dedup_threshold=dedup_threshold,
            dedup_lookback_hours=dedup_lookback_hours,
            digest_enabled=os.getenv("NEWS_DIGEST_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            digest_hour_utc=digest_hour_utc,
            breaking_alerts_enabled=os.getenv("NEWS_BREAKING_ALERTS_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
            breaking_interval_minutes=breaking_interval_minutes,
            breaking_keywords=breaking_keywords,
            article_retention_days=article_retention_days,
            preference_retention_days=preference_retention_days,
        )
