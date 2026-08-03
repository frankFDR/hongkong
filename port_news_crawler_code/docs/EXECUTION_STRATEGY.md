# Port News Crawler 执行策略详解

## 一、入口与调度

### 1.1 启动入口 (`run.py`)

```python
# 命令行参数解析
python run.py                    # 持续运行模式
python run.py --once             # 单次运行模式
python run.py --site cnn         # 只爬取指定站点（可重复）
python run.py --config other.yaml # 使用自定义配置
```

**执行逻辑**：
1. 解析 `--config` 参数确定配置文件路径（默认 `config.yaml`）
2. 创建 `Crawler` 实例，加载配置
3. 如果指定 `--site`，过滤 `crawler.sites` 列表
4. 根据 `--once` 决定调用 `run_once()` 或 `run_forever()`

### 1.2 调度循环 (`pipeline.py::Crawler.run_forever()`)

```python
def run_forever(self):
    self._install_signal_handlers()  # 注册 SIGINT/SIGTERM 信号处理
    while not self._stop:
        due_sites = [s for s in self.sites if s.due()]
        if not due_sites:
            self._sleep_until_next()
            continue
        for site in due_sites:
            saved = self.crawl_site(site)
            site.schedule_next()  # 设置下次执行时间
```

**调度策略**：
- **轮询检查**：每次循环检查所有站点是否到期
- **到期判断**：`site.due()` 检查 `time.time() >= site.next_run_at`
- **休眠机制**：无站点到期时，休眠到最近站点的下次执行时间（最少5秒，最多60秒）
- **信号处理**：捕获 SIGINT/SIGTERM，优雅关闭浏览器和数据库

### 1.3 单次运行 (`pipeline.py::Crawler.run_once()`)

```python
def run_once(self):
    for site in self.sites:
        saved = self.crawl_site(site)
    self.shutdown()
```

**与持续模式的区别**：不循环，遍历所有站点一轮后退出。

---

## 二、单站点抓取流程

### 2.1 `crawl_site()` 完整流程

```python
def crawl_site(self, site: SiteConfig) -> int:
    # ===== 阶段1：链接发现 =====
    candidate_urls = []
    seen = set()
    for list_url in site.list_urls:
        html = self._fetch_html(list_url, site.wait_css, site.render_wait)
        for u in discover_links(html, list_url, site):
            if u not in seen:
                seen.add(u)
                candidate_urls.append(u)
        self._polite_sleep()  # 列表页之间延迟

    # ===== 阶段2：去重过滤 =====
    new_urls = [u for u in candidate_urls if self.storage.should_fetch(u)]

    # ===== 阶段3：文章爬取 =====
    saved = 0
    for url in new_urls[:self.max_new_per_cycle]:  # 限制数量
        html = self._fetch_html(url, None, site.render_wait)
        
        # 检查是否被拦截
        if page_is_blocked(html) or page_is_challenge(html):
            self.storage.mark(url, site.name, "failed")
            continue
        
        # 内容提取
        art = extract(html, url, site.name, site.language)
        
        # 保存
        if art.is_complete():
            path = self.storage.save_article(art)
            saved += 1
        else:
            self.storage.mark(url, site.name, "failed")
        
        self._polite_sleep()  # 文章页之间延迟

    return saved
```

