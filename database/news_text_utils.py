import json
import os
import re
import urllib.parse
import calendar
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, Literal, Optional, Union

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


BASE_DIR = Path(__file__).resolve().parents[1]
GDELT_DIR = BASE_DIR / "Gdelt"
ORIGINAL_NEWS_DIR = GDELT_DIR / "globalnews2015-2026"

PREFILTER_DIRS = {
    "textile": [GDELT_DIR / "textile" / "prefilter_results_monthly"],
    "throughput": [
        GDELT_DIR / "data_news_filter",
        GDELT_DIR / "data_news_filter_2",
        GDELT_DIR / "data_news_filter_18_19",
    ],
}
SCORE_FILES = {
    "textile": [GDELT_DIR / "textile" / "impact_scores" / "news_impacts.json"],
    "throughput": [
        GDELT_DIR / "data_news_filter" / "top10pct_impact_scores" / "news_impacts.json",
        GDELT_DIR / "data_news_filter_2" / "top10pct_impact_scores" / "news_impacts.json",
        GDELT_DIR / "data_news_filter_18_19" / "top10pct_impact_scores" / "news_impacts.json",
    ],
}

THROUGHPUT_SCORE_KEYS = {
    "海运_抵港": "sea_import",
    "海运_离港": "sea_export",
    "河运_抵港": "river_import",
    "河运_离港": "river_export",
}
TEXTILE_SCORE_KEYS = {
    "中国大陆_出口": "mainland_export",
    "中国大陆_进口": "mainland_import",
    "美国_出口": "usa_export",
    "美国_进口": "usa_import",
    "越南_出口": "vietnam_export",
    "越南_进口": "vietnam_import",
    "香港_出口": "hongkong_export",
    "香港_进口": "hongkong_import",
}


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

    真实数据字段包括 timestamp、title、news_text 和 source；id 是数据库内部主键，
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
                    `news_title` TEXT,
                    `news_text` LONGTEXT NOT NULL,
                    `source` VARCHAR(255),
                    `throughput_relevance` DOUBLE,
                    `textile_relevance` DOUBLE,
                    `throughput_summary` TEXT,
                    `textile_summary` TEXT,
                    `throughput_score` JSON,
                    `textile_score` JSON,
                    INDEX `idx_timestamp` (`timestamp`)
                ) CHARSET=utf8mb4
                """
            )
        )
        existing_columns = {
            row._mapping["Field"]
            for row in conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`"))
        }
        # 旧表的 title -> news_title 迁移由 migrate_news_text_schema.py 独立负责。
        required = {
            "source": "VARCHAR(255)",
            "throughput_relevance": "DOUBLE",
            "textile_relevance": "DOUBLE",
            "throughput_summary": "TEXT",
            "textile_summary": "TEXT",
            "throughput_score": "JSON",
            "textile_score": "JSON",
        }
        if "news_title" not in existing_columns:
            if "title" in existing_columns:
                raise RuntimeError(
                    "检测到旧字段 title；请先运行 database/migrate_news_text_schema.py"
                )
            conn.execute(
                text(f"ALTER TABLE `{table_name}` ADD COLUMN `news_title` TEXT AFTER `timestamp`")
            )
        for column, definition in required.items():
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{column}` {definition}"))


def _build_news_text(row: dict) -> str:
    return str(row.get("content") or "").strip()


def load_news_json_to_db(
    json_file: str,
    table_name: str = "news_text",
    if_exists: Literal["replace", "append"] = "replace",
) -> bool:
    """
    将新闻 JSON 数组导入数据库。

    JSON 每条记录使用 date/title/content/source：
    - date -> timestamp
    - title -> title
    - content -> news_text
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
            title = str(row.get("title") or "").strip() or None
            news_text = _build_news_text(row)
            if pd.isna(timestamp) or not news_text:
                continue
            source = str(row.get("source") or "").strip() or None
            rows.append({
                "timestamp": timestamp.to_pydatetime(),
                "news_title": title,
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
                    "news_title": sqlalchemy.Text,
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
                    "columns_json": json.dumps(
                        ["timestamp", "news_title", "news_text", "source"],
                        ensure_ascii=False,
                    ),
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


def _score_with_english_keys(scores: dict, kind: str) -> dict:
    """将模型结果中的中文维度名转换成数据库约定的英文键。"""
    key_map = TEXTILE_SCORE_KEYS if kind == "textile" else THROUGHPUT_SCORE_KEYS
    converted = {}
    for source_key, value in (scores or {}).items():
        target_key = key_map.get(source_key)
        if target_key:
            converted[target_key] = value
    return converted


def _load_impact_scores() -> Dict[str, dict]:
    merged: Dict[str, dict] = {}
    for kind, files in SCORE_FILES.items():
        for score_file in files:
            with score_file.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            for item in payload.get("news_impacts", []):
                news_id = item.get("news_id")
                if not news_id:
                    continue
                target = merged.setdefault(news_id, {})
                target[f"{kind}_summary"] = item.get("news_summary") or None
                target[f"{kind}_score"] = _score_with_english_keys(
                    item.get("scores_confidence") or {}, kind
                )
    return merged


def _prefilter_csv_files(kind: str) -> Iterator[Path]:
    for directory in PREFILTER_DIRS[kind]:
        pattern = "month_*_news.csv" if kind == "textile" else "quarter_*_scored.csv"
        yield from sorted(directory.glob(pattern))


def _load_prefilter_records() -> Dict[str, dict]:
    """按 news_id 合并港口与纺织初筛结果。"""
    records: Dict[str, dict] = {}
    for kind in ("throughput", "textile"):
        for csv_file in _prefilter_csv_files(kind):
            df = pd.read_csv(csv_file, dtype=str, encoding="utf-8-sig").fillna("")
            for row in df.to_dict("records"):
                news_id = row.get("news_id", "").strip()
                if not news_id:
                    continue
                confidence = pd.to_numeric(row.get("confidence"), errors="coerce")
                candidate = {
                    "news_id": news_id,
                    "date": row.get("date") or None,
                    "source": row.get("source") or None,
                    "source_file": row.get("source_file") or f"{news_id.rsplit(':', 1)[0]}.json",
                    "title": row.get("title") or None,
                    "url": row.get("url") or None,
                    "content_excerpt": row.get("content_excerpt") or "",
                }
                target = records.setdefault(news_id, candidate)
                # 同一类初筛出现重复时，较高置信度胜出；跨类别只补齐公共字段。
                old_confidence = target.get(f"{kind}_relevance")
                if old_confidence is None or (
                    not pd.isna(confidence) and confidence > old_confidence
                ):
                    target[f"{kind}_relevance"] = None if pd.isna(confidence) else float(confidence)
                    for key, value in candidate.items():
                        if value and (not target.get(key) or key == "content_excerpt"):
                            target[key] = value
    scores = _load_impact_scores()
    for news_id, values in records.items():
        values.update(scores.get(news_id, {}))
    return records


def _iter_large_json_array(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[dict]:
    """仅用标准库流式解析巨型顶层 JSON 数组。"""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8-sig") as f:
        buffer = ""
        pos = 0
        started = False
        eof = False
        while True:
            if pos >= len(buffer) and not eof:
                buffer = f.read(chunk_size)
                pos = 0
                eof = not buffer
            if not started:
                bracket = buffer.find("[", pos)
                if bracket < 0:
                    if eof:
                        raise ValueError(f"{path} 不是 JSON 数组")
                    buffer += f.read(chunk_size)
                    continue
                pos = bracket + 1
                started = True
            while True:
                while pos < len(buffer) and (buffer[pos].isspace() or buffer[pos] == ","):
                    pos += 1
                if pos < len(buffer) and buffer[pos] == "]":
                    return
                try:
                    value, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    chunk = f.read(chunk_size)
                    buffer = buffer[pos:] + chunk
                    pos = 0
                    eof = not chunk
                    continue
                yield value
                pos = end
                if pos > chunk_size:
                    buffer = buffer[pos:]
                    pos = 0
                break


def iter_merged_scored_news() -> Iterator[dict]:
    """生成合并后的初筛新闻；全文优先取自原始 JSON。"""
    records = _load_prefilter_records()
    by_file: Dict[str, Dict[int, dict]] = {}
    for news_id, record in records.items():
        try:
            source_stem, index_text = news_id.rsplit(":", 1)
            source_index = int(index_text)
        except (ValueError, TypeError):
            source_stem, source_index = "", -1
        filename = record.get("source_file") or f"{source_stem}.json"
        by_file.setdefault(filename, {})[source_index] = record

    emitted = set()
    for filename, targets in by_file.items():
        original_file = ORIGINAL_NEWS_DIR / filename
        if original_file.exists():
            max_index = max(targets)
            for index, original in enumerate(_iter_large_json_array(original_file)):
                if index > max_index:
                    break
                record = targets.get(index)
                if record is None:
                    continue
                emitted.add(record["news_id"])
                yield _build_merged_row(record, original)
        for record in targets.values():
            if record["news_id"] not in emitted:
                yield _build_merged_row(record, {})


def _build_merged_row(record: dict, original: dict) -> dict:
    timestamp = pd.to_datetime(original.get("date") or record.get("date"), errors="coerce")
    return {
        "timestamp": None if pd.isna(timestamp) else timestamp.to_pydatetime(),
        "news_title": original.get("title") or record.get("title") or None,
        "news_text": (original.get("content") or record.get("content_excerpt") or "").strip(),
        "source": original.get("source") or record.get("source") or None,
        "throughput_relevance": record.get("throughput_relevance"),
        "textile_relevance": record.get("textile_relevance"),
        "throughput_summary": record.get("throughput_summary"),
        "textile_summary": record.get("textile_summary"),
        "throughput_score": record.get("throughput_score"),
        "textile_score": record.get("textile_score"),
    }


def append_scored_prefilter_news(
    table_name: str = "news_text", batch_size: int = 500
) -> int:
    """将全部初筛新闻合并后追加到现有 news_text 表。"""
    table_name = _safe_table_name(table_name)
    prepare_news_text_table(table_name, replace=False)
    dtype = {
        "timestamp": sqlalchemy.DateTime,
        "news_title": sqlalchemy.Text,
        "news_text": LONGTEXT,
        "source": sqlalchemy.String(255),
        "throughput_relevance": sqlalchemy.Float,
        "textile_relevance": sqlalchemy.Float,
        "throughput_summary": sqlalchemy.Text,
        "textile_summary": sqlalchemy.Text,
        "throughput_score": sqlalchemy.JSON(none_as_null=True),
        "textile_score": sqlalchemy.JSON(none_as_null=True),
    }
    batch, inserted = [], 0
    for row in iter_merged_scored_news():
        if row["timestamp"] is None or not row["news_text"]:
            continue
        batch.append(row)
        if len(batch) >= batch_size:
            pd.DataFrame(batch).to_sql(table_name, engine, if_exists="append", index=False,
                                       chunksize=batch_size, method="multi", dtype=dtype)
            inserted += len(batch)
            print(f"已追加 {inserted} 条")
            batch.clear()
    if batch:
        pd.DataFrame(batch).to_sql(table_name, engine, if_exists="append", index=False,
                                   chunksize=batch_size, method="multi", dtype=dtype)
        inserted += len(batch)
    print(f"✅ 合并并追加完成，共 {inserted} 条新闻")
    return inserted


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
        SELECT `timestamp`, `news_title`, `news_text`, `source`,
               `throughput_relevance`, `textile_relevance`,
               `throughput_summary`, `textile_summary`,
               `throughput_score`, `textile_score`
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
    append_scored_prefilter_news()
