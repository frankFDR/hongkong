"""Article content extraction.

Given the rendered HTML of an article page, extract a clean record with:
  - title
  - body (full plain-text)
  - published timestamp (ISO-8601 where possible)
  - author (best effort)

Primary engine is ``trafilatura`` (works well across CNN / Reuters / SCMP /
WordPress / gov sites). BeautifulSoup + meta-tag heuristics fill any gaps so
the three mandatory fields (title / body / timestamp) are as complete as
possible.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

log = logging.getLogger("crawler.extractor")


@dataclass
class Article:
    url: str
    site: str
    title: str = ""
    text: str = ""
    published: str | None = None      # ISO-8601 string
    author: str | None = None
    language: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_meta: dict = field(default_factory=dict)

    def is_complete(self) -> bool:
        """The three mandatory fields must be present and non-trivial."""
        return bool(self.title.strip()) and len(self.text.strip()) >= 120 and bool(self.published)

    def to_dict(self) -> dict:
        return asdict(self)


_DATE_META_KEYS = [
    ("meta", {"property": "article:published_time"}),
    ("meta", {"name": "article:published_time"}),
    ("meta", {"property": "og:article:published_time"}),
    ("meta", {"name": "publishdate"}),
    ("meta", {"name": "pubdate"}),
    ("meta", {"name": "date"}),
    ("meta", {"itemprop": "datePublished"}),
    ("meta", {"name": "dc.date.issued"}),
    ("meta", {"name": "DC.date.issued"}),
]

_TITLE_META_KEYS = [
    ("meta", {"property": "og:title"}),
    ("meta", {"name": "twitter:title"}),
]

_TITLE_SEPARATORS = (" | ", " - ", " — ", " – ", " :: ", " » ", " • ")


def _clean_title(title: str | None) -> str:
    """Collapse whitespace and drop a leading/trailing site-name segment."""
    if not title:
        return ""
    t = re.sub(r"\s+", " ", title).strip()
    for sep in _TITLE_SEPARATORS:
        if sep in t:
            parts = [p.strip() for p in t.split(sep) if p.strip()]
            if len(parts) >= 2:
                # The headline is almost always the longest segment.
                t = max(parts, key=len)
            break
    return t.strip()


def _normalise_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, OverflowError, TypeError):
        return value  # keep raw string rather than dropping the signal


def _from_jsonld(soup: BeautifulSoup) -> dict:
    """Pull title / date / author from schema.org JSON-LD blocks."""
    out: dict = {}
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        # Some sites nest under @graph.
        expanded = []
        for c in candidates:
            if isinstance(c, dict) and isinstance(c.get("@graph"), list):
                expanded.extend(c["@graph"])
            else:
                expanded.append(c)
        for c in expanded:
            if not isinstance(c, dict):
                continue
            t = c.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(x in ("NewsArticle", "Article", "ReportageNewsArticle", "WebPage") for x in types):
                out.setdefault("title", c.get("headline") or c.get("name"))
                out.setdefault("published", c.get("datePublished") or c.get("dateCreated"))
                author = c.get("author")
                if isinstance(author, dict):
                    out.setdefault("author", author.get("name"))
                elif isinstance(author, list) and author:
                    names = [a.get("name") for a in author if isinstance(a, dict) and a.get("name")]
                    if names:
                        out.setdefault("author", ", ".join(names))
                elif isinstance(author, str):
                    out.setdefault("author", author)
    return {k: v for k, v in out.items() if v}


def _meta_lookup(soup: BeautifulSoup, keys) -> str | None:
    for tag, attrs in keys:
        el = soup.find(tag, attrs=attrs)
        if el and el.get("content"):
            return el["content"]
    return None


def extract(html: str, url: str, site: str, language: str | None = None) -> Article:
    """Extract an :class:`Article` from rendered HTML."""
    art = Article(url=url, site=site, language=language)
    traf_title = ""

    # 1) trafilatura: best body extractor + decent metadata.
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if result:
            data = json.loads(result)
            art.text = (data.get("text") or "").strip()
            traf_title = (data.get("title") or "").strip()
            art.published = data.get("date")
            art.author = data.get("author")
            if data.get("language"):
                art.language = data.get("language")
    except Exception as exc:  # noqa: BLE001
        log.debug("trafilatura failed for %s: %s", url, exc)
        traf_title = ""

    # 2) BeautifulSoup fallbacks for the mandatory fields.
    soup = BeautifulSoup(html, "lxml")
    jsonld = _from_jsonld(soup)

    # Title: prefer explicit metadata (most reliable headline) over the
    # in-content heading, which is often a generic section label.
    h1 = soup.find("h1")
    title_candidates = [
        jsonld.get("title"),
        _meta_lookup(soup, _TITLE_META_KEYS),
        soup.title.get_text() if soup.title else None,
        traf_title,
        h1.get_text() if h1 else None,
    ]
    for cand in title_candidates:
        cleaned = _clean_title(cand)
        if cleaned:
            art.title = cleaned
            break

    if not art.published:
        art.published = jsonld.get("published") or _meta_lookup(soup, _DATE_META_KEYS)
        if not art.published:
            t = soup.find("time")
            if t:
                art.published = t.get("datetime") or t.get_text(strip=True)

    if not art.author:
        art.author = jsonld.get("author") or _meta_lookup(
            soup, [("meta", {"name": "author"}), ("meta", {"property": "article:author"})]
        )

    # If body is still thin, take a paragraph-based fallback.
    if len(art.text.strip()) < 120:
        article_node = soup.find("article") or soup.find("main") or soup.body
        if article_node:
            paras = [p.get_text(" ", strip=True) for p in article_node.find_all("p")]
            joined = "\n\n".join(p for p in paras if len(p) > 30)
            if len(joined) > len(art.text):
                art.text = joined

    art.published = _normalise_date(art.published)
    art.title = re.sub(r"\s+", " ", art.title or "").strip()
    art.raw_meta = jsonld
    return art