### 2.2 关键流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    crawl_site(site)                         │
├─────────────────────────────────────────────────────────────┤
│  1. 遍历 site.list_urls (2-3个列表页)                        │
│     │                                                       │
│     ├─→ _fetch_html(list_url, wait_css, render_wait)        │
│     │   - Selenium 加载页面                                   │
│     │   - 等待 wait_css 元素出现 (最多10秒)                    │
│     │   - 额外等待 render_wait 秒 (让JS渲染)                  │
│     │                                                       │
│     └─→ discover_links(html, base_url, site)                │
│         - BeautifulSoup 解析所有 <a> 标签                    │
│         - 正则匹配 article_url_patterns                      │
│         - 排除 exclude_patterns                              │
│         - 域名检查 (同站点)                                   │
├─────────────────────────────────────────────────────────────┤
│  2. 去重过滤                                                 │
│     │                                                       │
│     └─→ storage.should_fetch(url)                            │
│         - 查询 seen_urls 表                                   │
│         - 状态不是 "saved" 且 attempts < 4                    │
├─────────────────────────────────────────────────────────────┤
│  3. 限制数量: new_urls[:max_new_per_cycle] (默认5)            │
├─────────────────────────────────────────────────────────────┤
│  4. 逐个爬取文章                                             │
│     │                                                       │
│     ├─→ _fetch_html(url, None, render_wait)                 │
│     │                                                       │
│     ├─→ 检查拦截                                              │
│     │   - page_is_blocked(html) → "failed"                   │
│     │   - page_is_challenge(html) → "failed"                 │
│     │                                                       │
│     ├─→ extract(html, url, site, language)                   │
│     │   - trafilatura 提取正文                                │
│     │   - BeautifulSoup 提取标题/时间                         │
│     │                                                       │
│     └─→ storage.save_article(art) 或 mark(failed)            │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、浏览器控制与页面获取

### 3.1 浏览器初始化 (`browser.py::Browser._create_driver()`)

```python
def _create_driver(self):
    # 尝试顺序：undetected-chromedriver → 普通 Selenium
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
            return driver
        except Exception as exc:
            log.warning("Failed to start Chrome: %s", exc)
    raise RuntimeError("Could not start Chrome driver")
```

### 3.2 Chrome 启动参数

```python
def _build_options(self, undetected: bool):
    opts = ChromeOptions()
    
    # 反检测配置
    if self.cfg.headless:
        opts.add_argument("--headless=new")  # 新版 headless，更像真实浏览器
    opts.add_argument("--disable-blink-features=AutomationControlled")  # 禁用自动化标志
    opts.add_argument(f"--user-agent={self.cfg.user_agent}")  # 自定义 UA
    
    # 性能配置
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--blink-settings=imagesEnabled=false")  # 禁用图片
    
    # 窗口配置
    opts.add_argument(f"--window-size=1440,900")
    opts.add_argument("--lang=en-US,en;q=0.9,zh-HK;q=0.8")
    
    return opts
```

### 3.3 页面加载 (`browser.py::Browser.get()`)

```python
def get(self, url: str, wait_css: str | None = None,
        render_wait: float = 0.0) -> str:
    driver = self.driver
    
    # 1. 加载页面
    try:
        driver.get(url)
    except TimeoutException:
        log.debug("page load timeout (continuing with partial DOM)")
    except WebDriverException as exc:
        # 驱动崩溃，重启
        self.restart()
        driver.get(url)
    
    # 2. 等待特定 CSS 元素出现
    if wait_css:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
        )
    
    # 3. 额外等待 JS 渲染
    if render_wait > 0:
        time.sleep(render_wait)
    
    # 4. 获取页面源码
    html = driver.page_source
    
    # 5. 检测 Cloudflare 挑战，等待自动解决
    if page_is_challenge(html) and self.cfg.challenge_wait > 0:
        deadline = time.time() + self.cfg.challenge_wait
        while time.time() < deadline:
            time.sleep(2)
            html = driver.page_source
            if not page_is_challenge(html):
                break
    
    return html
```

### 3.4 Cloudflare 检测机制

```python
# 挑战页面特征（Cloudflare 验证）
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies to continue",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
)

# 拦截页面特征（IP/浏览器被封）
BLOCK_MARKERS = (
    "sorry, you have been blocked",
    "attention required! | cloudflare",
    "access denied",
    "error 1020",
    "you don't have permission to access",
)

def page_is_blocked(html: str) -> bool:
    low = (html or "").lower()[:4000]  # 只检查前4000字符
    return any(m in low for m in BLOCK_MARKERS)

def page_is_challenge(html: str) -> bool:
    low = (html or "").lower()[:4000]
    return any(m in low for m in CHALLENGE_MARKERS)
```

---

## 四、链接发现与内容提取

### 4.1 链接发现 (`site.py::discover_links()`)

