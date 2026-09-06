"""SQLite persistence for article delivery state."""

from contextlib import closing
from datetime import datetime, timezone
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
                    story_id TEXT
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
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")
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
            if cursor.rowcount == 0:
                existing = connection.execute(
                    "SELECT status, claimed_at FROM articles WHERE url=?", (article["url"],)
                ).fetchone()
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
                       devops_only, created, status, posted_at, story_id
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