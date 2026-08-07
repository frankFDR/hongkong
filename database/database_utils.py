# 用时间戳
import os
import json
import calendar
import hashlib
import pandas as pd
import re
import sqlalchemy
from sqlalchemy import create_engine, text, inspect, Table, Column, String, Text, DateTime, Integer, MetaData
from typing import List, Dict, Union, Optional, Any
import urllib.parse
from datetime import datetime

# --- 1. 配置与引擎初始化 ---

COMMON_IDENTIFIER_TRANSLATIONS = {
    "年": "year",
    "年份": "year",
    "月": "month",
    "月份": "month",
    "日": "day",
    "日期": "date",
    "季": "quarter",
    "季度": "quarter",
    "货船抵港船次": "cargo_vessel_arrivals",
    "货船净吨位(千吨)": "cargo_vessel_net_tonnage_thousand_tons",
    "客船抵港船次": "passenger_vessel_arrivals",
    "客船净吨位(千吨)": "passenger_vessel_net_tonnage_thousand_tons",
    "总计抵港船次": "total_vessel_arrivals",
    "总计净吨位(千吨)": "total_net_tonnage_thousand_tons",
}


def _slugify_identifier(value: str, fallback_prefix: str = "field") -> str:
    raw = str(value).strip()
    candidate = raw.lower()
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate or not re.search(r"[a-z]", candidate):
        digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
        candidate = f"{fallback_prefix}_{digest}"
    if candidate[0].isdigit():
        candidate = f"{fallback_prefix}_{candidate}"
    return candidate[:64]


def _is_english_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(value).strip()))


def _confirm_auto_translate_identifiers(non_english_items: Dict[str, List[str]]) -> bool:
    print("⚠️ 检测到数据库对象命名中存在非英文标识符：")
    labels = {
        "database": "数据库名",
        "table": "表名",
        "columns": "字段名",
    }
    for key, values in non_english_items.items():
        if not values:
            continue
        print(f"  {labels.get(key, key)}:")
        for value in values[:20]:
            print(f"    - {value}")
        if len(values) > 20:
            print(f"    ... 还有 {len(values) - 20} 个")

    try:
        user_input = input("❓ 是否自动翻译为英文命名后继续导入？(y/N): ").strip().lower()
    except EOFError:
        return False
    return user_input in {"y", "yes"}


def _call_deepseek_identifier_translation(names: List[str]) -> Dict[str, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {}
    try:
        import requests
    except ImportError:
        return {}

    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    prompt = (
        "Translate these database identifiers into concise English snake_case. "
        "Return JSON only, mapping each original string to one identifier. "
        "Use only lowercase letters, digits and underscores.\n"
        f"{json.dumps(names, ensure_ascii=False)}"
    )
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 1200,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"⚠️ DeepSeek 标识符翻译不可用，使用本地兜底命名: {e}")
        return {}


def _make_english_identifier_map(names: List[str], fallback_prefix: str = "field") -> Dict[str, str]:
    unique_names = list(dict.fromkeys(str(name).strip() for name in names))
    deepseek_map = _call_deepseek_identifier_translation(
        [
            name for name in unique_names
            if name not in COMMON_IDENTIFIER_TRANSLATIONS and not _is_english_identifier(name)
        ]
    )
    mapping = {}
    used = set()
    for name in unique_names:
        translated = (
            name if _is_english_identifier(name)
            else COMMON_IDENTIFIER_TRANSLATIONS.get(name) or deepseek_map.get(name) or name
        )
        identifier = _slugify_identifier(translated, fallback_prefix=fallback_prefix)
        base_identifier = identifier
        suffix = 2
        while identifier in used:
            identifier = f"{base_identifier[:58]}_{suffix}"
            suffix += 1
        used.add(identifier)
        mapping[name] = identifier
    return mapping


def _normalize_time_value(value: Union[str, datetime, pd.Timestamp]) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        ts = value
    else:
        text_value = str(value).strip() if value is not None else ""
        quarter_match = re.fullmatch(r"(\d{4})\s*-?\s*[Qq]\s*([1-4])", text_value)
        if quarter_match:
            year = int(quarter_match.group(1))
            quarter = int(quarter_match.group(2))
            ts = pd.Timestamp(year=year, month=(quarter - 1) * 3 + 1, day=1)
        else:
            ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"无效的时间格式: {value}")
    return ts


# 对密码进行转义，防止特殊字符导致连接失败
PASSWORD = urllib.parse.quote_plus("pku")
DB_NAME_ORIGINAL = os.getenv("PORT_DB_NAME", "port")
DB_NAME = _slugify_identifier(DB_NAME_ORIGINAL, fallback_prefix="db")
DB_URL = f"mysql+pymysql://port_user:{PASSWORD}@localhost:3306/{DB_NAME}?charset=utf8mb4"

# 创建全局引擎（内置连接池）
engine = create_engine(
    DB_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    connect_args={"connect_timeout": 60}
)

# 初始化 MetaData 对象用于管理元数据表结构
metadata_obj = MetaData()

# --- 2. 核心功能函数 ---