```python
def discover_links(html: str, base_url: str, site: SiteConfig) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).netloc
    found = []
    seen = set()
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        
        # 1. 跳过无效链接
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        
        # 2. 转换为绝对URL
        absolute = _clean(urljoin(base_url, href))
        
        # 3. 域名检查（同站点）
        host = urlparse(absolute).netloc
        if host and base_host and not _same_site(host, base_host):
            # 如果域名不同，但匹配明确的文章模式，仍然允许
            if not site.is_article_url(absolute):
                continue
        
        # 4. 正则匹配文章URL
        if absolute in seen:
            continue
        if site.is_article_url(absolute):
            seen.add(absolute)
            found.append(absolute)
    
    return found
```

### 4.2 URL 过滤规则 (`site.py::SiteConfig`)

```python
@dataclass
class SiteConfig:
    name: str
    list_urls: list[str]           # 列表页URL
    article_url_patterns: list[str] # 文章URL正则（必须匹配）
    exclude_patterns: list[str]     # 排除URL正则（匹配则跳过）
    
    def is_article_url(self, url: str) -> bool:
        # 排除检查
        if any(rx.search(url) for rx in self._exclude_re):
            return False
        # 包含检查
        return any(rx.search(url) for rx in self._include_re)
```

**示例配置**：
```yaml
# CNN 的配置
- name: cnn
  article_url_patterns:
    - "cnn\\.com/\\d{4}/\\d{2}/\\d{2}/.+/index\\.html"  # 匹配文章URL
  exclude_patterns:
    - "/videos/"      # 排除视频
    - "/live-news/"   # 排除直播
```

### 4.3 内容提取 (`extractor.py::extract()`)

```python
def extract(html: str, url: str, site: str, language: str | None = None) -> Article:
    art = Article(url=url, site=site, language=language)
    
    # ===== 方法1: trafilatura (主要引擎) =====
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            favor_recall=True,  # 偏向召回（提取更多内容）
        )
        if result:
            data = json.loads(result)
            art.text = data.get("text", "")
            art.published = data.get("date")
            art.author = data.get("author")
    except Exception:
        pass
    
    # ===== 方法2: BeautifulSoup 后备 =====
    soup = BeautifulSoup(html, "lxml")
    
    # 标题提取优先级
    title_candidates = [
        jsonld.get("title"),                    # JSON-LD
        _meta_lookup(soup, _TITLE_META_KEYS),   # og:title / twitter:title
        soup.title.get_text(),                  # <title>
        traf_title,                             # trafilatura
        h1.get_text(),                          # <h1>
    ]
    
    # 时间提取
    if not art.published:
        art.published = jsonld.get("published")
        if not art.published:
            art.published = _meta_lookup(soup, _DATE_META_KEYS)
    
    # 正文后备：如果 trafilatura 提取的内容太少
    if len(art.text.strip()) < 120:
        article_node = soup.find("article") or soup.find("main")
        paras = [p.get_text() for p in article_node.find_all("p")]
        art.text = "\n\n".join(p for p in paras if len(p) > 30)
    
    return art
```

### 4.4 内容提取优先级

| 字段 | 优先级 | 来源 |
|------|--------|------|
| **title** | 1 | JSON-LD `headline` |
| | 2 | `<meta property="og:title">` |
| | 3 | `<title>` 标签 |
| | 4 | trafilatura |
| | 5 | `<h1>` 标签 |
| **text** | 1 | trafilatura |
| | 2 | `<article>/<main>` 中的所有 `<p>` 拼接 |
| **published** | 1 | JSON-LD `datePublished` |
| | 2 | `<meta property="article:published_time">` |
| | 3 | `<meta name="publishdate">` |
| | 4 | `<time datetime="...">` |

---

## 五、数据存储与去重

### 5.1 SQLite 数据库结构 (`storage.py`)

