# example_v5.py
# 演示 database_utils_v4.py 的功能：
#   1. 导入 CSV -> 自动生成 timestamp
#      单个表格导入时可手动输入量纲，留空则写入空字符串
#   2. 检查库名、表名、字段名是否为英文标识符；不是英文时询问是否自动翻译
#   3. 查询元数据 (支持 timestamp 筛选)
#   4. 单点查询 get_data
#   5. 批量查询 get_all_data (支持多个 dataset, 多个 column)
#   6. 范围查询 get_all_data_period (支持 datetime 范围)
#   7. 保存与读取吞吐量预测结果 save_throughput_forecast / read_throughput_forecast，
#      timestamp 统一落库为 DATETIME
#   8. 保存与读取新闻文本 save_news_text / read_news_text_*

import os
import sys
from pprint import pprint
import pandas as pd

# 确保能导入同级目录下的模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database_utils_v4 import *

# 示例配置
TABLE_NAME = "sea_river_import_export_throughput"
ORIGINAL_COL_RIVER_EXPORT = "河运_出口"
ORIGINAL_COL_RIVER_IMPORT = "河运_进口"
ORIGINAL_COL_SEA_EXPORT = "海运_出口"
ORIGINAL_COL_SEA_IMPORT = "海运_进口"
CSV_PATH = "/data5/zhimo/port/database/sea_river_import_export_throughput.csv"


def prompt_unit(default_unit: str = "") -> str:
    """交互输入数据集量纲；直接回车则使用默认空值。"""
    try:
        unit = input("请输入本次导入表格的量纲（可留空，如 TEU、USD、kt）: ").strip()
    except EOFError:
        unit = default_unit
    return unit or default_unit


def setup_data():
    """导入数据作为演示准备"""
    print(f"=== 0. 准备数据: 导入 CSV ({CSV_PATH}) ===")
    print("如果 CSV 字段名不是英文，database_utils_v4 会先询问是否自动翻译。")
    if not os.path.exists(CSV_PATH):
        print(f"❌ 文件不存在: {CSV_PATH}")
        return False

    unit = prompt_unit()

    # 导入 CSV，不指定频率和映射，让其自动推断
    success = import_csv_to_db(CSV_PATH, TABLE_NAME, unit=unit)
    if success:
        pass
    else:
        print(f"❌ 数据导入失败")
    return success


def resolve_column_name(original_column_name: str) -> str:
    """根据原始 CSV 字段名，在元数据中找到实际落库字段名。"""
    for item in get_all_meta_data():
        if (
            item.get("dataset_name") == TABLE_NAME
            and item.get("original_column_name") == original_column_name
        ):
            return item["column_name"]
    raise ValueError(f"未在元数据中找到字段映射: {TABLE_NAME}.{original_column_name}")


def demo_get_all_meta_data():
    """演示获取元数据"""
    print("\n=== 1. 查看表结构信息：get_all_meta_data ===")
    
    # 获取所有元数据
    print("--- 元数据前两行 ---")
    all_meta = get_all_meta_data()
    # 为避免输出过长，只打印前2条
    pprint(all_meta[:2])
    
    # 带时间戳筛选 (演示新功能)
    query_ts = "1997-Q2"
    print(f"\n--- 筛选时间戳为 {query_ts} 的元数据，仅展示前两行结果 ---")
    filtered_meta = get_all_meta_data(timestamp=query_ts)
    pprint(filtered_meta[:2])


def demo_get_data():
    """演示单点查询"""
    print("\n=== 2. 查询单点数据：get_data ===")
    # 查询 1997 年第 1 季的河运出口吞吐量
    ts = "1997-Q1"
    col = resolve_column_name(ORIGINAL_COL_RIVER_EXPORT)
    
    print(f"查询: 表={TABLE_NAME}, 原始列={ORIGINAL_COL_RIVER_EXPORT}, 落库列={col}, 时间={ts}")
    val = get_data(TABLE_NAME, col, ts)
    print(f"结果: {val}")


def demo_get_all_data():
    """演示批量查询 (多个数据集/字段, 同一个时间点)"""
    print("\n=== 3. 批量查询：get_all_data ===")
    ts = "1997-Q2"
    # 这里演示同一个表不同字段，也可以是不同表
    datasets = [TABLE_NAME, TABLE_NAME]
    columns = [
        resolve_column_name(ORIGINAL_COL_RIVER_EXPORT),
        resolve_column_name(ORIGINAL_COL_SEA_EXPORT),
    ]
    
    print(f"查询: 时间={ts}, 组合={list(zip(datasets, columns))}")
    results = get_all_data(datasets, columns, ts)
    print(f"结果: {results}")


def demo_get_all_data_period():
    """演示范围查询"""
    print("\n=== 4. 范围查询：get_all_data_period ===")
    start_ts = "1997-Q1"
    end_ts = "1997-Q4"
    datasets = [TABLE_NAME]
    columns = [resolve_column_name(ORIGINAL_COL_RIVER_EXPORT)]
    
    print(f"查询: 时间范围={start_ts} ~ {end_ts}, 组合={list(zip(datasets, columns))}")
    results = get_all_data_period(datasets, columns, start_ts, end_ts)
    
    # results 是一个 list of lists
    for i, (ds, col) in enumerate(zip(datasets, columns)):
        print(f"数据集: {ds}, 列: {col}")
        print(f"数据序列 ({len(results[i])} 条): {results[i]}")


