"""Discord entrypoint, kept at app/bot.py for deployment compatibility."""

import asyncio
from datetime import datetime, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

try:
    from .config import Config, DEVOPS_FEEDS, FEEDS, TOPIC_FEEDS
    from .database import Database
    from .logging_config import configure_logging
    from .news import NewsService, format_date, ist_time, priority_news
    from .scheduler import NewsScheduler
except ImportError:  # Supports `python app/bot.py` from the repository root.
    from config import Config, DEVOPS_FEEDS, FEEDS, TOPIC_FEEDS
    from database import Database
    from logging_config import configure_logging
    from news import NewsService, format_date, ist_time, priority_news
    from scheduler import NewsScheduler


configure_logging()
LOGGER = logging.getLogger(__name__)
config = Config.from_env()
database = Database(config.database_path)
database.initialize()
news_service = NewsService(
    database,
    recent_news_hours=config.recent_news_hours,
    devops_feeds_enabled=config.devops_feeds_enabled,
)

TOKEN = config.token
CHANNEL_ID = config.channel_id
DB = config.database_path
NEWS_INTERVAL = config.news_interval_minutes
RECENT_NEWS_HOURS = config.recent_news_hours
PUBLISH_BATCH_SIZE = config.publish_batch_size
COMMAND_DEFAULT_LIMIT = config.command_default_limit
DEVOPS_FEEDS_ENABLED = config.devops_feeds_enabled


def init_db() -> None:
    database.initialize()


def get_news(source=None, topic=None, category=None, limit=10, feed_group=None):
    return news_service.get_news(source=source, topic=topic, category=category, limit=limit, feed_group=feed_group)

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
    await publish_articles(channel, limit=config.publish_batch_size)


scheduler = NewsScheduler(publish_news, config.news_interval_minutes)
news_loop = scheduler.loop


def requested_limit(limit: int | None) -> int:
    return min(max(limit or config.command_default_limit, 1), 25)


def matching_choices(values: list[str], current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    matches = [value for value in values if current in value.lower()]
    return [app_commands.Choice(name=value, value=value) for value in matches[:25]]


async def publish_command_articles(
    interaction: discord.Interaction,
    *,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
    devops_only: bool = False,
    feed_group: str | None = None,
) -> None:
    if interaction.channel is None:
        await interaction.followup.send("This command needs to be used in a Discord channel.")
        return
    try:
        articles = await asyncio.to_thread(
            news_service.get_news,
            source=source,
            topic=topic,
            category=category,
            limit=requested_limit(limit),
            devops_only=devops_only,
            feed_group=feed_group,
        )
    except Exception:
        LOGGER.exception("Unable to fetch %snews for slash command", "DevOps " if devops_only else "")
        await interaction.followup.send("Unable to fetch news right now.")
        return
    if not articles:
        await interaction.followup.send("No matching new articles found.")
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


@bot.tree.command(name="news", description="Latest news")
@app_commands.describe(
    topic="Only show articles matching this topic",
    source="Only fetch from this source name",
    category="Only fetch from this category",
    limit="Number of articles to publish, from 1 to 25",
)
async def news(
    interaction: discord.Interaction,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer()
    await publish_command_articles(
        interaction,
        topic=topic,
        source=source,
        category=category,
        limit=limit,
    )


@news.autocomplete("source")
async def news_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.sources()], current)


@news.autocomplete("category")
async def news_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.categories()], current)