def prepare_meta_table():
    """
    确保 meta_table_info 表存在
    """
    Table(
        "meta_table_info",
        metadata_obj,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("table_name", String(255), unique=True, nullable=False),
        Column("original_table_name", String(255)),
        Column("source_csv", Text),
        Column("columns_json", Text),       # 存储字段列表或详细元信息
        Column("column_mapping_json", Text), # 原始字段名到英文字段名的映射
        Column("unit", String(255), server_default=""), # 数据集量纲，默认为空
        Column("frequency", String(50)),    # 采样频率：yearly, quarterly, monthly, daily
        Column("start_time", DateTime),     # 数据集起始时间
        Column("end_time", DateTime),       # 数据集结束时间
        Column("row_count", Integer),
        Column("import_time", DateTime),
        extend_existing=True
    )
    metadata_obj.create_all(engine)
    with engine.begin() as conn:
        existing_columns = {
            row._mapping["Field"]
            for row in conn.execute(text("SHOW COLUMNS FROM `meta_table_info`")).fetchall()
        }
        if "original_table_name" not in existing_columns:
            conn.execute(text("ALTER TABLE `meta_table_info` ADD COLUMN `original_table_name` VARCHAR(255)"))
        if "column_mapping_json" not in existing_columns:
            conn.execute(text("ALTER TABLE `meta_table_info` ADD COLUMN `column_mapping_json` TEXT"))
        if "unit" not in existing_columns:
            conn.execute(text("ALTER TABLE `meta_table_info` ADD COLUMN `unit` VARCHAR(255) DEFAULT ''"))

def prepare_throughput_forecast_table():
    """
    确保 throughput_forecast 表存在。

    该表只保存吞吐量预测任务的输出结果；同一个目标季度可以保留不同预测时间下的多次预测。
    """
    create_sql = text("""
        CREATE TABLE IF NOT EXISTS `throughput_forecast` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `timestamp` DATETIME NOT NULL,
            `prediction_time` DATETIME NOT NULL,
            `horizon_step` INT NOT NULL,
            `river_export_pred` DOUBLE,
            `river_import_pred` DOUBLE,
            `sea_export_pred` DOUBLE,
            `sea_import_pred` DOUBLE
        )
    """)
    with engine.begin() as conn:
        conn.execute(create_sql)
        column_rows = conn.execute(
            text("SHOW COLUMNS FROM `throughput_forecast`")
        ).fetchall()
        column_info = {row._mapping["Field"]: row._mapping for row in column_rows}

        required_columns = {
            "id", "timestamp", "prediction_time", "horizon_step",
            "river_export_pred", "river_import_pred",
            "sea_export_pred", "sea_import_pred",
        }
        id_info = column_info.get("id", {})
        timestamp_type = str(column_info.get("timestamp", {}).get("Type", "")).lower()
        schema_is_current = (
            required_columns.issubset(column_info)
            and str(id_info.get("Key", "")).upper() == "PRI"
            and "auto_increment" in str(id_info.get("Extra", "")).lower()
            and "datetime" in timestamp_type
        )

        # 不迁移旧数据：旧表结构不符合当前定义时直接删除并新建。
        if not schema_is_current:
            print("⚠️ throughput_forecast 为旧表结构，将删除旧表并按新结构重建。")
            conn.execute(text("DROP TABLE `throughput_forecast`"))
            conn.execute(create_sql)

