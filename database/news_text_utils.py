import json
import os
import re
import urllib.parse
import calendar
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional, Union

import pandas as pd
import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.mysql import LONGTEXT


PASSWORD = urllib.parse.quote_plus("pku")
DB_URL = f"mysql+pymysql://root:{PASSWORD}@localhost:3306/port?charset=utf8mb4"

engine = create_engine(
    DB_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    connect_args={"connect_timeout": 60},
)


def _safe_table_name(table_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
        raise ValueError(f"非法表名: {table_name}")
    return table_name


def prepare_meta_table() -> None:
    """确保 meta_table_info 表存在，保持和 database_utils_v3.py 兼容。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS `meta_table_info` (
                    `id` INT AUTO_INCREMENT PRIMARY KEY,
                    `table_name` VARCHAR(255) NOT NULL UNIQUE,
                    `source_csv` TEXT,
                    `columns_json` TEXT,
                    `frequency` VARCHAR(50),
                    `start_time` DATETIME,
                    `end_time` DATETIME,
                    `row_count` INT,
                    `import_time` DATETIME
                ) CHARSET=utf8mb4
                """
            )
        )


def prepare_news_text_table(table_name: str = "news_text", replace: bool = False) -> None:
    """
    创建最小新闻文本表。

    真实数据字段包括 timestamp、news_text 和 source；id 是数据库内部主键，
    用于区分同一天的多条新闻。
    """
    table_name = _safe_table_name(table_name)
    with engine.begin() as conn:
        if replace:
            conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS `{table_name}` (
                    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                    `timestamp` DATETIME NOT NULL,
                    `news_text` LONGTEXT NOT NULL,
                    `source` VARCHAR(255),
                    INDEX `idx_timestamp` (`timestamp`)
                ) CHARSET=utf8mb4
                """
            )
        )
        existing_columns = {
            row._mapping["Field"]
            for row in conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`"))
        }
        if "source" not in existing_columns:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `source` VARCHAR(255)"))


def _build_news_text(row: dict) -> str:
    title = str(row.get("title") or "").strip()
    content = str(row.get("content") or "").strip()
    if title and content:
        return f"{title}\n\n{content}"
    return title or content


def load_news_json_to_db(
    json_file: str,
    table_name: str = "news_text",
    if_exists: Literal["replace", "append"] = "replace",
) -> bool:
    """
    将新闻 JSON 数组导入数据库。

    JSON 每条记录使用 date/title/content/source：
    - date -> timestamp
    - title + content -> news_text
    - source -> source
    """
    table_name = _safe_table_name(table_name)
    if if_exists not in {"replace", "append"}:
        raise ValueError("if_exists 只能是 'replace' 或 'append'")

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            raise ValueError("JSON 顶层结构必须是数组")

        rows = []
        for row in records:
            if not isinstance(row, dict):
                continue
            timestamp = pd.to_datetime(row.get("date"), errors="coerce")
            news_text = _build_news_text(row)
            if pd.isna(timestamp) or not news_text:
                continue
            source = str(row.get("source") or "").strip() or None
            rows.append({
                "timestamp": timestamp.to_pydatetime(),
                "news_text": news_text,
                "source": source,
            })

        if not rows:
            print("⚠️ 没有可导入的新闻记录")
            return False

        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

        prepare_meta_table()
        prepare_news_text_table(table_name, replace=(if_exists == "replace"))

        with engine.begin() as conn:
            df.to_sql(
                name=table_name,
                con=conn,
                if_exists="append",
                index=False,
                chunksize=1000,
                method="multi",
                dtype={
                    "timestamp": sqlalchemy.DateTime,
                    "news_text": LONGTEXT,
                    "source": sqlalchemy.String(255),
                },
            )

            conn.execute(
                text("DELETE FROM `meta_table_info` WHERE `table_name` = :table_name"),
                {"table_name": table_name},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO `meta_table_info`
                    (table_name, source_csv, columns_json, frequency, start_time, end_time, row_count, import_time)
                    VALUES
                    (:table_name, :source_csv, :columns_json, :frequency, :start_time, :end_time, :row_count, :import_time)
                    """
                ),
                {
                    "table_name": table_name,
                    "source_csv": os.path.abspath(json_file),
                    "columns_json": json.dumps(["timestamp", "news_text", "source"], ensure_ascii=False),
                    "frequency": "event",
                    "start_time": df["timestamp"].min(),
                    "end_time": df["timestamp"].max(),
                    "row_count": len(df),
                    "import_time": datetime.now(),
                },
            )

        print(f"✅ 成功导入 `{table_name}`: {len(df)} 条新闻")
        print(f"   时间范围: {df['timestamp'].min()} - {df['timestamp'].max()}")
        return True

    except Exception as e:
        print(f"❌ 新闻导入失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def read_news_period(
    timestamp_start: Union[str, datetime],
    timestamp_end: Union[str, datetime],
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False,
) -> Union[pd.DataFrame, list[str]]:
    """读取一个时间区间内的新闻，闭区间 [timestamp_start, timestamp_end]。"""
    table_name = _safe_table_name(table_name)
    start = pd.to_datetime(timestamp_start)
    end = pd.to_datetime(timestamp_end)

    limit_clause = ""
    params = {"start": start, "end": end}
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit 必须为正整数")
        limit_clause = " LIMIT :limit"
        params["limit"] = int(limit)

    query = text(
        f"""
        SELECT `timestamp`, `news_text`, `source`
        FROM `{table_name}`
        WHERE `timestamp` BETWEEN :start AND :end
        ORDER BY `timestamp` ASC, `id` ASC
        {limit_clause}
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params=params)

    if text_only:
        return df["news_text"].tolist()
    return df


def read_news_by_year(
    year: int,
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False,
) -> Union[pd.DataFrame, list[str]]:
    start = f"{int(year):04d}-01-01"
    end = f"{int(year):04d}-12-31 23:59:59"
    return read_news_period(start, end, table_name=table_name, limit=limit, text_only=text_only)


def read_news_by_month(
    year: int,
    month: int,
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False,
) -> Union[pd.DataFrame, list[str]]:
    year = int(year)
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError("month 必须在 1 到 12 之间")
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1)
    end = datetime(year, month, last_day, 23, 59, 59)
    return read_news_period(start, end, table_name=table_name, limit=limit, text_only=text_only)


def read_news_by_quarter(
    year: int,
    quarter: Union[int, str],
    table_name: str = "news_text",
    limit: Optional[int] = None,
    text_only: bool = False,
) -> Union[pd.DataFrame, list[str]]:
    if isinstance(quarter, str):
        quarter = quarter.upper().replace("Q", "")
    quarter = int(quarter)
    if quarter not in {1, 2, 3, 4}:
        raise ValueError("quarter 必须是 1, 2, 3, 4 或 'Q1'...'Q4'")

    year = int(year)
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    last_day = calendar.monthrange(year, end_month)[1]
    start = datetime(year, start_month, 1)
    end = datetime(year, end_month, last_day, 23, 59, 59)
    return read_news_period(start, end, table_name=table_name, limit=limit, text_only=text_only)


if __name__ == "__main__":
    default_json = "/data5/zhimo/port/Gdelt/globalnews2015-2026/news_gov_hk.json"
    load_news_json_to_db(str(default_json), table_name="news_text", if_exists="replace")
