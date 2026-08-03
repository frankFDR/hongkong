# -*- coding: utf-8 -*-
"""下载香港纺织品(SITC 65)月度贸易数据 -> data/Honkong.csv。

数据源: 政府统计处「贸易统计互动数据发布服务」(IDDS) 官方 API
  https://tradeidds.censtatd.gov.hk/api/<api_id>/get
api_id 取自 data.gov.hk 上公开的 API 文档示例,配合首页 cookie 会话即可使用。

原 Honkong.csv 的口径(经与源 CSV 逐值核对确认):
  - 只取 SITC 65(纺织纱、织物、制成品),不含 84(服装);
  - 千港元 * 1000 * 0.13(固定汇率)换算为美元;
  - 五个贸易种类: 进口(1) 港产品出口(2) 转口(3) 整体出口(4) 贸易总额(5)。

响应按 (贸易种类, 年) 缓存在 cache/hongkong/ 下;当年数据总是重新请求。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import json
import time

import pandas as pd
import requests

import regional_proxy

HOME_URL = "https://tradeidds.censtatd.gov.hk/"
API_URL = "https://tradeidds.censtatd.gov.hk/api/df5eaaf5f7fb498397408c731c5823e6/get"

START_YEAR = 2015
SITC_CODES = "65,84"        # 两类都抓下来缓存,输出时只用 65
EXCHANGE_HKD_TO_USD = 0.13  # 与原始整理口径一致的固定汇率
TRADE_TYPES = {1: "进口", 2: "港产品出口", 3: "转口", 4: "整体出口", 5: "贸易总额"}
SOURCE_COLUMNS = ["年", "月", "整体出口", "港产品出口", "贸易总额", "转口", "进口"]
OUTPUT_COLUMNS = ["年月", "年", "月", "进口额", "出口额"]

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache" / "hongkong"
DEFAULT_OUTPUT = HERE / "data" / "Honkong.csv"


class PeriodUndefinedError(Exception):
    """请求区间包含官方尚未发布的月份。"""


def api_get(session: requests.Session, ttype: int, year: int, end_month: int = 12) -> list[dict]:
    """请求某贸易种类某年(至 end_month)的 SITC 65/84 月度货值。"""
    params = {
        "lang": "sc",
        "sv": "VCm",            # 货值(按月)
        "freq": "M",
        "period": f"{year}01,{year}{end_month:02d}",
        "ttype": str(ttype),
        "codeclass": "SITC2",
        "code": SITC_CODES,
    }
    last_error = None
    for attempt in range(4):
        try:
            resp = session.get(API_URL, params=params, timeout=60)
            payload = resp.json()
            status = payload["header"]["status"]
            if status["code"] == 0:
                return payload.get("dataSet", [])
            message = " ".join(status.get("message") or [])
            # 「所选统计时段未被定义」= 该月尚未发布,由调用方回退处理
            if "未被定义" in message or "not defined" in message.lower():
                raise PeriodUndefinedError(message)
            last_error = RuntimeError(f"{status['name']}: {message}")
        except PeriodUndefinedError:
            raise
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"香港 IDDS ttype={ttype} year={year} 请求失败: {last_error}")


def fetch_year(session: requests.Session, ttype: int, year: int, end_month: int) -> list[dict]:
    """请求一年数据;末尾月份尚未发布时逐月回退。"""
    while end_month >= 1:
        try:
            return api_get(session, ttype, year, end_month)
        except PeriodUndefinedError:
            end_month -= 1
    return []


def load_year(session: requests.Session, ttype: int, year: int, end_month: int, refetch: bool) -> list[dict]:
    cache = CACHE_DIR / f"ttype{ttype}_{year}.json"
    if cache.exists() and not refetch:
        return json.loads(cache.read_text(encoding="utf-8"))
    records = fetch_year(session, ttype, year, end_month)
    if records:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return records


def main(output_path: Path = DEFAULT_OUTPUT, *,
         refresh: str = "auto", existing_path: Path | None = None) -> pd.DataFrame:
    # refresh: auto=用 cache/ 且近一两年重抓; all=忽略缓存全量重抓;
    #          data=existing_path 指向的既有 CSV 当缓存,已整年覆盖的年份不抓,只补缺
    existing: dict[tuple[int, int], dict] = {}
    if refresh == "data" and existing_path and existing_path.exists():
        for row in pd.read_csv(existing_path).to_dict("records"):
            year, month = int(row["年"]), int(row["月"])
            existing[(year, month)] = {
                "年月": f"{year:04d}{month:02d}",
                "年": year,
                "月": month,
                "进口额": row.get("进口额", row.get("进口")),
                "出口额": row.get("出口额", row.get("整体出口")),
            }
        print(f"HK: data 模式,沿用 {existing_path} 已有 {len(existing)} 个月")

    today = date.today()
    this_year = today.year
    session = requests.Session()
    regional_proxy.configure_session(session)
    all_records = []
    session.get(HOME_URL, timeout=60)  # 建立 cookie 会话,否则 API 返回 Access denied
    jobs = []
    for year in range(START_YEAR, this_year + 1):
        # data 模式:过去年 12 个月齐全则整年跳过,不发请求
        if year < this_year and all((year, m) in existing for m in range(1, 13)):
            continue
        jobs.extend((ttype, year) for ttype in TRADE_TYPES)
    # IDDS 对并发有限制,3 个线程比较稳妥
    with ThreadPoolExecutor(max_workers=3) as pool:
        for rows in pool.map(
            lambda job: load_year(
                session, job[0], job[1],
                end_month=today.month if job[1] == this_year else 12,
                refetch=refresh == "all" or job[1] >= this_year - 1,
            ), jobs
        ):
            all_records.extend(rows)

    df = pd.DataFrame(all_records)
    df = df[df["code"] == "65"].copy()
    df["年"] = df["period"].str[:4].astype(int)
    df["月"] = df["period"].str[4:].astype(int)
    df["种类"] = df["ttype"].map(TRADE_TYPES)
    df["美元"] = df["figure"].astype(float) * 1000 * EXCHANGE_HKD_TO_USD

    source_wide = df.pivot_table(index=["年", "月"], columns="种类", values="美元").reset_index()
    source_wide = source_wide[SOURCE_COLUMNS].sort_values(["年", "月"]).reset_index(drop=True)
    if source_wide[SOURCE_COLUMNS[2:]].isna().any().any():
        raise ValueError("香港数据存在缺失的贸易种类,请检查缓存/接口返回。")
    wide = source_wide.assign(
        年月=source_wide["年"] * 100 + source_wide["月"],
        进口额=source_wide["进口"],
        出口额=source_wide["整体出口"],
    )[OUTPUT_COLUMNS]

    # data 模式:被跳过的整年没有抓取结果,把既有 CSV 的旧行补回
    if existing:
        fetched = set(zip(wide["年"], wide["月"]))
        leftover = [row for key, row in existing.items() if key not in fetched]
        if leftover:
            wide = pd.concat([wide, pd.DataFrame(leftover)[OUTPUT_COLUMNS]],
                             ignore_index=True)
            wide = wide.sort_values(["年", "月"]).reset_index(drop=True)
    if wide[["进口额", "出口额"]].isna().any().any():
        raise ValueError("香港数据存在缺失的进出口额,请检查缓存/接口返回。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(output_path, index=False, encoding="utf-8-sig")
    last = wide.iloc[-1]
    print(f"HK: 写入 {output_path} ({len(wide)} 个月, 截止 {int(last['年'])}-{int(last['月']):02d})")
    return wide


if __name__ == "__main__":
    main()
