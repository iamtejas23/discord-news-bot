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
- `NEWS_ARTICLE_RETENTION_DAYS` (default: `7`)
- `NEWS_PREFERENCE_RETENTION_DAYS` (default: `90`)

SQLite claims an article before delivery and marks it posted only after Discord accepts the message. Failed sends are released for retry, and abandoned claims expire after one hour. Keep the `/data` volume when deploying so duplicate protection survives restarts.

Posted article details are automatically deleted after `NEWS_ARTICLE_RETENTION_DAYS`. Reaction preference events retain only compact source/category feedback for `NEWS_PREFERENCE_RETENTION_DAYS`, so `/rankings` continues working after article cleanup. URL fingerprints are retained separately to prevent an old RSS item from being reposted after its article details are deleted.

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
NEWS_ARTICLE_RETENTION_DAYS=7
NEWS_PREFERENCE_RETENTION_DAYS=90
LOG_LEVEL=INFO
```

Then start the bot:

```bash
APP_UID="$(id -u)" APP_GID="$(id -g)" docker compose up -d --build
```

The container runs as a non-root user whose UID/GID match the command's values. The compose file mounts `./data` to `/data`; keep that folder between deploys so the SQLite database continues preventing duplicate posts.

## CI/CD and OCI deployment

`.github/workflows/ci.yml` runs on pull requests and pushes to `main`. It compiles the Python sources, audits the pinned Python dependencies with `pip-audit`, runs the configured SonarQube scan and waits for its quality gate, checks Terraform with Checkov when `.tf` files are present, scans the repository with Trivy, builds the Docker image, and scans that image with Trivy. High and critical Trivy findings fail the checks. Deployment runs only after all checks pass on `main`.

Add `SONAR_TOKEN` as a repository or organization Actions secret so the CI job can access it. For SonarQube Server, also define the `SONAR_HOST_URL` Actions variable with the server's base URL. If `SONAR_HOST_URL` is unset, the workflow defaults to SonarQube Cloud at `https://sonarcloud.io`. Create the SonarQube project first and ensure its project key (and, for SonarQube Cloud, organization key) matches `sonar-project.properties` and your SonarQube setup.

Configure a GitHub Environment named `production` under **Settings → Environments**. Add required reviewers and, if desired, deployment branch restrictions for `main`. GitHub pauses the deploy job for the configured reviewers before it receives the Environment secrets. Add these secrets to that Environment:

| Secret | Value |
| --- | --- |
| `OCI_HOST` | OCI instance public DNS name or IP address |
| `OCI_USER` | SSH user with access to the deployment directory and Docker Compose |
| `OCI_SSH_KEY` | Private SSH deploy key for that user (use a dedicated key) |
| `OCI_KNOWN_HOSTS` | Verified SSH host key line(s) for the OCI server, obtained and checked through a trusted channel |

SonarQube Server must be reachable from GitHub Actions, and the project and quality gate must exist there. The workflow waits for that gate and fails when it does not pass. [SonarSource documents the token and host URL requirements](https://github.com/SonarSource/sonarqube-scan-action#environment-variables).

Optionally set the `OCI_DEPLOY_PATH` variable on the `production` Environment. It defaults to `/opt/discord-news-bot`; the SSH user must be able to create and write this directory and run Docker Compose. The server must have Docker Engine and the Compose plugin installed. Create the deployment directory's `.env` file on the OCI server with `DISCORD_TOKEN` and `DISCORD_CHANNEL_ID` (plus any desired optional settings) before the first deployment. The workflow transfers the validated source over SSH while preserving that `.env` file and the `data/` SQLite volume. It builds the image with the OCI SSH user's UID/GID, stops the bot, adjusts the persistent data ownership through a temporary root container, and restarts the bot as that non-root UID/GID.

The SSH connection requires strict host key checking. To set `OCI_KNOWN_HOSTS`, get the server's public SSH host key from a trusted OCI console session with `sudo cat /etc/ssh/ssh_host_ed25519_key.pub`, and get its fingerprint with `sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. From your workstation, run `ssh-keyscan -t ed25519 <OCI_HOST> 2>/dev/null | ssh-keygen -lf -` and compare the fingerprint with the one from the OCI console. Only after they match, add the `ssh-keyscan` output line as the `OCI_KNOWN_HOSTS` Environment secret. The host field in that line must match `OCI_HOST` exactly. Keep the private key and application secrets only in GitHub Environment secrets or on the server, and rotate them if exposed. The workflow does not need OCI API keys because it deploys to the instance over SSH. Configure the matching `sonar.projectKey` in `sonar-project.properties` if it differs from `discord-news-bot`.
