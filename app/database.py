"""SQLite persistence for article delivery state."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
import re
import sqlite3
import hashlib
import difflib
from typing import Any


class Database:
    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    url TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    published TEXT,
                    category TEXT NOT NULL,
                    image_url TEXT,
                    feed_group TEXT,
                    devops_only INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'posted',
                    claimed_at TEXT,
                    posted_at TEXT,
                    story_id TEXT,
                    message_id TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
            for column, definition in (
                ("status", "TEXT NOT NULL DEFAULT 'posted'"),
                ("claimed_at", "TEXT"),
                ("posted_at", "TEXT"),
                ("image_url", "TEXT"),
                ("feed_group", "TEXT"),
                ("devops_only", "INTEGER NOT NULL DEFAULT 0"),
                ("story_id", "TEXT"),
                ("message_id", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")
            connection.commit()

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS article_reactions (
                    message_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    created TEXT NOT NULL,
                    PRIMARY KEY (message_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS url_fingerprints (
                    url TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS preference_events (
                    message_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    created TEXT NOT NULL,
                    PRIMARY KEY (message_id, user_id, source, category)
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO url_fingerprints (url, first_seen, last_seen)
                SELECT url, created, COALESCE(posted_at, created) FROM articles
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO preference_events
                    (message_id, user_id, source, category, emoji, created)
                SELECT r.message_id, r.user_id, a.source, a.category, r.emoji, r.created
                FROM article_reactions r
                JOIN articles a ON a.message_id = r.message_id
                WHERE r.emoji IN ('👍', '👎')
                """
            )
            connection.commit()

    def _compute_story_id(self, article: dict[str, Any], existing_stories: list[dict[str, Any]], threshold: float = 0.6) -> str | None:
        """Determine story_id for a new article by comparing against existing stories.
        Returns an existing story_id if similar, or None to create a new story."""
        norm_title = self._normalize_text(article.get("title", ""))
        norm_summary = self._normalize_text(article.get("summary", ""))

        for story in existing_stories:
            story_title = self._normalize_text(story.get("canonical_title", ""))
            story_summary = self._normalize_text(story.get("summary", ""))
            jac_title = self.jaccard_similarity(norm_title, story_title)
            jac_summary = self.jaccard_similarity(norm_summary, story_summary)
            sequence_title = difflib.SequenceMatcher(None, norm_title, story_title).ratio()
            sequence_summary = difflib.SequenceMatcher(None, norm_summary, story_summary).ratio()
            if max(jac_title, jac_summary, sequence_title, sequence_summary) >= threshold:
                return story["story_id"]
        return None

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

    def claim(self, article: dict[str, Any], story_dedup_threshold: float = 0.6) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        stale_before = (datetime.now(timezone.utc).timestamp() - 3600)
        with closing(self._connect()) as connection:
            fingerprint = connection.execute(
                "SELECT url FROM url_fingerprints WHERE url=?", (article["url"],)
            ).fetchone()
            existing_article = connection.execute(
                "SELECT status, claimed_at FROM articles WHERE url=?", (article["url"],)
            ).fetchone()
            if fingerprint and existing_article is None:
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO articles
                (url, source, title, summary, published, category, image_url, feed_group,
                 devops_only, created, status, claimed_at, story_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (article["url"], article["source"], article["title"], article["summary"],
                 article.get("published", ""), article["category"], article.get("image_url"),
                 article.get("feed_group"), int(bool(article.get("devops_only"))), now, now, None),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    "INSERT OR IGNORE INTO url_fingerprints (url, first_seen, last_seen) VALUES (?, ?, ?)",
                    (article["url"], now, now),
                )
            if cursor.rowcount == 0:
                existing = existing_article
                claimed = False
                if existing and existing["status"] == "pending" and existing["claimed_at"]:
                    try:
                        claimed_timestamp = datetime.fromisoformat(existing["claimed_at"]).timestamp()
                    except ValueError:
                        claimed_timestamp = 0
                    if claimed_timestamp < stale_before:
                        claimed = connection.execute(
                            "UPDATE articles SET claimed_at=? WHERE url=? AND status='pending' AND claimed_at=?",
                            (now, article["url"], existing["claimed_at"]),
                        ).rowcount == 1
                connection.commit()
                return claimed

            existing_stories_rows = connection.execute(
                "SELECT story_id, MIN(title) AS canonical_title, MIN(summary) AS summary, "
                "MIN(source) AS source, COUNT(*) AS article_count "
                "FROM articles WHERE story_id IS NOT NULL GROUP BY story_id"
            ).fetchall()
            existing_stories: list[dict[str, Any]] = []
            for row in existing_stories_rows:
                existing_stories.append({
                    "story_id": row["story_id"],
                    "canonical_title": row["canonical_title"] or "",
                    "summary": row["summary"] or "",
                    "source": row["source"] or "",
                    "article_count": row["article_count"],
                })

            story_id = self._compute_story_id(article, existing_stories, story_dedup_threshold)

            if story_id is None:
                # Create a new story_id (use a hash-like identifier based on title)
                story_id = f"story_{hashlib.sha256(article['title'].encode()).hexdigest()[:12]}"

            article["story_id"] = story_id
            connection.execute("UPDATE articles SET story_id=? WHERE url=?", (story_id, article["url"]))
            connection.commit()
            return True

    def mark_posted(self, url: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE articles SET status='posted', posted_at=? WHERE url=?",
                (datetime.now(timezone.utc).isoformat(), url),
            )
            connection.commit()

    def record_message(self, url: str, message_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute("UPDATE articles SET message_id=? WHERE url=?", (str(message_id), url))
            connection.commit()

    def record_reaction(self, message_id: int, user_id: int, emoji: str) -> None:
        with closing(self._connect()) as connection:
            created = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT OR REPLACE INTO article_reactions (message_id, user_id, emoji, created) VALUES (?, ?, ?, ?)",
                (str(message_id), str(user_id), emoji, created),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO preference_events
                    (message_id, user_id, source, category, emoji, created)
                SELECT ?, ?, source, category, ?, ?
                FROM articles
                WHERE message_id=?
                """,
                (str(message_id), str(user_id), emoji, created, str(message_id)),
            )
            connection.commit()

    def remove_reaction(self, message_id: int, user_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM article_reactions WHERE message_id=? AND user_id=?",
                (str(message_id), str(user_id)),
            )
            connection.execute(
                "DELETE FROM preference_events WHERE message_id=? AND user_id=?",
                (str(message_id), str(user_id)),
            )
            connection.commit()

    def reaction_rankings(self, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        limit = min(max(limit, 1), 25)
        with closing(self._connect()) as connection:
            source_rows = connection.execute(
                """
                SELECT source, emoji, COUNT(*) AS votes
                FROM preference_events
                WHERE emoji IN ('👍', '👎')
                GROUP BY source, emoji
                ORDER BY votes DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            category_rows = connection.execute(
                """
                SELECT category, emoji, COUNT(*) AS votes
                FROM preference_events
                WHERE emoji IN ('👍', '👎')
                GROUP BY category, emoji
                ORDER BY votes DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {
            "sources": [dict(row) for row in source_rows if row["source"]],
            "categories": [dict(row) for row in category_rows if row["category"]],
        }

    def cleanup_retention(self, article_days: int = 7, preference_days: int = 90) -> dict[str, int]:
        """Remove old article details while retaining compact preference history."""
        now = datetime.now(timezone.utc)
        article_cutoff = (now - timedelta(days=article_days)).isoformat()
        preference_cutoff = (now - timedelta(days=preference_days)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO preference_events
                    (message_id, user_id, source, category, emoji, created)
                SELECT r.message_id, r.user_id, a.source, a.category, r.emoji, r.created
                FROM article_reactions r
                JOIN articles a ON a.message_id = r.message_id
                WHERE r.emoji IN ('👍', '👎')
                """
            )
            preferences_deleted = connection.execute(
                "DELETE FROM preference_events WHERE created < ?", (preference_cutoff,)
            ).rowcount
            connection.execute("DELETE FROM article_reactions WHERE created < ?", (preference_cutoff,))
            articles_deleted = connection.execute(
                """
                DELETE FROM articles
                WHERE status='posted' AND COALESCE(posted_at, created) < ?
                """,
                (article_cutoff,),
            ).rowcount
            connection.commit()
        return {"articles": articles_deleted, "preferences": preferences_deleted}

    def release(self, url: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM articles WHERE url=? AND status='pending'", (url,))
            connection.commit()

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM articles GROUP BY status").fetchall()
        return {row["status"]: row["count"] for row in rows}

    def search_articles(
        self,
        keyword: str | None = None,
        source: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 10)
        filters = ["status = 'posted'"]
        params: list[Any] = []
        if keyword:
            filters.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
            keyword_param = f"%{keyword.lower()}%"
            params.extend([keyword_param, keyword_param])
        if source and source.lower() != "all":
            filters.append("LOWER(source) = ?")
            params.append(source.lower())
        if category and category.lower() != "all":
            filters.append("LOWER(category) = ?")
            params.append(category.lower())
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT url, source, title, summary, published, category, image_url, feed_group,
                       devops_only, created, status, posted_at, story_id, message_id
                FROM articles
                {where}
                ORDER BY COALESCE(posted_at, claimed_at, created) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stories(self, include_devops: bool = False) -> list[dict[str, Any]]:
        """Retrieve all grouped stories from the articles table."""
        with closing(self._connect()) as connection:
            query = """
                SELECT story_id, MIN(title) AS canonical_title, MIN(summary) AS summary,
                       GROUP_CONCAT(DISTINCT source) AS sources, COUNT(*) AS article_count,
                       MIN(created) AS first_seen
                FROM articles
                WHERE story_id IS NOT NULL
                GROUP BY story_id
                ORDER BY first_seen DESC
            """
            params: list[Any] = []
            if not include_devops:
                query = query.replace("WHERE story_id IS NOT NULL", "WHERE story_id IS NOT NULL AND devops_only = 0")
            rows = connection.execute(query, params).fetchall()
            stories = []
            for row in rows:
                stories.append({
                    "story_id": row["story_id"],
                    "canonical_title": row["canonical_title"] or "Untitled story",
                    "summary": row["summary"] or "",
                    "source": row["sources"] or "Unknown",
                    "article_count": row["article_count"],
                    "first_seen": row["first_seen"],
                })
            return stories