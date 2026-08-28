"""SQLite persistence for article delivery state."""

from contextlib import closing
from datetime import datetime, timezone
import os
import sqlite3
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
                    posted_at TEXT
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
            ):
                if column not in columns:
                    connection.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")
            connection.commit()

    def claim(self, article: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        stale_before = (datetime.now(timezone.utc).timestamp() - 3600)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO articles
                (url, source, title, summary, published, category, image_url, feed_group, devops_only, created, status, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (article["url"], article["source"], article["title"], article["summary"],
                 article["published"], article["category"], article.get("image_url"), article.get("feed_group"),
                 int(bool(article.get("devops_only"))), now, now),
            )
            claimed = cursor.rowcount == 1
            if not claimed:
                existing = connection.execute(
                    "SELECT status, claimed_at FROM articles WHERE url=?",
                    (article["url"],),
                ).fetchone()
                if existing and existing["status"] == "pending" and existing["claimed_at"]:
                    try:
                        claimed_timestamp = datetime.fromisoformat(existing["claimed_at"]).timestamp()
                    except ValueError:
                        claimed_timestamp = 0
                    if claimed_timestamp < stale_before:
                        cursor = connection.execute(
                            "UPDATE articles SET claimed_at=? WHERE url=? AND status='pending' AND claimed_at=?",
                            (now, article["url"], existing["claimed_at"]),
                        )
                        claimed = cursor.rowcount == 1
            connection.commit()
            return claimed

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
                       devops_only, created, status, posted_at
                FROM articles
                {where}
                ORDER BY COALESCE(posted_at, claimed_at, created) DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]
