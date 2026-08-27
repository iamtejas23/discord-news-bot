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
                (url, source, title, summary, published, category, created, status, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (article["url"], article["source"], article["title"], article["summary"],
                 article["published"], article["category"], now, now),
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
                        connection.execute(
                            "UPDATE articles SET claimed_at=? WHERE url=? AND status='pending'",
                            (now, article["url"]),
                        )
                        claimed = True
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