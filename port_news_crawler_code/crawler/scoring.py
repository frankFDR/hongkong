"""Adapter from crawler articles to the shared scoring and MySQL pipeline."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


PORT_ROOT = Path(__file__).resolve().parents[2]
if str(PORT_ROOT) not in sys.path:
    sys.path.insert(0, str(PORT_ROOT))

from database.auto_news_pipeline import (  # noqa: E402
    DEFAULT_TEXTILE_BASELINE,
    DEFAULT_THROUGHPUT_BASELINE,
    insert_rows,
    process_news,
)


class NewsScorer:
    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled", True))
        self.table = str(config.get("table", "news_text"))
        self.api_key_env = str(config.get("api_key_env", "DEEPSEEK_API_KEY"))
        self.args = SimpleNamespace(
            base_url=config.get("base_url", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
            model=config.get("model", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")),
            content_chars=int(config.get("content_chars", 900)),
            batch_size=int(config.get("batch_size", 20)),
            context_tokens=int(config.get("context_tokens", 256000)),
            call_tokens=int(config.get("call_tokens", 8000)),
            token_buffer=int(config.get("token_buffer", 2000)),
            temperature=float(config.get("temperature", 0.1)),
            timeout=int(config.get("timeout", 180)),
            retries=int(config.get("retries", 3)),
            retry_sleep=float(config.get("retry_sleep", 5.0)),
            throughput_baseline=Path(
                config.get("throughput_baseline", DEFAULT_THROUGHPUT_BASELINE)
            ),
            textile_baseline=Path(config.get("textile_baseline", DEFAULT_TEXTILE_BASELINE)),
        )

    def process_and_insert(self, articles: list) -> set[str]:
        """Score and insert articles, returning URLs successfully inserted."""
        if not articles:
            return set()
        if not self.enabled:
            raise RuntimeError("scoring.enabled=false：禁止在未评分时将 URL 标记为已完成")

        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"环境变量 {self.api_key_env} 未设置")

        items = [
            {
                "date": article.published or datetime.now(timezone.utc).date().isoformat(),
                "title": article.title or "",
                "content": article.text or "",
                "source": article.site,
                "url": article.url,
            }
            for article in articles
        ]
        rows = process_news(items, self.args, api_key)
        insert_rows(rows, self.table)
        return {row["url"] for row in rows if row.get("url")}
