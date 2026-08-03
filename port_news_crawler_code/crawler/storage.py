"""Persistence layer.

Two responsibilities:
  1. Deduplication: remember which URLs have already been processed so the
     continuous loop only downloads *newly published* articles.
  2. Storage: write each article as a pretty JSON file under
     ``data/articles/<site>/<date>/<hash>.json`` and record metadata in SQLite.

SQLite is used (rather than a flat file) so dedup lookups stay fast even with
hundreds of thousands of seen URLs, and so the system is restart-safe.
"""

from __future__ import annotations

import hashlib
import json
import logging
from os import path
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .extractor import Article

log = logging.getLogger("crawler.storage")


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class Storage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.articles_dir = self.data_dir / "articles"
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "crawler.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self):
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    url_hash    TEXT PRIMARY KEY,
                    url         TEXT NOT NULL,
                    site        TEXT NOT NULL,
                    title       TEXT,
                    published   TEXT,
                    author      TEXT,
                    language    TEXT,
                    fetched_at  TEXT NOT NULL,
                    json_path   TEXT,
                    complete    INTEGER DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_site ON articles(site)"
            )
            # Track URLs we have *seen* on a list page even before fetching,
            # plus failed fetches, so we don't retry forever in one session.
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_urls (
                    url_hash   TEXT PRIMARY KEY,
                    url        TEXT NOT NULL,
                    site       TEXT NOT NULL,
                    status     TEXT NOT NULL,   -- queued|saved|failed
                    attempts   INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )

    # -- dedup --------------------------------------------------------
    def is_done(self, url: str) -> bool:
        """True if the URL was already saved successfully."""
        h = _url_hash(url)
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM seen_urls WHERE url_hash=?", (h,)
            ).fetchone()
        return bool(row and row[0] == "saved")

    def should_fetch(self, url: str, max_attempts: int = 4) -> bool:
        """Whether to (re)attempt a URL: not saved, and under attempt cap."""
        h = _url_hash(url)
        with self._lock:
            row = self._conn.execute(
                "SELECT status, attempts FROM seen_urls WHERE url_hash=?", (h,)
            ).fetchone()
        if row is None:
            return True
        status, attempts = row
        if status == "saved":
            return False
        return attempts < max_attempts

    def mark(self, url: str, site: str, status: str):
        h = _url_hash(url)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO seen_urls (url_hash, url, site, status, attempts, updated_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    status=excluded.status,
                    attempts=seen_urls.attempts + 1,
                    updated_at=excluded.updated_at
                """,
                (h, url, site, status, now),
            )

    # -- save ---------------------------------------------------------
    def save_article(self, art: Article) -> Path:
        """Write the article JSON to disk and record it in SQLite."""
        date_part = "unknown-date"
        if art.published:
            date_part = str(art.published)[:10]
        out_dir = self.articles_dir / art.site / date_part
        out_dir.mkdir(parents=True, exist_ok=True)

        fname = f"{_url_hash(art.url)[:16]}.json"
        path = out_dir / fname
        
        # 转换为学长脚本要求的格式
        published = art.published
        if published:
            try:
                date_str = published.split('T')[0] if 'T' in published else published
            except:
                date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        else:
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        news_data = {
            "date": date_str,
            "title": art.title or "",
            "content": art.text or "",
            "source": art.site
        }

        path.write_text(
            json.dumps(news_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        h = _url_hash(art.url)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO articles
                    (url_hash, url, site, title, published, author, language,
                     fetched_at, json_path, complete)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    title=excluded.title, published=excluded.published,
                    author=excluded.author, language=excluded.language,
                    fetched_at=excluded.fetched_at, json_path=excluded.json_path,
                    complete=excluded.complete
                """,
                (h, art.url, art.site, art.title, art.published, art.author,
                 art.language, now, str(path), int(art.is_complete())),
            )
            self._conn.execute(
                """
                INSERT INTO seen_urls (url_hash, url, site, status, attempts, updated_at)
                VALUES (?, ?, ?, 'saved', 1, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    status='saved', attempts=seen_urls.attempts + 1, updated_at=excluded.updated_at
                """,
                (h, art.url, art.site, now),
            )
        return path

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            per_site = dict(
                self._conn.execute(
                    "SELECT site, COUNT(*) FROM articles GROUP BY site"
                ).fetchall()
            )
        return {"total": total, "per_site": per_site}

    def close(self):
        with self._lock:
            self._conn.close()
