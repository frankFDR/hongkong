# -*- coding: utf-8 -*-
"""下载香港按月海运、河运货物吞吐量。

数据源为香港政府统计处统计表 410-55111A、410-55112A 的官方 API。
输出单位沿用官方口径：千公吨。现有商品贸易下载脚本及 Honkong.csv 不受影响。
"""
from datetime import date
from pathlib import Path
import json

import pandas as pd
import requests

import regional_proxy

HOME_URL = "https://www.censtatd.gov.hk/tc/web_table.html?id=410-55111A"
API_URL = "https://www.censtatd.gov.hk/api/post.php"
START_PERIOD = "201501"
TABLES = {
    "410-55111A": {"transport": "S", "prefix": "海运"},
    "410-55112A": {"transport": "J", "prefix": "河运"},
}
HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "data" / "HongKong_Port_Throughput.csv"

# 统计处站点拦截了 requests 默认的 UA，需模拟浏览器
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}


def fetch_table(session, table_id: str, transport: str, end_period: str) -> list[dict]:
    query = {
        "cv": {
            "TRANS": [transport],
            "DIRECTION": ["In", "Out"],
            "SHIPMENT_TYPE": ["DS", "TS"],
        },
        "sv": {"PORT_CARGO_TP": ["Raw_K_tn_n"]},
        "period": {"start": START_PERIOD, "end": end_period},
        "id": table_id,
        "lang": "tc",
    }
    response = session.post(
        API_URL,
        data={"query": json.dumps(query, ensure_ascii=False)},
        headers={"Referer": HOME_URL, "Accept": "application/json"},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    status = payload["header"]["status"]
    if status["code"] != 0:
        raise RuntimeError(f"香港统计处表 {table_id} 请求失败: {status}")
    return payload.get("dataSet", [])


def main(output_path: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    session = requests.Session()
    regional_proxy.configure_session(session)
    session.headers.update(HEADERS)
    session.get(HOME_URL, timeout=60).raise_for_status()
    end_period = date.today().strftime("%Y%m")

    series = []
    for table_id, config in TABLES.items():
        rows = fetch_table(session, table_id, config["transport"], end_period)
        if not rows:
            raise RuntimeError(f"香港统计处表 {table_id} 未返回数据，请检查 API 参数。")
        frame = pd.DataFrame(rows)
        frame = frame[
            (frame["freq"] == "M")
            & (frame["SHIPMENT_TYPE"] == "")
            & (frame["DIRECTION"].isin(["In", "Out"]))
        ].copy()
        frame["列名"] = frame["DIRECTION"].map(
            {"In": f"{config['prefix']}_抵港_千公吨", "Out": f"{config['prefix']}_离港_千公吨"}
        )
        series.append(frame[["period", "列名", "figure"]])

    long = pd.concat(series, ignore_index=True)
    if long.duplicated(["period", "列名"]).any():
        raise ValueError("香港吞吐量 API 返回重复月份/指标。")
    wide = long.pivot(index="period", columns="列名", values="figure").reset_index()
    wide = wide.rename(columns={"period": "年月"}).sort_values("年月").reset_index(drop=True)
    expected = [
        "年月",
        "海运_抵港_千公吨",
        "海运_离港_千公吨",
        "河运_抵港_千公吨",
        "河运_离港_千公吨",
    ]
    wide = wide[expected]
    if wide[expected[1:]].isna().any().any():
        raise ValueError("香港吞吐量数据存在缺失的运输方式或方向。")

    wide.insert(1, "年", wide["年月"].str[:4].astype(int))
    wide.insert(2, "月", wide["年月"].str[4:].astype(int))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"HK Port: 写入 {output_path} ({len(wide)} 个月, 截止 {wide.iloc[-1]['年月']})")
    return wide


if __name__ == "__main__":
    main()