def save_throughput_forecast(
    forecasts: Union[pd.DataFrame, List[Dict[str, Any]]],
    expected_horizon: int = 4
) -> bool:
    """
    保存一次吞吐量预测任务的预测结果。

    :param forecasts: 预测结果，需包含 timestamp、horizon_step 以及四个预测值字段；
                      可传入 List[Dict] 或 DataFrame。
    :param expected_horizon: 预测窗口长度，默认 4；会校验 horizon_step 为 1..4。

    示例:
        save_throughput_forecast([
            {
                "timestamp": "2026-01-01",
                "horizon_step": 1,
                "river_export_pred": 8200.5,
                "river_import_pred": 9100.2,
                "sea_export_pred": 14500.8,
                "sea_import_pred": 23800.6,
            },
            ...
        ])
    """
    required_columns = [
        "timestamp",
        "horizon_step",
        "river_export_pred",
        "river_import_pred",
        "sea_export_pred",
        "sea_import_pred",
    ]
    insert_columns = [
        "timestamp",
        "prediction_time",
        "horizon_step",
        "river_export_pred",
        "river_import_pred",
        "sea_export_pred",
        "sea_import_pred",
    ]

    try:
        prepare_throughput_forecast_table()

        if isinstance(forecasts, pd.DataFrame):
            df = forecasts.copy()
        else:
            df = pd.DataFrame(forecasts)

        if df.empty:
            print("⚠️ 预测结果为空，未写入 throughput_forecast")
            return False

        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"⚠️ 预测结果缺少字段: {missing_columns}")
            return False

        if len(df) != expected_horizon:
            print(f"⚠️ 预测结果行数应为 {expected_horizon}，当前为 {len(df)}")
            return False

        df = df[required_columns].copy()
        df["prediction_time"] = pd.Timestamp.now()

        try:
            df["timestamp"] = df["timestamp"].map(_normalize_time_value)
        except ValueError as e:
            print(f"⚠️ {e}")
            return False

        df["horizon_step"] = pd.to_numeric(df["horizon_step"], errors="coerce")
        if df["horizon_step"].isna().any():
            print("⚠️ horizon_step 必须是数字")
            return False
        df["horizon_step"] = df["horizon_step"].astype(int)

        expected_steps = set(range(1, expected_horizon + 1))
        actual_steps = set(df["horizon_step"].tolist())
        if actual_steps != expected_steps:
            print(f"⚠️ horizon_step 应为 {sorted(expected_steps)}，当前为 {sorted(actual_steps)}")
            return False

        pred_columns = [
            "river_export_pred",
            "river_import_pred",
            "sea_export_pred",
            "sea_import_pred",
        ]
        for col in pred_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[insert_columns].sort_values("horizon_step")
        records = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")

        insert_sql = text("""
            INSERT INTO `throughput_forecast`
            (`timestamp`, `prediction_time`, `horizon_step`,
             `river_export_pred`, `river_import_pred`, `sea_export_pred`, `sea_import_pred`)
            VALUES
            (:timestamp, :prediction_time, :horizon_step,
             :river_export_pred, :river_import_pred, :sea_export_pred, :sea_import_pred)
        """)
        with engine.begin() as conn:
            conn.execute(insert_sql, records)

        print(f"✅ 成功写入 throughput_forecast: {len(records)} 行")
        return True

    except Exception as e:
        print(f"❌ 保存吞吐量预测结果失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def read_throughput_forecast(
    target_timestamp: Optional[str] = None,
    prediction_time_start: Optional[Union[str, datetime]] = None,
    prediction_time_end: Optional[Union[str, datetime]] = None,
    limit: Optional[int] = None
) -> pd.DataFrame:
    """
    读取吞吐量预测结果。

    :param target_timestamp: 可选，筛选被预测的目标时间，例如 "2026Q1"、"2026-Q1" 或 "2026-01-01"。
    :param prediction_time_start: 可选，筛选预测任务启动时间下界。
    :param prediction_time_end: 可选，筛选预测任务启动时间上界。
    :param limit: 可选，限制返回行数。
    """
    prepare_throughput_forecast_table()

    conditions = []
    params = {}

    if target_timestamp is not None:
        conditions.append("`timestamp` = :target_timestamp")
        params["target_timestamp"] = _normalize_time_value(target_timestamp)

    if prediction_time_start is not None:
        start = pd.to_datetime(prediction_time_start)
        if pd.isna(start):
            raise ValueError(f"无效的 prediction_time_start: {prediction_time_start}")
        conditions.append("`prediction_time` >= :prediction_time_start")
        params["prediction_time_start"] = start

    if prediction_time_end is not None:
        end = pd.to_datetime(prediction_time_end)
        if pd.isna(end):
            raise ValueError(f"无效的 prediction_time_end: {prediction_time_end}")
        conditions.append("`prediction_time` <= :prediction_time_end")
        params["prediction_time_end"] = end

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = ""
    if limit is not None:
        if int(limit) <= 0:
            raise ValueError("limit 必须为正整数")
        params["limit"] = int(limit)
        limit_clause = "LIMIT :limit"

    query = text(f"""
        SELECT
            `id`, `timestamp`, `prediction_time`, `horizon_step`,
            `river_export_pred`, `river_import_pred`,
            `sea_export_pred`, `sea_import_pred`
        FROM `throughput_forecast`
        {where_clause}
        ORDER BY `prediction_time` DESC, `horizon_step` ASC, `id` ASC
        {limit_clause}
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params=params)

def read_latest_throughput_forecast() -> pd.DataFrame:
    """
    读取最近一次预测任务的全部预测结果。

    通常返回同一个 prediction_time 下的 4 行记录，按 horizon_step 升序排列。
    """
    prepare_throughput_forecast_table()
    query = text("""
        SELECT
            `id`, `timestamp`, `prediction_time`, `horizon_step`,
            `river_export_pred`, `river_import_pred`,
            `sea_export_pred`, `sea_import_pred`
        FROM `throughput_forecast`
        WHERE `prediction_time` = (
            SELECT MAX(`prediction_time`) FROM `throughput_forecast`
        )
        ORDER BY `horizon_step` ASC, `id` ASC
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)

def _safe_table_name(table_name: str) -> str:
    """
    校验表名，避免动态 SQL 中出现非法字符；表名必须是英文标识符。
    """
    if not table_name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(table_name)):
        raise ValueError(f"非法表名: {table_name}")
    return str(table_name).lower()


def _safe_column_name(column_name: str) -> str:
    """
    校验字段名；字段名必须是英文标识符。
    """
    if not column_name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(column_name)):
        raise ValueError(f"非法字段名: {column_name}")
    return str(column_name).lower()