```sql
-- 已见URL表（去重核心）
CREATE TABLE seen_urls (
    url_hash   TEXT PRIMARY KEY,  -- URL的SHA1哈希
    url        TEXT NOT NULL,
    site       TEXT NOT NULL,
    status     TEXT NOT NULL,     -- queued|saved|failed
    attempts   INTEGER DEFAULT 0, -- 尝试次数
    updated_at TEXT NOT NULL
);

-- 已保存文章表
CREATE TABLE articles (
    url_hash    TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    site        TEXT NOT NULL,
    title       TEXT,
    published   TEXT,
    author      TEXT,
    language    TEXT,
    fetched_at  TEXT NOT NULL,
    json_path   TEXT,             -- JSON文件路径
    complete    INTEGER DEFAULT 0 -- 是否完整（标题+正文≥120字+时间）
);
```

### 5.2 去重逻辑

```python
def should_fetch(self, url: str, max_attempts: int = 4) -> bool:
    """判断是否需要抓取该URL"""
    h = _url_hash(url)
    row = self._conn.execute(
        "SELECT status, attempts FROM seen_urls WHERE url_hash=?", (h,)
    ).fetchone()
    
    if row is None:
        return True  # 从未见过，需要抓取
    
    status, attempts = row
    if status == "saved":
        return False  # 已保存，跳过
    
    return attempts < max_attempts  # 未达到最大尝试次数
```

### 5.3 状态流转

```
            ┌─────────────┐
            │   (新URL)   │
            └──────┬──────┘
                   │
                   ▼
            ┌─────────────┐
            │   queued    │ ← mark() 首次记录
            └──────┬──────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌──────────────┐      ┌──────────────┐
│    failed    │      │    saved     │
│  (抓取失败)  │      │  (保存成功)  │
└──────┬───────┘      └──────────────┘
       │
       │ attempts < 4
       ▼
┌──────────────┐
│   重试抓取    │
└──────────────┘
```

### 5.4 文件存储结构

```
data/
├── crawler.db                    # SQLite数据库
└── articles/
    ├── cnn/
    │   └── 2026-06-02/
    │       ├── a1b2c3d4e5f6g7h8.json
    │       └── ...
    ├── hket/
    │   └── 2026-06-02/
    │       └── ...
    └── ...
```

### 5.5 JSON 输出格式

```json
{
  "date": "2026-06-02",
  "title": "文章标题",
  "content": "正文内容...",
  "source": "cnn"
}
```

---

## 六、配置与全局设置

### 6.1 全局配置 (`config.yaml::global`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `data_dir` | `"data"` | 数据存储目录 |
| `log_dir` | `"logs"` | 日志目录 |
| `headless` | `true` | 是否无头模式 |
| `use_undetected` | `false` | 是否使用 undetected-chromedriver |
| `page_load_timeout` | `45` | 页面加载超时（秒） |
| `challenge_wait` | `12` | Cloudflare 挑战等待时间（秒） |
| `user_agent` | Chrome 125 UA | 自定义 User-Agent |
| `min_delay_between_pages` | `2.5` | 页面间最小延迟（秒） |
| `max_delay_between_pages` | `6.0` | 页面间最大延迟（秒） |
| `max_new_articles_per_cycle` | `5` | 每周期最大新文章数 |
| `default_poll_interval` | `900` | 默认轮询间隔（秒） |
| `max_retries` | `3` | 单页最大重试次数 |

### 6.2 站点配置 (`config.yaml::sites`)

| 参数 | 说明 |
|------|------|
| `name` | 站点标识（用于文件夹和数据库） |
| `enabled` | 是否启用 |
| `list_urls` | 列表页URL数组 |
| `article_url_patterns` | 文章URL正则数组（必须匹配） |
| `exclude_patterns` | 排除URL正则数组 |
| `poll_interval` | 轮询间隔（秒，覆盖全局默认值） |
| `wait_css` | 等待出现的CSS选择器 |
| `render_wait` | JS渲染等待时间（秒） |
| `language` | 语言标识（信息用途） |

### 6.3 配置示例

