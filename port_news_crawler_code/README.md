# Port News Crawler

A **continuous, Selenium-based** news collector for 7 shipping / macro news
sources. It runs forever, polling each site on its own schedule, detecting
**newly published** articles and downloading them with the three mandatory
fields guaranteed as completely as possible:

- **题目 / title**
- **正文 / body (full text)**
- **时间戳 / published timestamp**

After extraction, each batch is passed through the shared throughput/textile
prefilters and impact scorers, then inserted into MySQL `news_text`. Only rows
successfully inserted are marked `saved` in the crawler's SQLite state.

## Covered sites

| name             | source                                    | language  |
|------------------|-------------------------------------------|-----------|
| `hket`           | Hong Kong Economic Times (总站 + 航运专题)  | 繁體中文  |
| `manifold_times` | Manifold Times                            | English   |
| `hk_marine_dept` | HK Marine Dept / Maritime & Port Board    | English   |
| `scmp`           | South China Morning Post                  | English   |
| `cnn`            | CNN Business                              | English   |
| `reuters`        | Reuters Business                          | English   |
| `afp`            | AFP News Hub                              | English   |

## How it works

```
list page(s)  --Selenium-->  rendered HTML  --discover_links-->  article URLs
                                                   |
                                          dedup via SQLite (only NEW urls)
                                                   |
article URL  --Selenium-->  rendered HTML  --trafilatura+bs4-->  {title, body, date}
                                                   |
                                   save JSON + record in SQLite
```

- **Browser**: Chrome via `undetected-chromedriver` (falls back to plain
  Selenium). Headless "new" mode, realistic user-agent, anti-automation flags —
  this is what gives a fighting chance against Cloudflare on CNN/Reuters/SCMP.
- **Extraction**: `trafilatura` does the heavy lifting; BeautifulSoup +
  JSON-LD / `<meta>` heuristics fill any gaps for title & timestamp.
- **Dedup / restart-safe**: every seen URL is recorded in `data/crawler.db`
  (SQLite, WAL). Restarting the process never re-downloads old articles.
- **Continuous loop**: each site has a `poll_interval`; the loop wakes sites as
  they come due, so high-frequency sources (HKET, CNN, Reuters) are checked
  more often than low-frequency ones (HK Marine Dept).
- **Politeness**: randomised delay between page fetches; per-cycle cap on new
  articles to avoid bursts.

## Install

```bash
cd port_news_crawler
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
```

Google Chrome must be installed. Selenium Manager / undetected-chromedriver
download the matching driver automatically.

## Run

Set the model API key before starting:

```bash
export DEEPSEEK_API_KEY="your-key"
```

```bash
python run.py                 # run forever (the intended deployment mode)
python run.py --once          # one cycle over all sites (smoke test)
python run.py --site cnn      # only one site (repeatable: --site cnn --site reuters)
python run.py --config config.yaml
```

## Output

```
data/
  crawler.db                       # dedup + metadata index (SQLite)
  articles/
    cnn/2026-06-02/<hash>.json
    hket/2026-06-02/<hash>.json
    ...
logs/
  crawler.log                      # rotating log
```

Each JSON file:

```json
{
  "url": "https://...",
  "site": "cnn",
  "title": "...",
  "text": "full body...",
  "published": "2026-06-02T09:30:00+00:00",
  "author": "...",
  "language": "en",
  "fetched_at": "2026-06-02T11:14:00+00:00",
  "raw_meta": { ... }
}
```

## Adapting / adding sites

Editing `config.yaml` is enough for most cases — no code changes:

- `list_urls`: index/search/section pages to scan for links.
- `article_url_patterns`: regex(es) an article URL must match.
- `exclude_patterns`: regex(es) to drop (categories, tags, authors...).
- `render_wait`: seconds to let JS render before reading the DOM.
- `poll_interval`: seconds between crawls of this site.

Because discovery is generic (collect `<a>` links → filter by regex) and
extraction is generic (trafilatura), an LLM can onboard a brand-new site just
by appending a new block to `config.yaml`.

## Troubleshooting

- **`session not created ... only supports Chrome version N`** — the stealth
  driver version didn't match installed Chrome. The crawler now auto-detects
  the installed Chrome major version (Windows registry / `chrome.exe`) and pins
  `undetected-chromedriver` to it. You can override via `chrome_version` in
  `browser.py` if detection fails.
- **`undetected_chromedriver` download fails (`retrieval incomplete`)** — flaky
  network while fetching the patched driver from GitHub. The crawler falls back
  to plain Selenium automatically, but plain Selenium is easier to block. On a
  stable network the stealth driver downloads once and is cached.
- **Every page returns "Sorry, you have been blocked"** — Cloudflare blocked the
  IP/headless session. The crawler detects these pages and does **not** save
  them as articles (they are marked `failed` and retried later). To get past it:
  set `headless: false` in `config.yaml`, ensure `use_undetected: true` works,
  and/or add a residential proxy.
- **`no such execution context`** — transient driver crash; the browser layer
  catches it, restarts Chrome, and retries the page automatically.

### Validation status

The end-to-end pipeline (discovery → Selenium fetch → extraction → dedup →
JSON save) is verified against the HK Marine Department site (no anti-bot):
title, full body, and timestamp are extracted correctly. The Cloudflare-fronted
sites (Manifold, SCMP, CNN, Reuters, AFP) require the stealth driver + a clean
network/IP on the deployment host; their `article_url_patterns` may need a small
tweak once you can inspect their live HTML from that host.

## Notes on anti-bot / paywalls

CNN, Reuters, SCMP and AFP use aggressive anti-bot and/or paywalls. The
undetected driver handles most public pages, but for hardened deployments
consider: residential/datacenter proxies (add `--proxy-server` in
`browser.py`), authenticated sessions/cookies for paywalled content, and
respecting each site's Terms of Service.
