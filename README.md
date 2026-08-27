# Discord News Bot

The bot fetches RSS news, publishes Discord embeds, and stores delivery state in SQLite so an article is not posted twice. The existing deployment contract remains unchanged: run `python app/bot.py` or use Docker Compose.

## Configuration

Required environment variables:

- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`

Optional variables:

- `NEWS_DATABASE_PATH` (default: `/data/news.db`)
- `NEWS_INTERVAL_MINUTES` (default: `30`)
- `NEWS_RECENT_HOURS` (default: `24`)
- `LOG_LEVEL` (default: `INFO`)

SQLite claims an article before delivery and marks it posted only after Discord accepts the message. Failed sends are released for retry, and abandoned claims expire after one hour. Keep the `/data` volume when deploying so duplicate protection survives restarts.

Only articles published within `NEWS_RECENT_HOURS` are eligible for posting. Entries without a valid published or updated date are skipped.

Available slash commands are `/news`, `/status`, `/sources`, and `/health`.