def demo_throughput_forecast_storage_and_reading():
    """演示保存和读取吞吐量预测结果"""
    print("\n=== 5. 保存与读取吞吐量预测结果：throughput_forecast ===")
    print("示例：一次预测任务保存未来 4 个季度的吞吐量预测结果，然后读取最近一次预测任务")
    print("prediction_time 会由系统当前时间自动生成")

    forecast_rows = [
        {
            "timestamp": "2026-Q1",
            "horizon_step": 1,
            "river_export_pred": 8200.5,
            "river_import_pred": 9100.2,
            "sea_export_pred": 14500.8,
            "sea_import_pred": 23800.6,
        },
        {
            "timestamp": "2026-Q2",
            "horizon_step": 2,
            "river_export_pred": 8350.7,
            "river_import_pred": 9230.4,
            "sea_export_pred": 14820.5,
            "sea_import_pred": 24100.3,
        },
        {
            "timestamp": "2026-Q3",
            "horizon_step": 3,
            "river_export_pred": 8500.1,
            "river_import_pred": 9360.8,
            "sea_export_pred": 15100.2,
            "sea_import_pred": 24450.9,
        },
        {
            "timestamp": "2026-Q4",
            "horizon_step": 4,
            "river_export_pred": 8660.3,
            "river_import_pred": 9480.6,
            "sea_export_pred": 15380.7,
            "sea_import_pred": 24720.4,
        },
    ]

    success = save_throughput_forecast(forecast_rows)
    print(f"保存结果: {success}")

    print("\n--- 读取最近一次预测任务的 4 行结果 ---")
    latest_df = read_latest_throughput_forecast()
    pprint(latest_df[[
        "timestamp",
        "prediction_time",
        "horizon_step",
        "river_export_pred",
        "river_import_pred",
        "sea_export_pred",
        "sea_import_pred",
    ]].to_dict(orient="records"))

    print("\n--- 按目标季度读取历史预测结果：timestamp = 2026-Q1，仅展示最近 3 行 ---")
    q1_df = read_throughput_forecast(target_timestamp="2026-Q1", limit=3)
    pprint(q1_df[[
        "timestamp",
        "prediction_time",
        "horizon_step",
        "river_export_pred",
        "river_import_pred",
        "sea_export_pred",
        "sea_import_pred",
    ]].to_dict(orient="records"))


def demo_news_text_storage_and_reading():
    """演示新闻文本存储和按年/月/季范围读取"""
    print("\n=== 6. 新闻文本存储与读取：news_text ===")
    print("示例：保存新闻、政策、事件文本，并按年/月/季范围读取")
    demo_news_table = "news_text_example_v4"
    print(f"为避免影响正式 news_text 表，本示例写入独立示例表: {demo_news_table}")

    news_rows = [
        {
            "timestamp": "2026-01-15 10:00:00",
            "news_text": "香港港口发布新一季度货运吞吐量相关政策，强调提升跨境物流效率。",
            "source": "example_policy",
        },
        {
            "timestamp": "2026-02-20 09:30:00",
            "news_text": "区域航运公司报告称，春节后出口订单恢复，短期海运需求有所回升。",
            "source": "example_shipping_news",
        },
        {
            "timestamp": "2026-04-08 14:20:00",
            "news_text": "珠三角制造业活动增强，带动河运与海运相关货物流转预期改善。",
            "source": "example_event",
        },
    ]

    prepare_news_text_table(table_name=demo_news_table)
    save_success = save_news_text(news_rows, table_name=demo_news_table)
    print(f"新闻保存结果: {save_success}")

    print("\n--- 按年份读取：2026 年，仅展示前 3 行 ---")
    year_df = read_news_text_by_year_range(2026, table_name=demo_news_table, limit=3)
    pprint(year_df[["timestamp", "source", "news_text"]].to_dict(orient="records"))

    print("\n--- 按月份范围读取：2026-01 到 2026-02 ---")
    month_df = read_news_text_by_month_range(2026, 1, 2026, 2, table_name=demo_news_table)
    pprint(month_df[["timestamp", "source", "news_text"]].to_dict(orient="records"))

    print("\n--- 按季度范围读取：2026-Q1，且只返回文本列表 ---")
    q1_texts = read_news_text_by_quarter_range(2026, "Q1", table_name=demo_news_table, text_only=True)
    pprint(q1_texts)


if __name__ == "__main__":
    if setup_data():
        demo_get_all_meta_data()
        demo_get_data()
        demo_get_all_data()
        demo_get_all_data_period()
        demo_throughput_forecast_storage_and_reading()
        demo_news_text_storage_and_reading()
    else:
        print("无法进行后续演示，因为数据导入失败。")
