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
        except ValueError as exc:
            raise RuntimeError(
                "DISCORD_CHANNEL_ID, NEWS_INTERVAL_MINUTES, NEWS_RECENT_HOURS, "
                "NEWS_PUBLISH_BATCH_SIZE, and NEWS_COMMAND_DEFAULT_LIMIT must be integers"
            ) from exc
        if interval < 1 or recent_news_hours < 1 or publish_batch_size < 1 or command_default_limit < 1:
            raise RuntimeError(
                "NEWS_INTERVAL_MINUTES, NEWS_RECENT_HOURS, NEWS_PUBLISH_BATCH_SIZE, "
                "and NEWS_COMMAND_DEFAULT_LIMIT must be at least 1"
            )
        return cls(
            token=token,
            channel_id=parsed_channel_id,
            database_path=os.getenv("NEWS_DATABASE_PATH", "/data/news.db"),
            news_interval_minutes=interval,
            recent_news_hours=recent_news_hours,
            publish_batch_size=publish_batch_size,
            command_default_limit=command_default_limit,
            devops_feeds_enabled=os.getenv("DEVOPS_FEEDS_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        )
