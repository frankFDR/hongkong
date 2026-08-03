"""Selenium browser factory.

Creates a Chrome WebDriver, optionally via ``undetected-chromedriver`` which
patches the driver to evade common bot detection (Cloudflare, PerimeterX, ...).
A single browser instance is reused across the whole run for efficiency.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("crawler.browser")


# Markers that indicate an anti-bot interstitial / block page rather than content.
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies to continue",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
)
BLOCK_MARKERS = (
    "sorry, you have been blocked",
    "attention required! | cloudflare",
    "access denied",
    "error 1020",
    "you don't have permission to access",
)


def detect_chrome_major_version() -> int | None:
    """Best-effort detection of the installed Chrome major version (Windows)."""
    # 1) Registry beacon (most reliable on Windows).
    try:
        out = subprocess.run(
            ["reg", "query", r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon", "/v", "version"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
        if m:
            return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    # 2) chrome.exe file version via PowerShell.
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Item '{path}').VersionInfo.ProductVersion"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
            if m:
                return int(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def page_is_blocked(html: str) -> bool:
    low = (html or "").lower()[:4000]
    return any(m in low for m in BLOCK_MARKERS)


def page_is_challenge(html: str) -> bool:
    low = (html or "").lower()[:4000]
    return any(m in low for m in CHALLENGE_MARKERS)


@dataclass
class BrowserConfig:
    headless: bool = True
    use_undetected: bool = True
    page_load_timeout: int = 45
    user_agent: str | None = None
    window_size: tuple[int, int] = (1440, 900)
    chrome_version: int | None = None       # pin undetected driver; auto-detected if None
    challenge_wait: float = 12.0            # max extra seconds to let a JS challenge clear


class Browser:
    """Thin wrapper around a Selenium Chrome driver with lazy (re)creation."""

    def __init__(self, cfg: BrowserConfig):
        self.cfg = cfg
        self._driver = None
        if self.cfg.use_undetected and self.cfg.chrome_version is None:
            self.cfg.chrome_version = detect_chrome_major_version()
            if self.cfg.chrome_version:
                log.info("Detected Chrome major version: %s", self.cfg.chrome_version)

    # -- lifecycle ----------------------------------------------------
    def _build_options(self, undetected: bool):
        if undetected:
            import undetected_chromedriver as uc

            opts = uc.ChromeOptions()
        else:
            from selenium.webdriver.chrome.options import Options

            opts = Options()

        if self.cfg.headless:
            # "new" headless renders like a real browser; better for anti-bot.
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--blink-settings=imagesEnabled=false")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(f"--window-size={self.cfg.window_size[0]},{self.cfg.window_size[1]}")
        opts.add_argument("--lang=en-US,en;q=0.9,zh-HK;q=0.8")
        if self.cfg.user_agent:
            opts.add_argument(f"--user-agent={self.cfg.user_agent}")
        return opts

    def _create_driver(self):
        last_err: Exception | None = None
        # Try undetected first (if requested), then fall back to plain Selenium.
        for undetected in ([True, False] if self.cfg.use_undetected else [False]):
            try:
                opts = self._build_options(undetected)
                if undetected:
                    import undetected_chromedriver as uc

                    driver = uc.Chrome(
                        options=opts,
                        headless=self.cfg.headless,
                        version_main=self.cfg.chrome_version,
                    )
                else:
                    from selenium import webdriver

                    driver = webdriver.Chrome(options=opts)
                driver.set_page_load_timeout(self.cfg.page_load_timeout)
                log.info("Chrome driver started (undetected=%s, headless=%s)",
                         undetected, self.cfg.headless)
                return driver
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("Failed to start Chrome (undetected=%s): %s", undetected, exc)
        raise RuntimeError(f"Could not start Chrome driver: {last_err}")

    @property
    def driver(self):
        if self._driver is None:
            self._driver = self._create_driver()
        return self._driver

    def restart(self):
        """Recreate the driver (used to recover from a crashed session)."""
        self.quit()
        self._driver = self._create_driver()

    def quit(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:  # noqa: BLE001
                pass
            self._driver = None

    # -- navigation ---------------------------------------------------
    def get(self, url: str, wait_css: str | None = None,
            render_wait: float = 0.0) -> str:
        """Navigate to ``url`` and return the rendered page source.

        Raises on hard failures so callers can decide to retry/restart.
        """
        driver = self.driver
        try:
            driver.get(url)
        except TimeoutException:
            # Page load timed out but partial DOM may still be usable.
            log.debug("page load timeout (continuing with partial DOM): %s", url)
        except WebDriverException as exc:
            # Session likely dead -> restart and retry once.
            log.warning("WebDriverException on get(%s): %s -> restarting", url, exc)
            self.restart()
            self.driver.get(url)

        if wait_css:
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
                )
            except TimeoutException:
                log.debug("wait_css '%s' not found on %s", wait_css, url)

        if render_wait > 0:
            time.sleep(render_wait)

        html = self.driver.page_source

        # If we hit a JS interstitial (Cloudflare "Just a moment..."), give the
        # undetected driver time to solve it and re-read the DOM.
        if page_is_challenge(html) and self.cfg.challenge_wait > 0:
            log.info("challenge detected on %s, waiting up to %.0fs", url, self.cfg.challenge_wait)
            deadline = time.time() + self.cfg.challenge_wait
            while time.time() < deadline:
                time.sleep(2)
                html = self.driver.page_source
                if not page_is_challenge(html):
                    log.info("challenge cleared for %s", url)
                    break

        return html

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.quit()
