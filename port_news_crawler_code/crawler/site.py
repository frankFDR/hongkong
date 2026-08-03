"""Per-site configuration and list-page link discovery.

A ``SiteConfig`` describes how to find article URLs for one news source.
``discover_links`` parses a rendered index/search page and returns the set of
absolute URLs that look like articles (matching the site's patterns).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

log = logging.getLogger("crawler.site")


@dataclass
class SiteConfig:
    name: str
    list_urls: list[str]
    article_url_patterns: list[str]
    exclude_patterns: list[str] = field(default_factory=list)
    enabled: bool = True
    language: str | None = None
    wait_css: str | None = None
    render_wait: float = 2.0
    poll_interval: int = 900

    # runtime state (not from config)
    next_run_at: float = 0.0

    def __post_init__(self):
        self._include_re = [re.compile(p, re.I) for p in self.article_url_patterns]
        self._exclude_re = [re.compile(p, re.I) for p in self.exclude_patterns]

    @classmethod
    def from_dict(cls, d: dict, default_poll: int) -> "SiteConfig":
        return cls(
            name=d["name"],
            list_urls=d.get("list_urls", []),
            article_url_patterns=d.get("article_url_patterns", []),
            exclude_patterns=d.get("exclude_patterns", []),
            enabled=d.get("enabled", True),
            language=d.get("language"),
            wait_css=d.get("wait_css"),
            render_wait=float(d.get("render_wait", 2.0)),
            poll_interval=int(d.get("poll_interval", default_poll)),
        )

    def is_article_url(self, url: str) -> bool:
        if any(rx.search(url) for rx in self._exclude_re):
            return False
        return any(rx.search(url) for rx in self._include_re)

    def due(self) -> bool:
        return time.time() >= self.next_run_at

    def schedule_next(self):
        self.next_run_at = time.time() + self.poll_interval


def _clean(url: str) -> str:
    url, _ = urldefrag(url)            # drop #fragment
    return url.rstrip()


def discover_links(html: str, base_url: str, site: SiteConfig) -> list[str]:
    """Return ordered, de-duplicated article URLs found on a list page."""
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).netloc
    found: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = _clean(urljoin(base_url, href))
        # Stay on the same registrable host family (allow www/edition/service subdomains).
        host = urlparse(absolute).netloc
        if host and base_host and not _same_site(host, base_host):
            # still allow if it matches an explicit article pattern (e.g. edition.cnn.com)
            if not site.is_article_url(absolute):
                continue
        if absolute in seen:
            continue
        if site.is_article_url(absolute):
            seen.add(absolute)
            found.append(absolute)

    log.debug("[%s] discovered %d article links on %s", site.name, len(found), base_url)
    return found


def _same_site(host_a: str, host_b: str) -> bool:
    """Compare hosts ignoring common subdomain prefixes."""
    def root(h: str) -> str:
        parts = h.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h

    return root(host_a) == root(host_b)