def prepare_news_text_table(table_name: str = "news_text"):
    """
    确保新闻文本表存在。

    news_text 用于存储新闻、政策、事件等文本数据，每一行对应一条文本记录。
    """
    table_name = _safe_table_name(table_name)
    create_sql = text(f"""
        CREATE TABLE IF NOT EXISTS `{table_name}` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `timestamp` DATETIME,
            `news_text` TEXT,
            `source` VARCHAR(255)
        )
    """)
    with engine.begin() as conn:
        conn.execute(create_sql)

        existing_columns = {
            row._mapping["Field"]
            for row in conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`")).fetchall()
        }
        if "source" not in existing_columns:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `source` VARCHAR(255)"))

def save_news_text(
    news_records: Union[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any]],
    source: Optional[str] = None,
    table_name: str = "news_text"
) -> bool:
    """
    保存新闻、政策、事件等文本数据。

    :param news_records: 可传入 Dict、List[Dict] 或 DataFrame。
                         必须包含 timestamp 和 news_text；source 可选。
    :param source: 如果记录中没有 source，则使用该默认来源。
    :param table_name: 默认写入 news_text。
    """
    table_name = _safe_table_name(table_name)
    try:
        prepare_news_text_table(table_name)

        if isinstance(news_records, pd.DataFrame):
            df = news_records.copy()
        elif isinstance(news_records, dict):
            df = pd.DataFrame([news_records])
        else:
            df = pd.DataFrame(news_records)

        if df.empty:
            print("⚠️ 新闻文本为空，未写入 news_text")
            return False

        required_columns = ["timestamp", "news_text"]
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"⚠️ 新闻文本缺少字段: {missing_columns}")
            return False

        df = df.copy()
        if "source" not in df.columns:
            df["source"] = source
        elif source is not None:
            df["source"] = df["source"].fillna(source)

        df = df[["timestamp", "news_text", "source"]]
        df["timestamp"] = df["timestamp"].map(
            lambda value: _normalize_time_value(value) if pd.notna(value) else pd.NaT
        )
        df["news_text"] = df["news_text"].where(pd.notnull(df["news_text"]), "").astype(str).str.strip()
        df["source"] = df["source"].where(pd.notnull(df["source"]), None)

        valid_mask = df["timestamp"].notna() & (df["news_text"] != "")
        invalid_count = len(df) - int(valid_mask.sum())
        df = df.loc[valid_mask].copy()

        if df.empty:
            print("⚠️ 没有有效新闻文本可写入")
            return False

        records = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
        insert_sql = text(f"""
            INSERT INTO `{table_name}` (`timestamp`, `news_text`, `source`)
            VALUES (:timestamp, :news_text, :source)
        """)
        with engine.begin() as conn:
            conn.execute(insert_sql, records)

        print(f"✅ 成功写入 `{table_name}`: {len(records)} 条新闻")
        if invalid_count:
            print(f"   跳过无效记录: {invalid_count} 条")
        return True

    except Exception as e:
        print(f"❌ 保存新闻文本失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def read_news_text_period(
    timestamp_start: Union[str, datetime],
    timestamp_end: Union[str, datetime],
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False
) -> Union[pd.DataFrame, List[str]]:
    """
    读取一个时间区间内的新闻文本，闭区间 [timestamp_start, timestamp_end]。
    """
    table_name = _safe_table_name(table_name)
    prepare_news_text_table(table_name)

    start = _normalize_time_value(timestamp_start)
    end = _normalize_time_value(timestamp_end)
    if start > end:
        raise ValueError("timestamp_start 不能晚于 timestamp_end")

    params = {"start": start, "end": end}
    limit_clause = ""
    if limit is not None:
        if int(limit) <= 0:
            raise ValueError("limit 必须为正整数")
        params["limit"] = int(limit)
        limit_clause = "LIMIT :limit"

    query = text(f"""
        SELECT `id`, `timestamp`, `news_text`, `source`
        FROM `{table_name}`
        WHERE `timestamp` BETWEEN :start AND :end
        ORDER BY `timestamp` ASC, `id` ASC
        {limit_clause}
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params=params)

    if text_only:
        return df["news_text"].tolist()
    return df

def read_news_text_by_year_range(
    start_year: int,
    end_year: Optional[int] = None,
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False
) -> Union[pd.DataFrame, List[str]]:
    """
    按年份范围读取新闻；如果 end_year 为空，则读取单年。
    """
    start_year = int(start_year)
    end_year = int(end_year) if end_year is not None else start_year
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31, 23, 59, 59)
    return read_news_text_period(start, end, table_name=table_name, limit=limit, text_only=text_only)

def read_news_text_by_month_range(
    start_year: int,
    start_month: int,
    end_year: Optional[int] = None,
    end_month: Optional[int] = None,
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False
) -> Union[pd.DataFrame, List[str]]:
    """
    按月份范围读取新闻；如果结束年月为空，则读取单月。
    """
    start_year = int(start_year)
    start_month = int(start_month)
    end_year = int(end_year) if end_year is not None else start_year
    end_month = int(end_month) if end_month is not None else start_month

    if start_month < 1 or start_month > 12 or end_month < 1 or end_month > 12:
        raise ValueError("month 必须在 1 到 12 之间")

    last_day = calendar.monthrange(end_year, end_month)[1]
    start = datetime(start_year, start_month, 1)
    end = datetime(end_year, end_month, last_day, 23, 59, 59)
    return read_news_text_period(start, end, table_name=table_name, limit=limit, text_only=text_only)

def read_news_text_by_quarter_range(
    start_year: int,
    start_quarter: Union[int, str],
    end_year: Optional[int] = None,
    end_quarter: Optional[Union[int, str]] = None,
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False
) -> Union[pd.DataFrame, List[str]]:
    """
    按季度范围读取新闻；如果结束年季为空，则读取单季度。
    """
    def normalize_quarter(quarter: Union[int, str]) -> int:
        if isinstance(quarter, str):
            quarter = quarter.upper().replace("Q", "")
        quarter = int(quarter)
        if quarter not in {1, 2, 3, 4}:
            raise ValueError("quarter 必须是 1, 2, 3, 4 或 'Q1'...'Q4'")
        return quarter

    start_year = int(start_year)
    start_quarter = normalize_quarter(start_quarter)
    end_year = int(end_year) if end_year is not None else start_year
    end_quarter = normalize_quarter(end_quarter) if end_quarter is not None else start_quarter

    start_month = (start_quarter - 1) * 3 + 1
    end_month = (end_quarter - 1) * 3 + 3
    last_day = calendar.monthrange(end_year, end_month)[1]
    start = datetime(start_year, start_month, 1)
    end = datetime(end_year, end_month, last_day, 23, 59, 59)
    return read_news_text_period(start, end, table_name=table_name, limit=limit, text_only=text_only)

def _generate_timestamp(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.Series:
    """
    根据映射关系生成时间戳列
    Supported keys in mapping: 'year', 'month', 'day', 'quarter', 'date'
    """
    try:
        if 'date' in mapping:
            return pd.to_datetime(df[mapping['date']], errors='coerce')
        
        # 构造日期字典
        dates = pd.DataFrame()
        
        # 处理年份
        if 'year' in mapping:
            # 1. 尝试直接转换为数字
            y = pd.to_numeric(df[mapping['year']], errors='coerce')
            
            # 2. 如果存在非数字字符串（例如 "2020年"），尝试提取数字
            # 这里的 mask 筛选出：原本不是 NaN，但转数字失败变成 NaN 的行
            mask = y.isna() & df[mapping['year']].notna()
            if mask.any():
                # 使用正则提取数字部分
                extracted = df.loc[mask, mapping['year']].astype(str).str.extract(r'(\d+)', expand=False)
                y.loc[mask] = pd.to_numeric(extracted, errors='coerce')
            
            # 3. 仍为NaN的（原本就是空，或者提取不出数字），填充为1 (0001年，防止报错)
            dates['year'] = y.fillna(1).astype(int)
        else:
            raise ValueError("Time mapping must include 'year' or 'date'")
            
        # 处理月份/季度
        if 'month' in mapping:
             # 同样增加提取逻辑
            m = pd.to_numeric(df[mapping['month']], errors='coerce')
            mask = m.isna() & df[mapping['month']].notna()
            if mask.any():
                extracted = df.loc[mask, mapping['month']].astype(str).str.extract(r'(\d+)', expand=False)
                m.loc[mask] = pd.to_numeric(extracted, errors='coerce')
                
            dates['month'] = m.fillna(1).astype(int)
            
        elif 'quarter' in mapping:
            # 季度转月份：Q1->1, Q2->4, Q3->7, Q4->10 (使用季度初作为时间戳)
            q = pd.to_numeric(df[mapping['quarter']], errors='coerce')
            
            # 如果存在非数字字符串（例如 "Q1", "第1季"），尝试提取数字
            mask = q.isna() & df[mapping['quarter']].notna()
            if mask.any():
                 extracted = df.loc[mask, mapping['quarter']].astype(str).str.extract(r'(\d+)', expand=False)
                 q.loc[mask] = pd.to_numeric(extracted, errors='coerce')
            
            # 缺失或异常季度默认为1
            q = q.fillna(1)
            
            dates['month'] = ((q - 1) * 3 + 1).astype(int)
        else:
            dates['month'] = 1 # 默认为1月
            
        # 处理日期
        if 'day' in mapping:
            dates['day'] = pd.to_numeric(df[mapping['day']], errors='coerce').fillna(1).astype(int)
        else:
            dates['day'] = 1 # 默认为1号
            
        # 再次确保年份也是处理过的（虽然上面处理了，但可能是float类型含NaN）
        # pd.to_datetime 组装时如果 year 含 NaN 会生成 NaT
        return pd.to_datetime(dates, errors='coerce')
    except Exception as e:
        raise ValueError(f"时间戳生成失败: {e}")

def import_csv_to_db(
    csv_file: str,
    table_name: str,
    frequency: str = None,
    time_mapping: Dict[str, str] = None,
    auto_translate_identifiers: Optional[bool] = None,
    unit: str = "",
    add_auto_id: bool = False,
) -> bool:
    """
    导入CSV到数据库，并标准化时间戳
    :param csv_file: CSV文件路径
    :param table_name: 目标表名
    :param frequency: (可选) 采样频率 ('yearly', 'quarterly', 'monthly', 'daily')。如果为空，将自动推断。
    :param time_mapping: (可选) 时间字段映射。如果不传，将自动尝试匹配 {'year': '年', 'month': '月', 'day': '日'} 等常见列名。
    :param auto_translate_identifiers: (可选) 非英文库名/表名/字段名处理方式。
                                       None 表示交互询问；True 表示自动翻译；False 表示直接退出。
    :param unit: (可选) 数据集量纲，默认空字符串。可在导入时手动传入，如 "TEU"、"USD"。
    :param add_auto_id: (可选) 是否在数据表首列增加自增主键 id，默认 False。
    """
    try:
        # 0. 初始化
        abs_csv_path = os.path.abspath(csv_file)
        original_table_name = str(table_name)
        unit = "" if unit is None else str(unit).strip()
        
        # 1. 读取CSV
        df = pd.read_csv(csv_file, encoding='utf-8', low_memory=False)
        if df.empty:
            print("⚠️ CSV文件为空")
            return False

        original_columns = [str(col).strip() for col in df.columns]
        non_english_items = {
            "database": [] if _is_english_identifier(DB_NAME_ORIGINAL) else [DB_NAME_ORIGINAL],
            "table": [] if _is_english_identifier(original_table_name) else [original_table_name],
            "columns": [col for col in original_columns if not _is_english_identifier(col)],
        }
        needs_translation = any(non_english_items.values())
        if needs_translation:
            if auto_translate_identifiers is None:
                should_translate = _confirm_auto_translate_identifiers(non_english_items)
            else:
                should_translate = bool(auto_translate_identifiers)

            if not should_translate:
                print("⏹️ 操作已取消：存在非英文命名，且未选择自动翻译。")
                return False
            if auto_translate_identifiers is True:
                print("ℹ️ 检测到非英文命名，已按参数设置自动翻译为英文标识符。")

        if needs_translation:
            table_name = _make_english_identifier_map([table_name], fallback_prefix="table")[original_table_name]
            column_mapping = _make_english_identifier_map(original_columns, fallback_prefix="col")
        else:
            table_name = original_table_name.lower()
            column_mapping = {col: col.lower() for col in original_columns}
        prepare_meta_table()
            
        # 2. 清洗列名。先保留原始语义，生成 timestamp 后再统一翻译成英文标识符。
        clean_to_original = {
            str(col).strip().replace(' ', '_').replace('-', '_'): str(col).strip()
            for col in df.columns
        }
        df.columns = list(clean_to_original.keys())
        
        # 3. 生成标准时间戳字段
        
        # 如果未提供映射，尝试自动推断
        if not time_mapping:
            potential_map = {
                'year': ['年', '年份', 'Year', 'year', 'YEAR'],
                'month': ['月', '月份', 'Month', 'month', 'MONTH'],
                'day': ['日', '日期', 'Day', 'day', 'DAY'],
                'quarter': ['季', '季度', 'Quarter', 'quarter'],
                'date': [
                    'timestamp', 'Timestamp', 'TIMESTAMP',
                    'datetime', 'Datetime', 'DATETIME',
                    'date_time', 'DateTime',
                    '日期时间', '时间戳', '日期', '时间',
                    'Date', 'date', 'DATE', 'Time', 'time', 'TIME'
                ]
            }
            time_mapping = {}
            for key, candidates in potential_map.items():
                for c in candidates:
                    # 匹配清洗后的列名
                    clean_c = c.strip().replace(' ', '_').replace('-', '_')
                    if clean_c in df.columns:
                        time_mapping[key] = clean_c
                        break
            
            if not time_mapping:
                 print("⚠️ 无法自动识别时间列，请手动指定 time_mapping")
                 return False
            print(f"ℹ️ 自动匹配时间列: {time_mapping}")

        # 更新映射中的列名以匹配清洗后的列名
        clean_mapping = {}
        for k, v in time_mapping.items():
            clean_v = v.strip().replace(' ', '_').replace('-', '_')
            if clean_v in df.columns:
                clean_mapping[k] = clean_v
            else:
                print(f"⚠️ 警告: 映射列 {v} (cleaned: {clean_v}) 不在CSV列中: {df.columns.tolist()}")
                return False
                
        df['timestamp'] = _generate_timestamp(df, clean_mapping)
        if df['timestamp'].isnull().any():
            print("⚠️ 警告: 存在无效的时间戳（如日期不存在），已默认设为 1900-01-01")
            df['timestamp'] = df['timestamp'].fillna(pd.Timestamp("1900-01-01"))

        rename_mapping = {
            clean_col: column_mapping[original_col]
            for clean_col, original_col in clean_to_original.items()
        }
        df = df.rename(columns=rename_mapping)
        if add_auto_id and "id" in df.columns:
            raise ValueError("启用 add_auto_id 时，CSV 不能已包含 id 字段")
        # CSV 原本就以 timestamp 作为时间列时，生成标准时间戳会原位覆盖该列，
        # 这是合法情况；其他字段翻译后占用 timestamp 仍按原逻辑报错。
        source_date_column = clean_mapping.get("date")
        source_date_is_timestamp = bool(
            source_date_column
            and rename_mapping.get(source_date_column) == "timestamp"
        )
        if "timestamp" in df.columns[:-1] and not source_date_is_timestamp:
            raise ValueError("CSV 字段翻译后不能与系统字段 timestamp 重名")
            
        # 4. 检查表是否存在（防止误覆盖）
        ins = inspect(engine)
        if ins.has_table(table_name):
            print(f"⚠️ 表 `{table_name}` 已存在。")
            user_input = input(f"❓ 是否覆盖导入并更新元数据？(y/N): ").strip().lower()
            if user_input not in ['y', 'yes']:
                print(f"⏹️ 操作已取消: {table_name}")
                return False

        # 5. 获取时间范围
        start_time = df['timestamp'].min()
        end_time = df['timestamp'].max()
        
        # 如果未指定频率，自动推断
        if not frequency:
            freq_result = 'unknown'
            try:
                # 获取排序后的唯一时间戳
                unique_dates = df['timestamp'].dropna().drop_duplicates().sort_values()
                
                if len(unique_dates) > 1:
                    # 计算最小时间间隔
                    min_diff = unique_dates.diff().min()
                    days = min_diff.days
                    
                    if days >= 360:
                        freq_result = 'yearly'
                    elif days >= 88:
                        freq_result = 'quarterly'
                    elif days >= 28:
                        freq_result = 'monthly'
                    elif days >= 1:
                        freq_result = 'daily'
                    else:
                        # 小于1天的情况，可以根据需要扩展
                        freq_result = 'daily' 
                else:
                    freq_result = 'unknown'
            except Exception as e:
                print(f"⚠️ 自动推断频率失败: {e}")
                freq_result = 'unknown'
                
            frequency = freq_result
            print(f"ℹ️ 自动推断采样频率: {frequency}")

        # 5. 写入数据库
        with engine.begin() as conn:
            # A. 写入数据表 (包含新的 timestamp 列)
            df.to_sql(
                name=table_name,
                con=conn,
                if_exists='replace',
                index=False,
                chunksize=5000,
                method='multi',
                dtype={'timestamp': sqlalchemy.DateTime}
            )
            if add_auto_id:
                conn.execute(text(
                    f"ALTER TABLE `{table_name}` "
                    "ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
                ))
            # 为 timestamp 创建索引以优化查询
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD INDEX idx_timestamp (`timestamp`)"))
            
            # B. 更新元数据
            conn.execute(
                text("DELETE FROM `meta_table_info` WHERE `table_name` = :t"),
                {"t": table_name}
            )
            
            column_list = df.columns.tolist()
            conn.execute(
                text("""
                    INSERT INTO `meta_table_info` 
                    (table_name, original_table_name, source_csv, columns_json, column_mapping_json,
                     unit, frequency, start_time, end_time, row_count, import_time)
                    VALUES 
                    (:table_name, :original_table_name, :source_csv, :columns_json, :column_mapping_json,
                     :unit, :frequency, :start_time, :end_time, :row_count, :import_time)
                """),
                {
                    'table_name': table_name,
                    'original_table_name': original_table_name,
                    'source_csv': abs_csv_path,
                    'columns_json': json.dumps(column_list, ensure_ascii=False),
                    'column_mapping_json': json.dumps(column_mapping, ensure_ascii=False),
                    'unit': unit,
                    'frequency': frequency,
                    'start_time': start_time,
                    'end_time': end_time,
                    'row_count': len(df),
                    'import_time': pd.Timestamp.now()
                }
            )
            
        print(f"✅ 成功导入 `{table_name}`")
        print(f"   时间范围: {start_time} - {end_time}, 频率: {frequency}")
        if unit:
            print(f"   量纲: {unit}")
        return True
        
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

# --- 3. 查询接口 ---

def get_all_meta_data(timestamp: Optional[Union[str, datetime]] = None):
    """
    返回我们全部“数据集-字段”的元信息，包括源数据集，起止时间，采样频率，量纲
    :param timestamp: (可选) 如果提供，仅返回包含该时间戳的数据集
    """
    try:
        # 处理时间戳筛选
        query_ts = None
        if timestamp:
            try:
                query_ts = _normalize_time_value(timestamp)
            except Exception as e:
                print(f"⚠️ 无效的时间戳格式: {timestamp}, 错误: {e}")
                return []

        with engine.connect() as conn:
            # 获取所有表的元数据记录
            result = conn.execute(text("SELECT * FROM meta_table_info"))
            meta_rows = [dict(row._mapping) for row in result]
            
        all_meta = []
        for row in meta_rows:
            # 如果指定了时间戳，检查是否在范围内
            if query_ts:
                start = row['start_time']
                end = row['end_time']
                # 处理起止时间为空的情况（虽然理论上都有但为了健壮性）
                if pd.isna(start) or pd.isna(end):
                    continue
                if not (start <= query_ts <= end):
                    continue

            table_name = row['table_name']
            columns = json.loads(row['columns_json'])
            column_mapping = json.loads(row.get("column_mapping_json") or "{}")
            reverse_mapping = {v: k for k, v in column_mapping.items()}
            
            for col in columns:
                if col == 'timestamp': continue # 跳过系统生成的时间戳列
                
                all_meta.append({
                    'dataset_name': table_name,
                    'column_name': col,
                    'original_column_name': reverse_mapping.get(col),
                    'original_dataset_name': row.get('original_table_name'),
                    'source_csv': row['source_csv'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'frequency': row['frequency'],
                    'unit': row.get('unit') or ''
                })
        return all_meta
    except Exception as e:
        print(f"获取元数据失败: {e}")
        return []

def get_data(dataset_name: str, column_name: str, timestamp):
    """
    返回某一个时间戳下，某一个数据集某一字段的取值
    """
    try:
        # 确保 timestamp 格式正确
        dataset_name = _safe_table_name(dataset_name)
        column_name = _safe_column_name(column_name)
        ts = _normalize_time_value(timestamp)
        
        query = text(f"SELECT `{column_name}` FROM `{dataset_name}` WHERE `timestamp` = :ts LIMIT 1")
        with engine.connect() as conn:
            result = conn.execute(query, {"ts": ts}).fetchone()
            
        return result[0] if result else None
    except Exception as e:
        print(f"查询数据失败 [{dataset_name}.{column_name} @ {timestamp}]: {e}")
        return None

def get_all_data(dataset_names: List[str], column_names: List[str], timestamp):
    """
    返回某一个时间戳下，一系列“数据集-字段“的取值。
    校验采样频率是否一致。
    """
    if len(dataset_names) != len(column_names):
        raise ValueError("dataset_names 和 column_names 长度必须一致")
        
    ts = _normalize_time_value(timestamp)
    dataset_names = [_safe_table_name(name) for name in dataset_names]
    column_names = [_safe_column_name(name) for name in column_names]
    unique_datasets = list(set(dataset_names))
    
    # 1. 校验采样频率
    try:
        with engine.connect() as conn:
            # 批量获取涉及到的数据集的频率
            if not unique_datasets:
                return []
                
            placeholders = ','.join([f":d{i}" for i in range(len(unique_datasets))])
            params = {f"d{i}": name for i, name in enumerate(unique_datasets)}
            
            sql = text(f"SELECT table_name, frequency, start_time, end_time FROM meta_table_info WHERE table_name IN ({placeholders})")
            meta_res = conn.execute(sql, params).fetchall()
            
            meta_dict = {row.table_name: row for row in meta_res}
            
            # 检查频率一致性
            first_freq = None
            for name in unique_datasets:
                if name not in meta_dict:
                    raise ValueError(f"数据集 `{name}` 不存在于元数据表中")
                
                freq = meta_dict[name].frequency
                if first_freq is None:
                    first_freq = freq
                elif freq != first_freq:
                    raise ValueError(f"采样频率不一致: 检测到 {first_freq} 和 {freq}")
                
                # 检查时间戳是否在范围内 (可选，依据需求 "如果数据集的采样频率和timestamp不一样也可以报错")
                # 这里简单检查时间是否在范围内，具体如果是 Monthly 数据查了一个不合法的日期，依靠数据库没查到返回 None
                if not (meta_dict[name].start_time <= ts <= meta_dict[name].end_time):
                    # 也可以选择此时报错，或者返回 None。这里根据 "不一样也可以报错" 的提示，稍微严格一点
                    # 但考虑到实际情况可能只是没数据，先不强行在这里报错，由查询结果决定
                    pass
                    
    except ValueError as ve:
        raise ve
    except Exception as e:
        raise RuntimeError(f"校验元数据失败: {e}")

    # 2. 查询数据
    # 优化策略：按数据集分组查询，然后重组结果
    # 避免对同一个表发起多次查询
    results_map = {} # {(dataset, col): value}
    
    # 组织查询: {dataset: [col1, col2]}
    dataset_cols = {}
    for ds, col in zip(dataset_names, column_names):
        if ds not in dataset_cols:
            dataset_cols[ds] = set()
        dataset_cols[ds].add(col)
        
    with engine.connect() as conn:
        for ds, cols in dataset_cols.items():
            cols_list = list(cols)
            # 构建 SELECT col1, col2, ... FROM ds WHERE timestamp = ...
            select_clause = ", ".join([f"`{c}`" for c in cols_list])
            query = text(f"SELECT {select_clause} FROM `{ds}` WHERE `timestamp` = :ts")
            
            row = conn.execute(query, {"ts": ts}).fetchone()
            
            if row:
                for i, col in enumerate(cols_list):
                    results_map[(ds, col)] = row[i]
            else:
                for col in cols_list:
                    results_map[(ds, col)] = None
                    
    # 3. 按原始顺序返回列表
    final_results = []
    for ds, col in zip(dataset_names, column_names):
        final_results.append(results_map.get((ds, col)))
        
    return final_results

def get_all_data_period(dataset_names: List[str], column_names: List[str], timestamp_start, timestamp_end):
    """
    读取一段时间内的数据
    优化：从数据库的表设计层面优化这种连续时间步读取 -> 使用 Range Query
    """
    if len(dataset_names) != len(column_names):
        raise ValueError("dataset_names 和 column_names 长度必须一致")
    
    ts_start = _normalize_time_value(timestamp_start)
    ts_end = _normalize_time_value(timestamp_end)
    dataset_names = [_safe_table_name(name) for name in dataset_names]
    column_names = [_safe_column_name(name) for name in column_names]
    
    # 组织查询: {dataset: [col1, col2]}
    dataset_cols = {}
    for ds, col in zip(dataset_names, column_names):
        if ds not in dataset_cols:
            dataset_cols[ds] = set()
        dataset_cols[ds].add(col)
    
    # {dataset_name: DataFrame_of_that_dataset_in_range}
    data_cache = {}
    
    with engine.connect() as conn:
        for ds, cols in dataset_cols.items():
            cols_list = list(cols)
            # 一次性查出该时间段的所有数据
            select_clause = ", ".join([f"`{c}`" for c in cols_list])
            # 同时查出 timestamp 以便后续对其 (虽然是有序的，但为了安全)
            query = text(f"""
                SELECT `timestamp`, {select_clause} 
                FROM `{ds}` 
                WHERE `timestamp` BETWEEN :start AND :end 
                ORDER BY `timestamp` ASC
            """)
            
            # 使用 pandas 读取以方便处理
            df = pd.read_sql(query, conn, params={"start": ts_start, "end": ts_end})
            # 设置时间戳为索引以便查找
            if not df.empty:
                df.set_index('timestamp', inplace=True)
            data_cache[ds] = df

    # 组装结果
    # 这里的需求稍微有点歧义：是返回 [Series1, Series2, ...] 还是返回合并后的 DataFrame?
    # 根据函数签名 "return 一个列表，包含了这一系列“数据集-字段”的取值"，结合是 period
    # 假设返回 List[List[values]] 或者 List[pd.Series]
    # 考虑到不同数据集的时间点可能略有不同（虽然频率一致，但可能缺失），
    # 最稳妥的是返回 List[List[values]]，且对齐比较困难，除非我们确定它们时间戳完全一致。
    # 这里我们返回 List[List[values]]，每个内部列表对应一个 (dataset, col) 在该时间段的值序列
    
    final_output = []
    for ds, col in zip(dataset_names, column_names):
        df = data_cache.get(ds)
        if df is not None and col in df.columns:
            # 这里的 values 是在该时间范围内存在的。
            # 注意：如果原本的时间序列是不连续的（有缺失），这里取出的也是按时间顺序排列的非空值（或包含空值如果数据库有记录）
            # 如果需要严格的时间对齐（比如填充缺失日期），需要在 DataFrame 层做 resample。
            # 鉴于题目未明确主要求严格对齐，这里返回数据库中查到的序列。
            final_output.append(df[col].tolist())
        else:
            final_output.append([])
            
    return final_output