```yaml
global:
  headless: true
  use_undetected: false
  max_new_articles_per_cycle: 5
  min_delay_between_pages: 2.5
  max_delay_between_pages: 6.0

sites:
  - name: cnn
    enabled: true
    language: "en"
    list_urls:
      - "https://www.cnn.com/business"
    article_url_patterns:
      - "cnn\\.com/\\d{4}/\\d{2}/\\d{2}/.+/index\\.html"
    exclude_patterns:
      - "/videos/"
      - "/live-news/"
    render_wait: 4
    poll_interval: 900

  - name: hk_marine_dept
    enabled: true
    language: "en"
    list_urls:
      - "https://www.hkmpdb.gov.hk/en/news.html"
    article_url_patterns:
      - "hkmpdb\\.gov\\.hk/en/news/\\d{8}[a-z]?\\.html"
    exclude_patterns: []
    render_wait: 2
    poll_interval: 21600   # 6小时（低频站点）
```

---

## 七、关键问题解答

### Q1: 爬虫是如何启动的？

**A**: `run.py` 是入口，解析命令行参数后创建 `Crawler` 实例，根据 `--once` 参数调用 `run_once()` 或 `run_forever()`。

### Q2: 它如何决定先爬哪个网站？

**A**: 在 `run_forever()` 中，每次循环检查所有站点的 `due()` 方法（判断 `time.time() >= next_run_at`）。到期的站点按配置顺序依次执行，没有优先级排序。

### Q3: 抓取一个网站的具体步骤是什么？

**A**:
1. 遍历 `list_urls`，用 Selenium 加载列表页
2. 用 BeautifulSoup 提取链接，正则过滤出文章URL
3. 查询 SQLite 去重，只保留新URL
4. 限制数量（最多 `max_new_articles_per_cycle`）
5. 逐个用 Selenium 加载文章页
6. 检测 Cloudflare 拦截
7. 用 trafilatura + BeautifulSoup 提取内容
8. 保存 JSON + 记录数据库

### Q4: 它如何避免重复爬取同一篇文章？

**A**: SQLite `seen_urls` 表记录所有已见URL的SHA1哈希。每次抓取前调用 `should_fetch()` 检查，状态为 `saved` 的跳过，`failed` 的最多重试4次。

### Q5: 它如何从网页中提取链接和内容？

**A**:
- **链接**：`discover_links()` 用 BeautifulSoup 解析 `<a>` 标签，正则匹配 `article_url_patterns`
- **内容**：`extract()` 主要用 trafilatura，后备用 BeautifulSoup 从 JSON-LD/meta 标签提取

### Q6: 关键的"策略参数"有哪些，它们分别控制什么？

**A**:
| 参数 | 作用 |
|------|------|
| `poll_interval` | 控制爬取频率（防频繁访问） |
| `max_new_articles_per_cycle` | 控制单次抓取量（防内存溢出） |
| `min/max_delay_between_pages` | 控制请求间隔（防触发反爬） |
| `render_wait` | 控制JS渲染等待（确保内容加载） |
| `challenge_wait` | 控制Cloudflare挑战等待（自动过验证） |
| `max_retries` | 控制重试次数（容错） |

---

## 八、部署与运维

### 8.1 内存优化

使用 `crawl_once.sh` 脚本，每个站点独立进程：

```bash
#!/bin/bash
SITES=(hket manifold_times hk_marine_dept scmp cnn reuters afp people_daily takungpao)

for site in "${SITES[@]}"; do
    python run.py --once --site "$site"
    pkill -f chromedriver 2>/dev/null
    pkill -f "chrome.*--headless" 2>/dev/null
    sleep 5
done
```

### 8.2 Cron 定时任务

```bash
# 每10分钟执行一次
*/10 * * * * /root/port_news_crawler/crawl_once.sh >> /root/port_news_crawler/logs/cron.log 2>&1
```

### 8.3 日志配置

- **日志文件**: `logs/crawler.log`
- **轮转策略**: 5MB/文件，保留5个备份
- **日志格式**: `2026-06-02 11:14:00 | INFO | crawler.pipeline | message`

### 8.4 反爬增强

1. 启用 `use_undetected: true`
2. 配置代理: 修改 `browser.py` 添加 `--proxy-server` 参数
3. 使用住宅代理IP
4. 增大 `min/max_delay_between_pages`
