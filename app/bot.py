"""Discord entrypoint, kept at app/bot.py for deployment compatibility."""

import asyncio
from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

try:
    from .config import Config, FEEDS
    from .database import Database
    from .logging_config import configure_logging
    from .news import NewsService, format_date, ist_time, priority_news
    from .scheduler import NewsScheduler
except ImportError:  # Supports `python app/bot.py` from the repository root.
    from config import Config, FEEDS
    from database import Database
    from logging_config import configure_logging
    from news import NewsService, format_date, ist_time, priority_news
    from scheduler import NewsScheduler


configure_logging()
LOGGER = logging.getLogger(__name__)
config = Config.from_env()
database = Database(config.database_path)
database.initialize()
news_service = NewsService(database)

TOKEN = config.token
CHANNEL_ID = config.channel_id
DB = config.database_path
NEWS_INTERVAL = config.news_interval_minutes


def init_db() -> None:
    database.initialize()


def get_news(source=None, topic=None, limit=10):
    return news_service.get_news(source=source, topic=topic, limit=limit)

intents = discord.Intents.none()
intents.guilds = True
bot = commands.Bot(command_prefix=None, intents=intents, help_command=None)


async def send_article(channel, article: dict) -> None:
    title = f"🚨 {article['title']}" if priority_news(article["title"]) else article["title"]
    embed = discord.Embed(
        title=title,
        url=article["url"],
        description=article["summary"],
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📰 Source", value=article["source"])
    embed.add_field(name="📂 Category", value=article["category"])
    embed.add_field(name="📅 Published", value=format_date(article["published"]))
    if article.get("image_url"):
        embed.set_image(url=article["image_url"])
    embed.set_footer(text="CodexBot News")
    await channel.send(embed=embed)


async def publish_articles(channel, limit: int) -> int:
    articles = await asyncio.to_thread(news_service.get_news, limit=limit)
    published = 0
    for article in articles:
        try:
            await send_article(channel, article)
        except Exception:
            news_service.release(article)
            LOGGER.exception("Unable to publish article %s", article["url"])
        else:
            news_service.mark_posted(article)
            published += 1
    return published


async def publish_news() -> None:
    channel = bot.get_channel(config.channel_id)
    if channel is None:
        LOGGER.error("Configured channel %s is not available", config.channel_id)
        return
    await publish_articles(channel, limit=5)


scheduler = NewsScheduler(publish_news, config.news_interval_minutes)
news_loop = scheduler.loop


@bot.tree.command(name="news", description="Latest news")
@app_commands.describe(topic="Only show articles matching this topic")
async def news(interaction: discord.Interaction, topic: str | None = None):
    await interaction.response.defer()
    try:
        articles = await asyncio.to_thread(news_service.get_news, topic=topic, limit=10)
    except Exception:
        LOGGER.exception("Unable to fetch news for slash command")
        await interaction.followup.send("Unable to fetch news right now.")
        return
    if not articles:
        await interaction.followup.send("No new news found")
        return
    published = 0
    for article in articles:
        try:
            await send_article(interaction.channel, article)
        except Exception:
            news_service.release(article)
            LOGGER.exception("Unable to publish article %s", article["url"])
        else:
            news_service.mark_posted(article)
            published += 1
    await interaction.followup.send(f"Published {published} article(s).", ephemeral=True)


@bot.tree.command(name="sources", description="News sources")
async def sources(interaction: discord.Interaction):
    await interaction.response.send_message(
        "\n".join(f"📰 {name} - {data['category']}" for name, data in FEEDS.items())
    )


@bot.tree.command(name="health", description="Bot health")
async def health(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🤖 CodexBot\n\nStatus: ONLINE\nServers: {len(bot.guilds)}\nTime: {ist_time()}"
    )


@bot.tree.command(name="status", description="Feed status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        lines = await asyncio.to_thread(news_service.feed_status)
        counts = database.counts()
        lines.append(f"Stored: {counts.get('posted', 0)} | Pending: {counts.get('pending', 0)}")
    except Exception:
        LOGGER.exception("Unable to collect bot status")
        await interaction.followup.send("Unable to collect status right now.")
        return
    await interaction.followup.send("\n".join(lines))


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception:
        LOGGER.exception("Unable to synchronize slash commands")
    try:
        scheduler.start()
    except Exception:
        LOGGER.exception("Unable to start news scheduler")
    LOGGER.info("CodexBot started as %s at %s", bot.user, ist_time())


if __name__ == "__main__":
    bot.run(config.token)