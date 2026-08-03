# -*- coding: utf-8 -*-
"""下载美国 Census HS 50-63(纺织类)月度进出口总额 -> data/USA_Total.csv。

数据源: U.S. Census Bureau International Trade API (porths 端点),
只取 PORT=- / CTY_CODE=- 的全国汇总行,按 HS2 章 (50-63) 求和。
输出与原 USA_Total.csv 完全同格式(utf-8-sig, 同列名同顺序)。

每月一次请求(单个 flow),响应按章缓存在 cache/usa/ 下;
最近 3 个月总是重新请求,以捕捉 Census 的数据修订。
"""
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

import regional_proxy

API_KEY = "016110633ab0428a7ac7b91943de056c9c665e02"
BASE_URLS = {
    "imports": "https://api.census.gov/data/timeseries/intltrade/imports/porths",
    "exports": "https://api.census.gov/data/timeseries/intltrade/exports/porths",
}
# imports/exports 的商品字段和总值字段名不同
COMMODITY_FIELD = {"imports": "I_COMMODITY", "exports": "E_COMMODITY"}
TOTAL_FIELD = {"imports": "GEN_VAL_MO", "exports": "ALL_VAL_MO"}

CHAPTERS = [f"{c:02d}" for c in range(50, 64)]
START_MONTH = "2015-01"
REFETCH_RECENT_MONTHS = 3

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "usa"
DEFAULT_OUTPUT = HERE / "data" / "USA_Total.csv"

OUTPUT_COLUMNS = ["年月", "年", "月", "进口额", "出口额"]


def month_list(start: str, end: str) -> list[str]:
    months, cur = [], start
    while cur <= end:
        months.append(cur)
        y, m = int(cur[:4]), int(cur[5:])
        cur = f"{y + m // 12:04d}-{m % 12 + 1:02d}"
    return months


def fetch_month(session: requests.Session, flow: str, month: str) -> list[dict]:
    """请求某月某 flow 的全部 HS2 汇总行,过滤出 50-63 章。空数据返回 []。"""
    commodity = COMMODITY_FIELD[flow]
    params = {
        "get": ",".join(["YEAR", "MONTH", commodity,
                         "VES_VAL_MO", "VES_WGT_MO", "CNT_VAL_MO", "CNT_WGT_MO",
                         TOTAL_FIELD[flow]]),
        "time": month,
        "COMM_LVL": "HS2",
        "PORT": "-",
        "CTY_CODE": "-",
        "SUMMARY_LVL": "DET",
        "key": API_KEY,
    }
    last_error = None
    for attempt in range(4):
        try:
            resp = session.get(BASE_URLS[flow], params=params, timeout=60)
            if resp.status_code == 204:
                return []
            if resp.status_code == 200:
                if not resp.text.strip():
                    return []
                raw = resp.json()
                header = raw[0]
                rows = [dict(zip(header, r)) for r in raw[1:]]
                return [r for r in rows if r.get(commodity) in CHAPTERS]
            if "No data" in resp.text or resp.status_code == 404:
                return []
            last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{flow} {month} 请求失败: {last_error}")


def load_month(session: requests.Session, flow: str, month: str, refetch: bool) -> list[dict]:
    cache = CACHE_DIR / flow / f"{month}.json"
    if cache.exists() and not refetch:
        return json.loads(cache.read_text(encoding="utf-8"))
    rows = fetch_month(session, flow, month)
    if rows:  # 空月份不缓存,下次运行继续尝试
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def aggregate(month: str, rows_by_flow: dict[str, list[dict]]) -> dict:
    year, month_number = month.split("-")
    return {
        "年月": f"{year}{month_number}",
        "年": int(year),
        "月": int(month_number),
        "进口额": sum(int(row[TOTAL_FIELD["imports"]] or 0) for row in rows_by_flow["imports"]),
        "出口额": sum(int(row[TOTAL_FIELD["exports"]] or 0) for row in rows_by_flow["exports"]),
    }


def normalize_existing(row: dict[str, str]) -> tuple[str, dict]:
    if "年月" in row:
        period = str(row["年月"]).replace("-", "").zfill(6)
        return f"{period[:4]}-{period[4:]}", {
            "年月": period,
            "年": int(row["年"]),
            "月": int(row["月"]),
            "进口额": int(float(row["进口额"])),
            "出口额": int(float(row["出口额"])),
        }
    period = row["time"]
    return period, {
        "年月": period.replace("-", ""),
        "年": int(row["YEAR"]),
        "月": int(row["MONTH"]),
        "进口额": int(float(row["imports_total_val_mo"])),
        "出口额": int(float(row["exports_total_val_mo"])),
    }


def main(output_path: Path = DEFAULT_OUTPUT, *,
         refresh: str = "auto", existing_path: Path | None = None) -> list[dict]:
    # refresh: auto=用 cache/ 且近期重抓; all=忽略缓存全量重抓;
    #          data=existing_path 指向的既有 CSV 当缓存,已有月份一律不抓,只补缺
    existing: dict[str, dict] = {}
    if refresh == "data" and existing_path and existing_path.exists():
        with existing_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                period, normalized = normalize_existing(row)
                existing[period] = normalized
        print(f"USA: data 模式,沿用 {existing_path} 已有 {len(existing)} 个月")

    today = date.today()
    end_month = f"{today.year:04d}-{today.month:02d}"
    months = month_list(START_MONTH, end_month)
    recent = set(months[-REFETCH_RECENT_MONTHS:])
    to_fetch = [month for month in months if month not in existing]

    session = requests.Session()
    regional_proxy.configure_session(session)
    records = []
    jobs = [(flow, month) for month in to_fetch for flow in ("imports", "exports")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(
            lambda job: load_month(
                session, job[0], job[1],
                refetch=refresh == "all" or job[1] in recent,
            ), jobs
        )
        fetched = {job: rows for job, rows in zip(jobs, results)}
    for month in months:
        if month in existing:
            records.append(existing[month])
            continue
        rows_by_flow = {}
        for flow in ("imports", "exports"):
            rows = fetched[(flow, month)]
            if rows and len(rows) != len(CHAPTERS):
                print(f"  注意: {flow} {month} 只有 {len(rows)}/{len(CHAPTERS)} 章数据")
            rows_by_flow[flow] = rows
        n_imp, n_exp = len(rows_by_flow["imports"]), len(rows_by_flow["exports"])
        if n_imp == 0 and n_exp == 0:
            latest = records[-1]["年月"] if records else "(无)"
            print(f"  USA: {month} 尚无数据,数据截止到 {latest}")
            break
        if n_imp == 0 or n_exp == 0:
            missing = "imports" if n_imp == 0 else "exports"
            print(f"  注意: {month} 缺少 {missing} 数据,该月暂不写入")
            break
        records.append(aggregate(month, rows_by_flow))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    print(f"USA: 写入 {output_path} ({len(records)} 个月, {records[0]['年月']} .. {records[-1]['年月']})")
    return records


if __name__ == "__main__":
    main()
