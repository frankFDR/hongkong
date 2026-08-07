"""database_utils 的单 CSV 导入及查询示例。

本文件只使用 CSV 中现有的英文表名和字段名，不再从 meta 表查询字段翻译映射，
也不执行自动翻译。检测到中文标识符时仅打印警告。
"""

import os
import re
import sys
from pathlib import Path
from pprint import pprint

# 确保能导入同级目录下的模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database_utils import (  # noqa: E402
    engine,
    get_all_data_period,
    get_data,
    import_csv_to_db,
    read_latest_throughput_forecast,
    read_throughput_forecast,
    save_throughput_forecast,
    text,
)


THROUGHPUT_TABLE = "sea_river_import_export_throughput"
THROUGHPUT_CSV = Path("/data5/zhimo/port/database/sea_river_import_export_throughput.csv")

TEXTILE_TABLE = "hongkong_textile_trade_monthly"
TEXTILE_CSV = Path(
    "/data5/zhimo/port/纺织物_0710/data_for_mysql/hongkong_textile_trade_monthly.csv"
)

TEXTILE_CSV_EXAMPLES = (
    Path("/data5/zhimo/port/纺织物_0710/data_for_mysql/hongkong_textile_trade_monthly.csv"),
    Path("/data5/zhimo/port/纺织物_0710/data_for_mysql/mainland_textile_trade_monthly.csv"),
    Path("/data5/zhimo/port/纺织物_0710/data_for_mysql/usa_textile_trade_monthly.csv"),
    Path("/data5/zhimo/port/纺织物_0710/data_for_mysql/vietnam_textile_trade_monthly.csv"),
)


def contains_chinese(value: str) -> bool:
    """判断字符串中是否含有常用 CJK 汉字。"""
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", str(value)))


def warn_chinese_identifiers(csv_path: Path, table_name: str) -> None:
    """发现中文表名或字段名时只警告，不翻译、不查询 meta 映射。"""
    import pandas as pd

    columns = list(pd.read_csv(csv_path, nrows=0, encoding="utf-8-sig").columns)
    chinese_items = [item for item in [table_name, *columns] if contains_chinese(item)]
    if chinese_items:
        print(
            "⚠️ 检测到中文表名或字段名，仅作警告，不会自动翻译："
            + ", ".join(map(str, chinese_items))
        )


def import_one_csv(
    csv_path: Path,
    table_name: str,
    unit: str = "",
    add_auto_id: bool = True,
) -> bool:
    """一次只导入一个 CSV；字段名按 CSV 原样使用，不做自动翻译。"""
    print(f"\n=== 导入单个 CSV：{csv_path.name} -> {table_name} ===")
    if not csv_path.exists():
        print(f"❌ 文件不存在：{csv_path}")
        return False

    warn_chinese_identifiers(csv_path, table_name)
    return import_csv_to_db(
        str(csv_path),
        table_name,
        auto_translate_identifiers=False,
        unit=unit,
        add_auto_id=add_auto_id,
    )


def demo_single_csv_imports() -> bool:
    """演示两个单 CSV 导入；纺织物四个文件中选用香港文件。"""
    print("纺织物目录中可供单独导入的四个文件：")
    for csv_path in TEXTILE_CSV_EXAMPLES:
        print(f"- {csv_path.name}")
    print(f"本次示例选取：{TEXTILE_CSV.name}")

    throughput_ok = import_one_csv(THROUGHPUT_CSV, THROUGHPUT_TABLE, unit="")
    textile_ok = import_one_csv(
        TEXTILE_CSV,
        TEXTILE_TABLE,
        unit="USD",
        add_auto_id=True,
    )
    return throughput_ok and textile_ok


def demo_throughput_query() -> None:
    """查询吞吐量 CSV：单点及一段季度范围。"""
    print("\n=== 查询示例 1：海运/河运进出口吞吐量 ===")
    timestamp = "1997-Q1"
    column = "river_export"
    print(f"单点查询：{THROUGHPUT_TABLE}.{column} @ {timestamp}")
    print(f"结果：{get_data(THROUGHPUT_TABLE, column, timestamp)}")

    print("范围查询：river_export，1997-Q1 ~ 1997-Q4")
    values = get_throughput_values_period(column, "1997-Q1", "1997-Q4")
    pprint(values)


def get_throughput_values_period(column: str, start_timestamp: str, end_timestamp: str):
    """查询指定吞吐量指标在一段季度范围内的数值序列。"""
    allowed_columns = {
        "river_export",
        "river_import",
        "sea_export",
        "sea_import",
    }
    if column not in allowed_columns:
        raise ValueError(f"column 只能是：{', '.join(sorted(allowed_columns))}")
    if start_timestamp > end_timestamp:
        raise ValueError("start_timestamp 不能晚于 end_timestamp")

    results = get_all_data_period(
        [THROUGHPUT_TABLE], [column], start_timestamp, end_timestamp
    )
    # get_all_data_period 对每个字段返回一个列表；本函数只查询一个字段。
    return results[0] if results else []


