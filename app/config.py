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

KEYWORDS = ("aws", "eks", "kubernetes", "terraform", "security", "vulnerability", "hack", "cloud")


@dataclass(frozen=True)
class Config:
    token: str
    channel_id: int
    database_path: str = "/data/news.db"
    news_interval_minutes: int = 30

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")
        if not token or not channel_id:
            raise RuntimeError("DISCORD_TOKEN and DISCORD_CHANNEL_ID are required")
        try:
            parsed_channel_id = int(channel_id)
            interval = int(os.getenv("NEWS_INTERVAL_MINUTES", "30"))
        except ValueError as exc:
            raise RuntimeError("DISCORD_CHANNEL_ID and NEWS_INTERVAL_MINUTES must be integers") from exc
        if interval < 1:
            raise RuntimeError("NEWS_INTERVAL_MINUTES must be at least 1")
        return cls(
            token=token,
            channel_id=parsed_channel_id,
            database_path=os.getenv("NEWS_DATABASE_PATH", "/data/news.db"),
            news_interval_minutes=interval,
        )