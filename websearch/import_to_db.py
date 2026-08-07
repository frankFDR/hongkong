"""Transform the published crawler CSV files and load them into MySQL."""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime
from pathlib import Path

import pandas as pd
import sqlalchemy
from sqlalchemy import create_engine, text


DEFAULT_DB_URL = (
    "mysql+pymysql://root:"
    f"{urllib.parse.quote_plus('pku')}@localhost:3306/port?charset=utf8mb4"
)

TRADE_TABLES = {
    "mainlandChina.csv": "mainland_textile_trade_monthly",
    "USA_Total.csv": "usa_textile_trade_monthly",
    "Honkong.csv": "hongkong_textile_trade_monthly",
    "Vietnam.csv": "vietnam_textile_trade_monthly",
}
THROUGHPUT_FILE = "HongKong_Port_Throughput.csv"
THROUGHPUT_TABLE = "sea_river_import_export_throughput"


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _trade_frame(path: Path) -> pd.DataFrame:
    source = _read_csv(path)
    required = {"年月", "进口额", "出口额"}
    if not required.issubset(source.columns):
        raise ValueError(f"{path.name} 缺少字段: {sorted(required - set(source.columns))}")
    timestamp = pd.to_datetime(source["年月"].astype(str), format="%Y%m", errors="raise")
    result = pd.concat(
        [
            pd.DataFrame({"timestamp": timestamp, "flow": "import", "trade_value": source["进口额"]}),
            pd.DataFrame({"timestamp": timestamp, "flow": "export", "trade_value": source["出口额"]}),
        ],
        ignore_index=True,
    )
    result["trade_value"] = pd.to_numeric(result["trade_value"], errors="raise")
    return result.sort_values(["timestamp", "flow"]).reset_index(drop=True)


def _throughput_frame(path: Path) -> pd.DataFrame:
    source = _read_csv(path)
    mapping = {
        "河运_离港_千公吨": "river_export",
        "河运_抵港_千公吨": "river_import",
        "海运_离港_千公吨": "sea_export",
        "海运_抵港_千公吨": "sea_import",
    }
    required = {"年", "月", *mapping}
    if not required.issubset(source.columns):
        raise ValueError(f"{path.name} 缺少字段: {sorted(required - set(source.columns))}")
    frame = source[["年", "月", *mapping]].rename(columns={"年": "year", "月": "month", **mapping})
    frame["quarter"] = ((frame["month"].astype(int) - 1) // 3 + 1).astype(int)
    values = list(mapping.values())
    # Official monthly figures are in thousand metric tonnes; quarterly figures are sums.
    grouped = frame.groupby(["year", "quarter"], as_index=False)
    result = grouped[values].sum()
    month_counts = grouped["month"].nunique().rename(columns={"month": "month_count"})
    result = result.merge(month_counts, on=["year", "quarter"])
    # Do not publish an incomplete quarter as if it were a final quarterly total.
    result = result[result["month_count"] == 3].drop(columns="month_count")
    result["timestamp"] = pd.to_datetime(
        result["year"].astype(int).astype(str) + "-" + ((result["quarter"] - 1) * 3 + 1).astype(str) + "-01"
    )
    return result[["year", "quarter", *values, "timestamp"]]


def _prepare_meta_table(conn) -> None:
    conn.execute(text("""
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
    """))


def _replace_table(engine, table_name: str, frame: pd.DataFrame, source: Path, frequency: str) -> None:
    if frame.empty or frame["timestamp"].isna().any():
        raise ValueError(f"{source.name} 没有有效数据或包含无效时间")
    with engine.begin() as conn:
        frame.to_sql(
            table_name, conn, if_exists="replace", index=False, chunksize=1000, method="multi",
            dtype={"timestamp": sqlalchemy.DateTime},
        )
        conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"))
        conn.execute(text(f"ALTER TABLE `{table_name}` ADD INDEX `idx_timestamp` (`timestamp`)"))
        _prepare_meta_table(conn)
        conn.execute(text("DELETE FROM `meta_table_info` WHERE `table_name`=:table_name"), {"table_name": table_name})
        conn.execute(text("""
            INSERT INTO `meta_table_info`
            (`table_name`,`source_csv`,`columns_json`,`frequency`,`start_time`,`end_time`,`row_count`,`import_time`)
            VALUES (:table_name,:source_csv,:columns_json,:frequency,:start_time,:end_time,:row_count,:import_time)
        """), {
            "table_name": table_name,
            "source_csv": str(source.resolve()),
            "columns_json": json.dumps(frame.columns.tolist(), ensure_ascii=False),
            "frequency": frequency,
            "start_time": frame["timestamp"].min(),
            "end_time": frame["timestamp"].max(),
            "row_count": len(frame),
            "import_time": datetime.now(),
        })
    print(f"  DB: `{table_name}` <- {source.name} ({len(frame)} rows)")


def import_published_data(data_dir: Path, filenames: set[str] | None = None) -> list[str]:
    """Load selected published files; defaults to all five crawler outputs."""
    selected = filenames or set(TRADE_TABLES) | {THROUGHPUT_FILE}
    engine = create_engine(os.getenv("PORT_DB_URL", DEFAULT_DB_URL), pool_pre_ping=True)
    imported: list[str] = []
    try:
        for filename, table_name in TRADE_TABLES.items():
            if filename in selected:
                source = data_dir / filename
                _replace_table(engine, table_name, _trade_frame(source), source, "monthly")
                imported.append(table_name)
        if THROUGHPUT_FILE in selected:
            source = data_dir / THROUGHPUT_FILE
            frame = _throughput_frame(source)
            # The crawler starts in 2015 while the established table has history from 1997.
            # Retain those older rows when refreshing the crawler-covered period.
            with engine.connect() as conn:
                if sqlalchemy.inspect(conn).has_table(THROUGHPUT_TABLE):
                    history = pd.read_sql(
                        text(f"SELECT `year`,`quarter`,`river_export`,`river_import`,`sea_export`,`sea_import`,`timestamp` FROM `{THROUGHPUT_TABLE}` WHERE `timestamp` < :start"),
                        conn,
                        params={"start": frame["timestamp"].min()},
                    )
                    frame = pd.concat([history, frame], ignore_index=True)
            _replace_table(engine, THROUGHPUT_TABLE, frame, source, "quarterly")
            imported.append(THROUGHPUT_TABLE)
    finally:
        engine.dispose()
    return imported
