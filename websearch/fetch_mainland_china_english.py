# -*- coding: utf-8 -*-
"""从海关总署英文站月报抓取 HS50-63 章美元当月值（无验证码/无 JS 风控的兜底来源）。

中文站 gdfs.customs.gov.cn 的统计月报有知道创宇 412 挑战，stats.customs.gov.cn
在线查询平台还要拖滑块验证码；而英文站 english.customs.gov.cn 的 Monthly Bulletin
表（4）「Imports and Exports by HS Section and Division」是同一套海关数据的美元表
（Unit: US$1,000，含当月值与累计值），普通 HTTP 请求即可抓取，且境内外线路均可访问。
注意精度：英文表是千美元舍入的展示版，中文 .xls 才是全精度权威版，因此主流程
（fetch_mainland_china.main / update_all）以中文站优先，本来源只补中文站抓不到的月份。

用法：
    python fetch_mainland_china_english.py                 # 抓当前年（默认今年）
    python fetch_mainland_china_english.py --year 2025     # 抓指定年
    python fetch_mainland_china_english.py --months 4 5    # 只抓指定月份

输出 output/mainlandChina_Import_<year>_english.csv 与 _Export_，
表头与 data/mainlandChina_Import.csv 相同（含历史遗留的“第58章”重复表头），
金额换算为美元（表内千美元 × 1000）。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pandas as pd

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
CACHE_DIR = HERE / "cache" / "mainland_china_english"

BASE = "http://english.customs.gov.cn"
# 2026 年（当前年）的列表页是 monthly.html，往年是 monthly<year>.html
LIST_URL_CURRENT = f"{BASE}/statics/report/monthly.html"
LIST_URL_YEAR = f"{BASE}/statics/report/monthly{{year}}.html"
TABLE4_MARK = "Imports and Exports by HS Section and Division"
MONTH_ABBREVS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CHAPTERS = tuple(range(50, 64))

sys.path.insert(0, str(HERE))
from fetch_mainland_china import OUTPUT_COLUMNS  # 复用遗留 17 列表头


# 不走系统代理：macOS 上 urllib 会自动读系统代理（Clash），经海外出口访问反而 502；
# 英文站境内外线路都能直连。若直连失败再回退系统代理各试一次。
_OPENERS = (
    urllib.request.build_opener(urllib.request.ProxyHandler({})),
    urllib.request.build_opener(),
)


# Clash TUN 环境下系统 DNS 返回 fake-ip、流量被劫持到境外出口后 CDN 502。
# 由 fetch_mainland_china.prepare_english_direct 解析真实 IP 并设置到这里，
# 请求改为「IP 直连 + Host 头」（配合 /24 静态路由走物理网关）。
_DIRECT_IP: str | None = None


def set_direct_ip(ip: str | None) -> None:
    global _DIRECT_IP
    _DIRECT_IP = ip


def _request_variants(url: str) -> list[tuple[str, dict[str, str]]]:
    variants: list[tuple[str, dict[str, str]]] = []
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if _DIRECT_IP and host.endswith("customs.gov.cn") and parsed.scheme == "http":
        direct = urlunparse(parsed._replace(netloc=_DIRECT_IP))
        variants.append((direct, {"Host": host}))
    variants.append((url, {}))
    return variants


def http_get(url: str, timeout: float = 60.0, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        for target, extra_headers in _request_variants(url):
            for opener in _OPENERS:
                try:
                    request = urllib.request.Request(
                        target, headers={"User-Agent": "Mozilla/5.0", **extra_headers})
                    with opener.open(request, timeout=timeout) as response:
                        return response.read()
                except Exception as exc:
                    last = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"下载失败 {url}: {last}")


def discover_table4_links(year: int, current_year: int) -> dict[int, str]:
    """返回 {月份: 表(4)文章 URL}。列表页每行是一张表 × 12 个月的链接格。"""
    url = LIST_URL_CURRENT if year >= current_year else LIST_URL_YEAR.format(year=year)
    html = http_get(url).decode("utf-8", "replace")
    row_match = re.search(
        rf"<td>[^<]*{TABLE4_MARK}[^<]*</td>\s*<td>(.*?)</td>", html, re.S)
    if not row_match:
        raise RuntimeError(f"{url} 上找不到表(4)行")
    result: dict[int, str] = {}
    # 往年页面 <a 与 href 之间可能是换行/多空格，href 值可能带引号也可能裸露
    for href, label in re.findall(r"<a\s+href=([^\s>]+)[^>]*>\s*([A-Za-z]+)\.?", row_match.group(1)):
        month_label = label.strip()[:3]
        if month_label in MONTH_ABBREVS:
            result[MONTH_ABBREVS.index(month_label) + 1] = href.strip('"')
    return result


def month_cache_path(year: int, month: int) -> Path:
    return CACHE_DIR / f"{year}-{month:02d}.html"


def fetch_month_table(url: str, year: int, month: int, refresh: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = month_cache_path(year, month)
    if cache.exists() and not refresh:
        content = cache.read_bytes()
    else:
        content = http_get(url)
        cache.write_bytes(content)
    tables = pd.read_html(io.BytesIO(content))
    return max(tables, key=len)


def fetch_month_values(year: int, month: int, url: str,
                       refresh: bool = False) -> tuple[dict[str, dict[int, int]], Path]:
    """抓取并解析单月表(4)，返回 ({import/export: {章: 美元}}, 缓存 HTML 路径)。

    非强制刷新时优先用缓存；缓存解析失败（残缺/错误页）则重新下载一次。
    """
    frame = fetch_month_table(url, year, month, refresh=refresh)
    try:
        parsed = parse_chapters(frame, month)
    except Exception:
        if refresh:
            raise
        frame = fetch_month_table(url, year, month, refresh=True)
        parsed = parse_chapters(frame, month)
    return parsed, month_cache_path(year, month)


def parse_chapters(frame: pd.DataFrame, month: int) -> dict[str, dict[int, int]]:
    """从表(4)取 HS50-63 的当月出口/进口（千美元→美元）。

    表结构随月份变化：1 月是 5 列（Exports 1 | Imports 1 | %chg×2），
    2 月起是 7 列（Exports 当月|累计 | Imports 当月|累计 | %chg×2），
    因此用「大表头行(Exports/Imports) + 子表头行(月份数字)」联合定位当月列。
    """
    header_row = flow_row = None
    for index in range(min(8, len(frame))):
        cells = [str(v).strip() for v in frame.iloc[index].tolist()]
        if flow_row is None and "Exports" in cells and "Imports" in cells:
            flow_row = index
        if flow_row is not None and any(c == str(month) for c in cells[1:]):
            header_row = index
            break
    if header_row is None or flow_row is None:
        raise RuntimeError("表(4)里找不到当月列表头")
    flows = [str(v).strip() for v in frame.iloc[flow_row].tolist()]
    subs = [str(v).strip() for v in frame.iloc[header_row].tolist()]
    columns: dict[str, int] = {}
    for position, (flow_label, sub) in enumerate(zip(flows, subs)):
        if sub == str(month) and flow_label in ("Exports", "Imports"):
            columns.setdefault(flow_label.lower().rstrip("s"), position)
    if set(columns) != {"export", "import"}:
        raise RuntimeError(f"表(4)当月列定位失败: {columns}")
    result: dict[str, dict[int, int]] = {"export": {}, "import": {}}
    for _, row in frame.iloc[header_row + 1:].iterrows():
        label = str(row.iloc[0]).strip()
        chapter_match = re.match(r"^(\d{2})\s", label)
        if not chapter_match:
            continue
        chapter = int(chapter_match.group(1))
        if chapter not in CHAPTERS:
            continue
        for flow, column in columns.items():
            text = str(row.iloc[column]).replace(",", "").strip()
            value = 0 if text in {"", "-", "nan"} else int(float(text))
            result[flow][chapter] = value * 1000  # US$1,000 → USD
    missing = [c for c in CHAPTERS for f in ("export", "import") if c not in result[f]]
    if missing:
        raise RuntimeError(f"表(4)缺章: {sorted(set(missing))}")
    return result


def write_output(path: Path, rows: list[list]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--months", type=int, nargs="*", help="只抓这些月份（默认抓已发布的全部）")
    parser.add_argument("--current-year", type=int, default=None,
                        help="覆盖“当前年”的判断（当前年的列表页 URL 不带年份）")
    args = parser.parse_args()
    current_year = args.current_year or max(args.year, date.today().year)

    links = discover_table4_links(args.year, current_year)
    months = sorted(set(args.months) & set(links)) if args.months else sorted(links)
    if not months:
        print(f"{args.year} 年没有可抓月份（已发布: {sorted(links)}）")
        return 1
    print(f"{args.year} 年英文站已发布月份: {sorted(links)}；本次抓取: {months}")

    rows = {"import": [], "export": []}
    for month in months:
        frame = fetch_month_table(links[month], args.year, month)
        parsed = parse_chapters(frame, month)
        for flow in ("import", "export"):
            values = [parsed[flow][chapter] for chapter in CHAPTERS]
            rows[flow].append([args.year, month, *values, sum(values)])
        print(f"  {args.year}-{month:02d}: export 总计 {sum(parsed['export'].values()):,} USD, "
              f"import 总计 {sum(parsed['import'].values()):,} USD")

    for flow, name in (("import", "Import"), ("export", "Export")):
        path = OUTPUT_DIR / f"mainlandChina_{name}_{args.year}_english.csv"
        write_output(path, rows[flow])
        print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
