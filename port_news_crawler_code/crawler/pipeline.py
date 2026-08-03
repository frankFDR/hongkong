"""Crawl orchestration + the never-ending polling loop.

``run_forever`` loops indefinitely. On every tick it asks each site whether it
is due (based on its ``poll_interval``); for due sites it discovers links from
the list pages, fetches only *new* article URLs, extracts the content and
persists it. Between sites it sleeps until the next site is due.
"""

from __future__ import annotations

import logging
import random
import signal
import time
from pathlib import Path

import yaml

from .browser import Browser, BrowserConfig, page_is_blocked, page_is_challenge
from .extractor import extract
from .logging_setup import setup_logging
from .scoring import NewsScorer
from .site import SiteConfig, discover_links
from .storage import Storage

log = logging.getLogger("crawler.pipeline")


class Crawler:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        with open(self.config_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        g = cfg.get("global", {})
        base = self.config_path.parent
        self.data_dir = (base / g.get("data_dir", "data")).resolve()
        self.log_dir = (base / g.get("log_dir", "logs")).resolve()

        setup_logging(self.log_dir)

        self.min_delay = float(g.get("min_delay_between_pages", 2.5))
        self.max_delay = float(g.get("max_delay_between_pages", 6.0))
        self.max_new_per_cycle = int(g.get("max_new_articles_per_cycle", 25))
        self.default_poll = int(g.get("default_poll_interval", 900))
        self.max_retries = int(g.get("max_retries", 3))

        self.browser = Browser(BrowserConfig(
            headless=bool(g.get("headless", True)),
            use_undetected=bool(g.get("use_undetected", True)),
            page_load_timeout=int(g.get("page_load_timeout", 45)),
            user_agent=g.get("user_agent"),
            challenge_wait=float(g.get("challenge_wait", 12.0)),
        ))
        self.storage = Storage(self.data_dir)
        self.scorer = NewsScorer(g.get("scoring", {}))

        self.sites = [
            SiteConfig.from_dict(s, self.default_poll)
            for s in cfg.get("sites", [])
            if s.get("enabled", True)
        ]

        self._stop = False

    # -- single-page fetch with retry --------------------------------
    def _fetch_html(self, url: str, wait_css: str | None, render_wait: float) -> str | None:
        for attempt in range(1, self.max_retries + 1):
            try:
                return self.browser.get(url, wait_css=wait_css, render_wait=render_wait)
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch attempt %d/%d failed for %s: %s",
                            attempt, self.max_retries, url, exc)
                if attempt < self.max_retries:
                    time.sleep(2 * attempt)
                    try:
                        self.browser.restart()
                    except Exception:  # noqa: BLE001
                        pass
        return None

    def _polite_sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    # -- one site cycle ----------------------------------------------
    def crawl_site(self, site: SiteConfig) -> int:
        log.info("[%s] crawling %d list page(s)", site.name, len(site.list_urls))
        candidate_urls: list[str] = []
        seen: set[str] = set()

        for list_url in site.list_urls:
            html = self._fetch_html(list_url, site.wait_css, site.render_wait)
            if not html:
                log.warning("[%s] could not load list page %s", site.name, list_url)
                continue
            for u in discover_links(html, list_url, site):
                if u not in seen:
                    seen.add(u)
                    candidate_urls.append(u)
            self._polite_sleep()

        # Keep only URLs we still need to fetch.
        new_urls = [u for u in candidate_urls if self.storage.should_fetch(u)]
        log.info("[%s] %d candidates, %d new to fetch",
                 site.name, len(candidate_urls), len(new_urls))

        saved = 0
        extracted = []
        for url in new_urls[: self.max_new_per_cycle]:
            if self._stop:
                break
            html = self._fetch_html(url, None, site.render_wait)
            if not html:
                self.storage.mark(url, site.name, "failed")
                continue
            if page_is_blocked(html) or page_is_challenge(html):
                log.warning("[%s] blocked/challenged, not saving %s", site.name, url)
                self.storage.mark(url, site.name, "failed")
                self._polite_sleep()
                continue
            try:
                art = extract(html, url, site.name, site.language)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] extraction failed for %s: %s", site.name, url, exc)
                self.storage.mark(url, site.name, "failed")
                self._polite_sleep()
                continue

            if not art.title and not art.text:
                log.info("[%s] empty article, skipping %s", site.name, url)
                self.storage.mark(url, site.name, "failed")
            else:
                extracted.append(art)
            self._polite_sleep()

        if extracted:
            try:
                inserted_urls = self.scorer.process_and_insert(extracted)
            except Exception as exc:  # noqa: BLE001
                log.exception("[%s] scoring/database stage failed: %s", site.name, exc)
                for art in extracted:
                    self.storage.mark(art.url, site.name, "failed")
                return 0

            for art in extracted:
                if art.url not in inserted_urls:
                    log.warning("[%s] scoring failed; article skipped: %s", site.name, art.url)
                    self.storage.mark(art.url, site.name, "failed")
                    continue
                path = self.storage.save_article(art)
                flag = "OK" if art.is_complete() else "PARTIAL"
                saved += 1
                log.info("[%s] scored and inserted [%s] %.70s (%s)", site.name, flag,
                         art.title or "(no title)", path.name)

        return saved

    # -- main loop ----------------------------------------------------
    def run_forever(self):
        self._install_signal_handlers()
        log.info("Crawler starting. Sites: %s",
                 ", ".join(s.name for s in self.sites))
        try:
            while not self._stop:
                due_sites = [s for s in self.sites if s.due()]
                if not due_sites:
                    self._sleep_until_next()
                    continue

                for site in due_sites:
                    if self._stop:
                        break
                    try:
                        saved = self.crawl_site(site)
                        log.info("[%s] cycle done, %d new article(s). Stats: %s",
                                 site.name, saved, self.storage.stats())
                    except Exception as exc:  # noqa: BLE001
                        log.exception("[%s] cycle crashed: %s", site.name, exc)
                        try:
                            self.browser.restart()
                        except Exception:  # noqa: BLE001
                            pass
                    finally:
                        site.schedule_next()
        finally:
            self.shutdown()

    def run_once(self):
        """Run a single cycle over all sites (useful for testing)."""
        for site in self.sites:
            try:
                saved = self.crawl_site(site)
                log.info("[%s] single cycle done, %d new article(s)", site.name, saved)
            except Exception as exc:  # noqa: BLE001
                log.exception("[%s] cycle crashed: %s", site.name, exc)
        self.shutdown()

    def _sleep_until_next(self):
        upcoming = min((s.next_run_at for s in self.sites), default=time.time() + 30)
        wait = max(5.0, min(60.0, upcoming - time.time()))
        log.debug("No site due; sleeping %.0fs", wait)
        # sleep in small chunks so Ctrl-C is responsive
        end = time.time() + wait
        while time.time() < end and not self._stop:
            time.sleep(1)

    def _install_signal_handlers(self):
        def handler(signum, _frame):
            log.info("Received signal %s, shutting down after current task...", signum)
            self._stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not in main thread / unsupported platform

    def shutdown(self):
        log.info("Final stats: %s", self.storage.stats())
        self.browser.quit()
        self.storage.close()
        log.info("Crawler stopped.")


def run_forever(config_path: str | Path = "config.yaml"):
    Crawler(config_path).run_forever()
