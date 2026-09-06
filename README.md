# Discord News Bot

The bot fetches RSS news, publishes Discord embeds, and stores delivery state in SQLite so an article is not posted twice. The existing deployment contract remains unchanged: run `python app/bot.py` or use Docker Compose.

## Features

- Scheduled RSS publishing to one Discord channel
- Slash commands for on-demand news, stored article search, DevOps news, cricket, geo-politics, space and astronomy, source lists, health, logs, and feed status
- Source, category, topic, and article-limit filters for on-demand commands
- Discord autocomplete for source and category filters
- SQLite duplicate protection with pending-claim retry handling
- Recent-news filtering so old RSS entries are not reposted
- Optional DevOps and Cloud feed set for Kubernetes, AWS, Azure, GCP, Terraform, Docker, Linux, CI/CD, DevSecOps, and cloud security
- Dedicated topic feeds for cricket, geo-politics, and space/astronomy
- Rich Discord embeds with source, category, publish time, priority markers, feed images when available, and action buttons
- Optional daily digest mode that posts one summary embed with the top stories
- Optional breaking-news alert loop for configured urgent keywords
- Reaction-based source and topic preference rankings using 👍 and 👎 feedback

## Configuration

Required environment variables:

- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`

Optional variables:

- `NEWS_DATABASE_PATH` (default: `/data/news.db`)
- `NEWS_INTERVAL_MINUTES` (default: `30`)
- `NEWS_RECENT_HOURS` (default: `24`)
- `NEWS_PUBLISH_BATCH_SIZE` (default: `5`)
- `NEWS_COMMAND_DEFAULT_LIMIT` (default: `10`)
- `DEVOPS_FEEDS_ENABLED` (default: `true`)
- `LOG_LEVEL` (default: `INFO`)
- `NEWS_DIGEST_ENABLED` (default: `false`)
- `NEWS_DIGEST_HOUR_UTC` (default: `8`)
- `NEWS_BREAKING_ALERTS_ENABLED` (default: `true`)
- `NEWS_BREAKING_INTERVAL_MINUTES` (default: `5`)
- `NEWS_BREAKING_KEYWORDS` (default: `breaking,urgent,alert,emergency,outage,attack,earthquake,war`)

SQLite claims an article before delivery and marks it posted only after Discord accepts the message. Failed sends are released for retry, and abandoned claims expire after one hour. Keep the `/data` volume when deploying so duplicate protection survives restarts.

Only articles published within `NEWS_RECENT_HOURS` are eligible for posting. Entries without a valid published or updated date are skipped.

`NEWS_PUBLISH_BATCH_SIZE` controls how many articles the scheduler posts each interval. `NEWS_COMMAND_DEFAULT_LIMIT` controls how many articles on-demand news commands publish when no limit is provided. Slash command limits are clamped to 1-25 articles.

## Slash Commands

- `/news` publishes general news. Optional filters: `topic`, `source`, `category`, and `limit`.
- `/devops` publishes DevOps and Cloud news. Optional filters: `topic`, `source`, `category`, and `limit`.
- `/cricket` publishes cricket news. Optional filters: `topic`, `source`, `category`, and `limit`.
- `/geo-politics` publishes geo-politics and foreign-policy news. Optional filters: `topic`, `source`, `category`, and `limit`.
- `/space` publishes space and astronomy news. Optional filters: `topic`, `source`, `category`, and `limit`.
- `/search-news` searches stored SQLite articles. Optional filters: `keyword`, `source`, `category`, and `limit`.
- `/sources` lists configured general, cricket, geo-politics, and space/astronomy sources. Set `include_devops` to include DevOps and Cloud sources.
- `/status` shows scheduler settings, feed availability, and stored/pending article counts.
- `/health` shows whether the bot is online, server count, and current IST time.
- `/logs` shows the last 10 captured application log lines from the running container. Requires Manage Server permission and responds ephemerally.
- `/rankings` shows source and topic preferences calculated from stored 👍 and 👎 reactions.

`source` and `category` options support Discord autocomplete. News embeds include `Read Article`, `More from Source`, and `Similar Topic` buttons. Set `DEVOPS_FEEDS_ENABLED=false` to disable `/devops` without affecting the general news sources.

When `NEWS_DIGEST_ENABLED=true`, the regular interval scheduler is replaced by one daily digest at `NEWS_DIGEST_HOUR_UTC`. Breaking alerts continue on their own interval when enabled. Reacting to an individual article, or to a digest containing several articles, records one preference vote per user and message.

## Docker Compose

Create a `.env` file next to `docker-compose.yml`:

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=123456789012345678
NEWS_INTERVAL_MINUTES=30
NEWS_RECENT_HOURS=24
NEWS_PUBLISH_BATCH_SIZE=5
NEWS_COMMAND_DEFAULT_LIMIT=10
DEVOPS_FEEDS_ENABLED=true
NEWS_DIGEST_ENABLED=false
NEWS_DIGEST_HOUR_UTC=8
NEWS_BREAKING_ALERTS_ENABLED=true
NEWS_BREAKING_INTERVAL_MINUTES=5
LOG_LEVEL=INFO
```

Then start the bot:

```bash
docker compose up -d --build
```

The compose file mounts `./data` to `/data`; keep that folder between deploys so the SQLite database continues preventing duplicate posts.