@bot.tree.command(name="sources", description="News sources")
@app_commands.describe(include_devops="Include DevOps and Cloud feeds")
async def sources(interaction: discord.Interaction, include_devops: bool = False):
    lines = ["📰 News sources"]
    lines.extend(f"{name} - {data['category']}" for name, data in FEEDS.items())
    if include_devops:
        lines.append("")
        lines.append("DevOps and Cloud sources")
        lines.extend(f"{name} - {data['category']}" for name, data in DEVOPS_FEEDS.items())
    for group, feeds in TOPIC_FEEDS.items():
        lines.append("")
        lines.append(f"{group} sources")
        lines.extend(f"{name} - {data['category']}" for name, data in feeds.items())
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="devops", description="Latest DevOps and Cloud news")
@app_commands.describe(
    topic="Only show articles matching this topic",
    source="Only fetch from this DevOps source name",
    category="Only fetch from this DevOps category",
    limit="Number of articles to publish, from 1 to 25",
)
async def devops(
    interaction: discord.Interaction,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer()
    if not config.devops_feeds_enabled:
        await interaction.followup.send("DevOps news feeds are currently disabled.")
        return
    await publish_command_articles(
        interaction,
        topic=topic,
        source=source,
        category=category,
        limit=limit,
        devops_only=True,
    )


@devops.autocomplete("source")
async def devops_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.sources(devops_only=True)], current)


@devops.autocomplete("category")
async def devops_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.categories(devops_only=True)], current)


@bot.tree.command(name="cricket", description="Latest cricket news")
@app_commands.describe(
    topic="Only show cricket articles matching this topic",
    source="Only fetch from this cricket source name",
    category="Only fetch from this cricket category",
    limit="Number of articles to publish, from 1 to 25",
)
async def cricket(
    interaction: discord.Interaction,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer()
    await publish_command_articles(
        interaction,
        topic=topic,
        source=source,
        category=category,
        limit=limit,
        feed_group="Cricket",
    )


@cricket.autocomplete("source")
async def cricket_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.sources(feed_group="Cricket")], current)


@cricket.autocomplete("category")
async def cricket_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.categories(feed_group="Cricket")], current)


@bot.tree.command(name="geo-politics", description="Latest geo-politics news")
@app_commands.describe(
    topic="Only show geo-politics articles matching this topic",
    source="Only fetch from this geo-politics source name",
    category="Only fetch from this geo-politics category",
    limit="Number of articles to publish, from 1 to 25",
)
async def geo_politics(
    interaction: discord.Interaction,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer()
    await publish_command_articles(
        interaction,
        topic=topic,
        source=source,
        category=category,
        limit=limit,
        feed_group="Geo-Politics",
    )


@geo_politics.autocomplete("source")
async def geo_politics_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.sources(feed_group="Geo-Politics")], current)


@geo_politics.autocomplete("category")
async def geo_politics_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.categories(feed_group="Geo-Politics")], current)


@bot.tree.command(name="space", description="Latest space and astronomy news")
@app_commands.describe(
    topic="Only show space articles matching this topic",
    source="Only fetch from this space source name",
    category="Only fetch from this space category",
    limit="Number of articles to publish, from 1 to 25",
)
async def space(
    interaction: discord.Interaction,
    topic: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer()
    await publish_command_articles(
        interaction,
        topic=topic,
        source=source,
        category=category,
        limit=limit,
        feed_group="Space",
    )


@space.autocomplete("source")
async def space_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.sources(feed_group="Space")], current)


@space.autocomplete("category")
async def space_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.categories(feed_group="Space")], current)


@bot.tree.command(name="health", description="Bot health")
async def health(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🤖 CodexBot\n\nStatus: ONLINE\nServers: {len(bot.guilds)}\nTime: {ist_time()}"
    )


@bot.tree.command(name="status", description="Feed status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        lines = [
            "🤖 CodexBot Status",
            f"Scheduler: {'running' if scheduler.is_running() else 'stopped'}",
            f"Interval: {config.news_interval_minutes} minute(s)",
            f"Scheduled batch: {config.publish_batch_size} article(s)",
            f"Recent window: {config.recent_news_hours} hour(s)",
            f"DevOps feeds: {'enabled' if config.devops_feeds_enabled else 'disabled'}",
            "",
        ]
        lines.extend(await asyncio.to_thread(news_service.feed_status, True, list(TOPIC_FEEDS)))
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
