"""Discord entrypoint, kept at app/bot.py for deployment compatibility."""

import asyncio
from datetime import datetime, timezone
import logging
import textwrap

import discord
from discord import app_commands
from discord.ext import commands

try:
    from .config import Config, DEVOPS_FEEDS, FEEDS, TOPIC_FEEDS
    from .database import Database
    from .logging_config import configure_logging, recent_log_records
    from .news import NewsService, format_date, ist_time, priority_news
    from .scheduler import DailyDigestScheduler, NewsScheduler
except ImportError:  # Supports `python app/bot.py` from the repository root.
    from config import Config, DEVOPS_FEEDS, FEEDS, TOPIC_FEEDS
    from database import Database
    from logging_config import configure_logging, recent_log_records
    from news import NewsService, format_date, ist_time, priority_news
    from scheduler import DailyDigestScheduler, NewsScheduler


configure_logging()
LOGGER = logging.getLogger(__name__)
config = Config.from_env()
database = Database(config.database_path)
database.initialize()
news_service = NewsService(
    database,
    recent_news_hours=config.recent_news_hours,
    devops_feeds_enabled=config.devops_feeds_enabled,
    summary_length=config.summary_length,
    summary_min_sentences=config.summary_min_sentences,
    dedup_threshold=config.dedup_threshold,
    breaking_keywords=config.breaking_keywords,
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
intents.reactions = True
bot = commands.Bot(command_prefix=None, intents=intents, help_command=None)


async def send_article(channel, article: dict) -> discord.Message:
    title = f"🚨 {article['title']}" if priority_news(article["title"]) else article["title"]
    summary = article.get("summary") or "No summary available."
    # If the summary is short (extractive), use it; otherwise keep original length check
    embed = discord.Embed(
        title=title,
        url=article["url"],
        description=summary,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📰 Source", value=article["source"])
    embed.add_field(name="📂 Category", value=article["category"])
    embed.add_field(name="📅 Published", value=format_date(article["published"]))
    if article.get("image_url"):
        embed.set_image(url=article["image_url"])
    embed.set_footer(text="CodexBot News")
    view = ArticleActionsView(article)
    message = await channel.send(embed=embed, view=view)
    database.record_message(article["url"], message.id)
    return message


async def publish_articles(channel, limit: int) -> int:
    articles = await asyncio.to_thread(
        news_service.get_news,
        limit=limit,
        exclude_priority=config.breaking_alerts_enabled,
    )
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


async def publish_breaking_news() -> None:
    channel = bot.get_channel(config.channel_id)
    if channel is None:
        LOGGER.error("Configured channel %s is not available for breaking alerts", config.channel_id)
        return
    articles = await asyncio.to_thread(news_service.get_news, limit=config.publish_batch_size, priority_only=True)
    for article in articles:
        try:
            await send_article(channel, article)
        except Exception:
            news_service.release(article)
            LOGGER.exception("Unable to publish breaking article %s", article["url"])
        else:
            news_service.mark_posted(article)


async def publish_daily_digest() -> None:
    channel = bot.get_channel(config.channel_id)
    if channel is None:
        LOGGER.error("Configured channel %s is not available for daily digest", config.channel_id)
        return
    articles = await asyncio.to_thread(
        news_service.get_news,
        limit=config.publish_batch_size,
        exclude_priority=config.breaking_alerts_enabled,
    )
    if not articles:
        LOGGER.info("No new articles available for daily digest")
        return
    embed = discord.Embed(
        title="Daily News Digest",
        description="Top stories from the latest feeds",
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    for article in articles:
        title = compact_article_text(article.get("title"), width=180)
        summary = compact_article_text(article.get("summary"), width=500)
        embed.add_field(
            name=f"{article['source']} | {article['category']}",
            value=f"**[{title}]({article['url']})**\n{summary}",
            inline=False,
        )
    embed.set_footer(text="React with 👍 or 👎 to rate this digest")
    try:
        message = await channel.send(embed=embed)
    except Exception:
        for article in articles:
            news_service.release(article)
        raise
    for article in articles:
        database.record_message(article["url"], message.id)
        news_service.mark_posted(article)


async def publish_news() -> None:
    channel = bot.get_channel(config.channel_id)
    if channel is None:
        LOGGER.error("Configured channel %s is not available", config.channel_id)
        return
    if config.digest_enabled:
        await publish_daily_digest()
    else:
        await publish_articles(channel, limit=config.publish_batch_size)


scheduler = NewsScheduler(publish_news, config.news_interval_minutes)
news_loop = scheduler.loop
breaking_scheduler = NewsScheduler(publish_breaking_news, config.breaking_interval_minutes)
digest_scheduler = DailyDigestScheduler(publish_daily_digest, config.digest_hour_utc)


def requested_limit(limit: int | None) -> int:
    return min(max(limit or config.command_default_limit, 1), 25)


def requested_search_limit(limit: int | None) -> int:
    return min(max(limit or 10, 1), 10)


def matching_choices(values: list[str], current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    matches = [value for value in values if current in value.lower()]
    return [app_commands.Choice(name=value, value=value) for value in matches[:25]]


def log_level_style(level: str) -> tuple[str, int]:
    if level == "ERROR" or level == "CRITICAL":
        return "Alert", 0xE74C3C
    if level == "WARNING":
        return "Warning", 0xF1C40F
    if level == "DEBUG":
        return "Debug", 0x95A5A6
    return "Info", 0x2ECC71


def compact_log_message(message: str, width: int = 220) -> str:
    message = " ".join(message.split())
    if not message:
        return "_No message_"
    return textwrap.shorten(message, width=width, placeholder="...")


def compact_article_text(value: str | None, width: int = 260) -> str:
    value = " ".join((value or "").split())
    if not value:
        return "No summary available."
    return textwrap.shorten(value, width=width, placeholder="...")


def can_view_operational_data(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    permissions = interaction.user.guild_permissions
    return permissions.administrator or permissions.manage_guild


def article_context(article: dict) -> tuple[bool, str | None]:
    if article.get("feed_group") or article.get("devops_only"):
        return bool(article.get("devops_only")), article.get("feed_group")
    source = article.get("source")
    category = article.get("category")
    if source in FEEDS:
        return False, None
    for group, registry in TOPIC_FEEDS.items():
        if source in registry or any(feed["category"] == category for feed in registry.values()):
            return False, group
    if source in DEVOPS_FEEDS or any(feed["category"] == category for feed in DEVOPS_FEEDS.values()):
        return True, None
    return False, None


def search_result_embeds(articles: list[dict], keyword: str | None, source: str | None, category: str | None) -> list[discord.Embed]:
    filters = []
    if keyword:
        filters.append(f"keyword: {keyword}")
    if source and source != "All":
        filters.append(f"source: {source}")
    if category and category != "All":
        filters.append(f"category: {category}")
    footer = "Stored article match"
    if filters:
        footer += f" | {', '.join(filters)}"
    embeds = []
    for index, article in enumerate(articles, start=1):
        title = compact_article_text(article.get("title"), width=120)
        summary = compact_article_text(article.get("summary"), width=220)
        published = format_date(article.get("published"))
        embed = discord.Embed(
            title=f"{index}. {title}",
            url=article["url"],
            description=summary,
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Source", value=article["source"], inline=True)
        embed.add_field(name="Category", value=article["category"], inline=True)
        embed.add_field(name="Published", value=published, inline=True)
        if article.get("image_url"):
            embed.set_thumbnail(url=article["image_url"])
        embed.set_footer(text=footer)
        embeds.append(embed)
    return embeds


async def publish_filtered_articles(
    channel,
    *,
    limit: int,
    source: str | None = None,
    topic: str | None = None,
    category: str | None = None,
    devops_only: bool = False,
    feed_group: str | None = None,
) -> int:
    articles = await asyncio.to_thread(
        news_service.get_news,
        source=source,
        topic=topic,
        category=category,
        limit=limit,
        devops_only=devops_only,
        feed_group=feed_group,
    )
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


class ArticleActionsView(discord.ui.View):
    def __init__(self, article: dict):
        super().__init__(timeout=900)
        self.article = article
        self.add_item(discord.ui.Button(label="Read Article", style=discord.ButtonStyle.link, url=article["url"]))
        more_button = discord.ui.Button(label="More from Source", style=discord.ButtonStyle.secondary)
        more_button.callback = self.more_from_source
        self.add_item(more_button)
        similar_button = discord.ui.Button(label="Similar Topic", style=discord.ButtonStyle.primary)
        similar_button.callback = self.similar_topic
        self.add_item(similar_button)
        if article.get("story_id"):
            story_button = discord.ui.Button(label="View Story", style=discord.ButtonStyle.success)
            story_button.callback = self.view_story
            self.add_item(story_button)

    async def view_story(self, interaction: discord.Interaction):
        if interaction.channel is None:
            await interaction.response.send_message("This button needs a Discord channel.", ephemeral=True)
            return
        story_id = self.article.get("story_id")
        rows = database._connect().execute(
            "SELECT source, title, summary FROM articles WHERE story_id=? AND status='posted' ORDER BY COALESCE(posted_at, claimed_at, created) DESC",
            (story_id,),
        ).fetchall()
        sources = list({row["source"] for row in rows})
        canonical_title = rows[0]["title"] if rows else "Untitled story"
        combined_summary = " ".join([row["summary"] or "" for row in rows])
        article_count = len(rows)
        await interaction.response.send_message(
            f"📖 **Story: {canonical_title}**\n"
            f"> {combined_summary[:300]}...\n"
            f"> Sources: {', '.join(sources) if sources else 'Unknown'}\n"
            f"> Articles in story: {article_count}",
            ephemeral=True,
        )

    async def _get_related_story_articles(self, story_id: str) -> list[dict]:
        rows = database._connect().execute(
            "SELECT url, source, title, summary, published, category FROM articles WHERE story_id=? AND status='posted' ORDER BY COALESCE(posted_at, claimed_at, created) DESC",
            (story_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    async def more_from_source(self, interaction: discord.Interaction):
        if interaction.channel is None:
            await interaction.response.send_message("This button needs a Discord channel.", ephemeral=True)
            return
        devops_only, feed_group = article_context(self.article)
        await interaction.response.defer(ephemeral=True, thinking=True)
        published = await publish_filtered_articles(
            interaction.channel,
            limit=3,
            source=self.article.get("source"),
            devops_only=devops_only,
            feed_group=feed_group,
        )
        await interaction.followup.send(
            f"Published {published} more article(s) from {self.article.get('source')}.",
            ephemeral=True,
        )

    async def similar_topic(self, interaction: discord.Interaction):
        if interaction.channel is None:
            await interaction.response.send_message("This button needs a Discord channel.", ephemeral=True)
            return
        devops_only, feed_group = article_context(self.article)
        await interaction.response.defer(ephemeral=True, thinking=True)
        published = await publish_filtered_articles(
            interaction.channel,
            limit=3,
            category=self.article.get("category"),
            devops_only=devops_only,
            feed_group=feed_group,
        )
        await interaction.followup.send(
            f"Published {published} similar article(s) in {self.article.get('category')}.",
            ephemeral=True,
        )


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
        published = await publish_filtered_articles(
            interaction.channel,
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
    if not published:
        await interaction.followup.send("No matching new articles found.")
        return
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


@bot.tree.command(name="search-news", description="Search stored news articles")
@app_commands.describe(
    keyword="Search text in stored article titles and summaries",
    source="Only search this source",
    category="Only search this category",
    limit="Number of stored articles to show, from 1 to 10",
)
async def search_news(
    interaction: discord.Interaction,
    keyword: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int | None = None,
):
    await interaction.response.defer(ephemeral=True)
    try:
        articles = await asyncio.to_thread(
            news_service.search_articles,
            keyword=keyword,
            source=source,
            category=category,
            limit=requested_search_limit(limit),
        )
    except Exception:
        LOGGER.exception("Unable to search stored articles")
        await interaction.followup.send("Unable to search stored articles right now.", ephemeral=True)
        return
    if not articles:
        await interaction.followup.send("No stored articles matched your search.", ephemeral=True)
        return
    await interaction.followup.send(
        embeds=search_result_embeds(articles, keyword, source, category),
        ephemeral=True,
    )


@search_news.autocomplete("source")
async def search_news_source_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.all_sources()], current)


@search_news.autocomplete("category")
async def search_news_category_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return matching_choices(["All", *news_service.all_categories()], current)


@bot.tree.command(name="health", description="Bot health")
async def health(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🤖 CodexBot\n\nStatus: ONLINE\nServers: {len(bot.guilds)}\nTime: {ist_time()}"
    )


@bot.tree.command(name="rankings", description="Show news preferences from reactions")
async def rankings(interaction: discord.Interaction):
    try:
        data = await asyncio.to_thread(database.reaction_rankings, 10)
    except Exception:
        LOGGER.exception("Unable to load reaction rankings")
        await interaction.response.send_message("Unable to load rankings right now.", ephemeral=True)
        return
    source_lines = [
        f"{row['source']}: {row['emoji']} {row['votes']}"
        for row in data["sources"]
    ]
    category_lines = [
        f"{row['category']}: {row['emoji']} {row['votes']}"
        for row in data["categories"]
    ]
    if not source_lines and not category_lines:
        await interaction.response.send_message("No reactions have been recorded yet.", ephemeral=True)
        return
    embed = discord.Embed(title="News Preferences", color=0x2ECC71)
    embed.add_field(name="Sources", value="\n".join(source_lines) or "No data", inline=False)
    embed.add_field(name="Topics", value="\n".join(category_lines) or "No data", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="logs", description="Show the last 10 container application logs")
@app_commands.default_permissions(manage_guild=True)
async def logs(interaction: discord.Interaction):
    if not can_view_operational_data(interaction):
        await interaction.response.send_message("You need Manage Server permission to view logs.", ephemeral=True)
        return
    records = recent_log_records(10)
    if not records:
        await interaction.response.send_message("No logs captured yet.", ephemeral=True)
        return
    highest_level = max(logging.getLevelName(record["level"]) for record in records)
    status, color = log_level_style(logging.getLevelName(highest_level))
    embed = discord.Embed(
        title="Container Logs",
        description=f"Last {len(records)} application log lines",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Limit", value="10 lines", inline=True)
    embed.add_field(name="Visibility", value="Only you", inline=True)
    for index, record in enumerate(records, start=1):
        created = datetime.fromisoformat(record["created"]).astimezone(timezone.utc).strftime("%H:%M:%S UTC")
        level_name = record["level"]
        logger_name = record["logger"].rsplit(".", maxsplit=1)[-1]
        name = f"{index}. {level_name} | {logger_name} | {created}"
        embed.add_field(name=name, value=compact_log_message(record["message"]), inline=False)
    embed.set_footer(text="CodexBot application log tail")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        if config.digest_enabled:
            digest_scheduler.start()
        else:
            scheduler.start()
        if config.breaking_alerts_enabled:
            breaking_scheduler.start()
    except Exception:
        LOGGER.exception("Unable to start news scheduler")
    LOGGER.info("CodexBot started as %s at %s", bot.user, ist_time())


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    emoji = str(payload.emoji)
    if emoji not in {"👍", "👎"} or bot.user and payload.user_id == bot.user.id:
        return
    database.record_reaction(payload.message_id, payload.user_id, emoji)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    emoji = str(payload.emoji)
    if emoji in {"👍", "👎"}:
        database.remove_reaction(payload.message_id, payload.user_id)


if __name__ == "__main__":
    bot.run(config.token)