def get_textile_trade_value(timestamp: str, flow: str):
    """按月份和贸易方向查询香港纺织物贸易额。"""
    normalized_flow = flow.strip().lower()
    if normalized_flow not in {"export", "import"}:
        raise ValueError("flow 只能选择 'export'（出口）或 'import'（进口）")

    query = text(
        f"SELECT `trade_value` FROM `{TEXTILE_TABLE}` "
        "WHERE `timestamp` = :timestamp AND `flow` = :flow LIMIT 1"
    )
    with engine.connect() as connection:
        row = connection.execute(
            query, {"timestamp": timestamp, "flow": normalized_flow}
        ).fetchone()
    return row[0] if row else None


def get_textile_trade_values_period(start_timestamp: str, end_timestamp: str, flow: str):
    """按时间范围和贸易方向查询香港纺织物月度贸易额。"""
    normalized_flow = flow.strip().lower()
    if normalized_flow not in {"export", "import"}:
        raise ValueError("flow 只能选择 'export'（出口）或 'import'（进口）")
    if start_timestamp > end_timestamp:
        raise ValueError("start_timestamp 不能晚于 end_timestamp")

    query = text(
        f"SELECT `timestamp`, `trade_value` FROM `{TEXTILE_TABLE}` "
        "WHERE `timestamp` BETWEEN :start_timestamp AND :end_timestamp "
        "AND `flow` = :flow ORDER BY `timestamp`"
    )
    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "flow": normalized_flow,
            },
        ).fetchall()
    return [{"timestamp": row[0], "trade_value": row[1]} for row in rows]


def demo_textile_query(flow: str = "export") -> None:
    """按 export/import 查询选取的香港纺织物月度贸易 CSV。"""
    print("\n=== 查询示例 2：香港纺织物月度贸易额 ===")
    timestamp = "2012-01-01 00:00:00"
    flow_label = {"export": "出口", "import": "进口"}.get(flow.lower(), flow)
    print(f"当前选择：{flow_label}（{flow}）")
    print(f"单点查询：{TEXTILE_TABLE}.trade_value @ {timestamp}")
    print(f"结果：{get_textile_trade_value(timestamp, flow)}")

    print(f"范围查询：{flow_label}贸易额，2012-01-01 ~ 2012-03-31")
    values = get_textile_trade_values_period(
        "2012-01-01", "2012-03-31 23:59:59", flow
    )
    pprint(values)


def demo_throughput_forecast_storage_and_reading():
    """演示保存和读取吞吐量预测结果。"""
    print("\n=== 保存与读取吞吐量预测结果：throughput_forecast ===")
    print("示例：保存未来 4 个季度的预测结果，然后读取最近一次预测任务")

    forecast_rows = [
        {"timestamp": "2026-Q1", "horizon_step": 1, "river_export_pred": 8200.5,
         "river_import_pred": 9100.2, "sea_export_pred": 14500.8, "sea_import_pred": 23800.6},
        {"timestamp": "2026-Q2", "horizon_step": 2, "river_export_pred": 8350.7,
         "river_import_pred": 9230.4, "sea_export_pred": 14820.5, "sea_import_pred": 24100.3},
        {"timestamp": "2026-Q3", "horizon_step": 3, "river_export_pred": 8500.1,
         "river_import_pred": 9360.8, "sea_export_pred": 15100.2, "sea_import_pred": 24450.9},
        {"timestamp": "2026-Q4", "horizon_step": 4, "river_export_pred": 8660.3,
         "river_import_pred": 9480.6, "sea_export_pred": 15380.7, "sea_import_pred": 24720.4},
    ]

    print(f"保存结果：{save_throughput_forecast(forecast_rows)}")
    columns = [
        "id", "timestamp", "prediction_time", "horizon_step", "river_export_pred",
        "river_import_pred", "sea_export_pred", "sea_import_pred",
    ]

    print("\n--- 最近一次预测任务 ---")
    latest_df = read_latest_throughput_forecast()
    pprint(latest_df[columns].to_dict(orient="records"))

    print("\n--- 目标季度 2026-Q1 的最近 3 条历史预测 ---")
    q1_df = read_throughput_forecast(target_timestamp="2026-Q1", limit=3)
    pprint(q1_df[columns].to_dict(orient="records"))


if __name__ == "__main__":
    if demo_single_csv_imports():
        demo_throughput_query()
        demo_textile_query(flow="export")
        demo_textile_query(flow="import")
        demo_throughput_forecast_storage_and_reading()
    else:
        print("无法进行查询演示，因为至少一个 CSV 导入失败。")
