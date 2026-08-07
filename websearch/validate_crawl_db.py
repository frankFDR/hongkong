"""End-to-end validation: remove one month, crawl it again, and verify MySQL.

The selected month is removed from both the published CSV and its database table.
Removing it only from MySQL would merely test re-importing the existing CSV, not crawling.
On any failure, the original CSV and database rows are restored automatically.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DEFAULT_DB_URL = (
    "mysql+pymysql://root:"
    f"{urllib.parse.quote_plus('pku')}@localhost:3306/port?charset=utf8mb4"
)
SOURCES = {
    "hongkong": ("Honkong.csv", "hongkong_textile_trade_monthly"),
    "mainland": ("mainlandChina.csv", "mainland_textile_trade_monthly"),
    "usa": ("USA_Total.csv", "usa_textile_trade_monthly"),
    "vietnam": ("Vietnam.csv", "vietnam_textile_trade_monthly"),
}


def _month(value: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, format="%Y-%m", errors="raise")
    return parsed.replace(day=1)


def _db_rows(conn, table: str, month: pd.Timestamp) -> pd.DataFrame:
    return pd.read_sql(
        text(
            f"SELECT `timestamp`,`flow`,`trade_value` FROM `{table}` "
            "WHERE `timestamp`=:month ORDER BY `flow`"
        ),
        conn,
        params={"month": month.to_pydatetime()},
    )


def _restore_db(engine, table: str, month: pd.Timestamp, rows: pd.DataFrame) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM `{table}` WHERE `timestamp`=:month"),
            {"month": month.to_pydatetime()},
        )
        if not rows.empty:
            rows.to_sql(table, conn, if_exists="append", index=False, method="multi")


def validate(source_name: str, requested_month: str | None, python: Path) -> None:
    filename, table = SOURCES[source_name]
    csv_path = DATA_DIR / filename
    original_csv = csv_path.read_bytes()
    csv_frame = pd.read_csv(csv_path, encoding="utf-8-sig")
    if csv_frame.empty:
        raise RuntimeError(f"{csv_path} 为空")

    month = _month(requested_month) if requested_month else pd.to_datetime(
        str(int(csv_frame["年月"].max())), format="%Y%m"
    )
    month_key = int(month.strftime("%Y%m"))
    csv_mask = pd.to_numeric(csv_frame["年月"], errors="coerce").eq(month_key)
    if csv_mask.sum() != 1:
        raise RuntimeError(f"CSV 中 {month:%Y-%m} 应有 1 行，实际 {int(csv_mask.sum())} 行")
    original_csv_row = csv_frame.loc[csv_mask].copy()

    engine = create_engine(os.getenv("PORT_DB_URL", DEFAULT_DB_URL), pool_pre_ping=True)
    succeeded = False
    original_db_rows = pd.DataFrame()
    try:
        with engine.connect() as conn:
            original_db_rows = _db_rows(conn, table, month)
        if len(original_db_rows) != 2:
            raise RuntimeError(f"数据库中 {month:%Y-%m} 应有 import/export 2 行，实际 {len(original_db_rows)} 行")

        # Preserve the exact original bytes in memory, then make the month absent from
        # every cache used by --refresh data.
        csv_frame.loc[~csv_mask].to_csv(csv_path, index=False, encoding="utf-8-sig")
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM `{table}` WHERE `timestamp`=:month"),
                {"month": month.to_pydatetime()},
            )
        with engine.connect() as conn:
            if not _db_rows(conn, table, month).empty:
                raise RuntimeError("测试准备失败：数据库目标月份仍然存在")

        print(f"[1/3] 已删除 {source_name} {month:%Y-%m}: CSV 1 行，DB 2 行", flush=True)
        command = [
            str(python), str(HERE / "update_all.py"), "--refresh", "data",
            "--only", source_name,
        ]
        print(f"[2/3] 执行: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=HERE, check=False)
        if completed.returncode != 0:
            # update_all isolates sources: an unrelated source may fail while the
            # selected source is crawled, published, and imported successfully.
            print(
                f"警告: update_all.py 总退出码为 {completed.returncode}；"
                "继续按目标月份的实际恢复结果判定",
                flush=True,
            )

        refreshed_csv = pd.read_csv(csv_path, encoding="utf-8-sig")
        refreshed_row = refreshed_csv[
            pd.to_numeric(refreshed_csv["年月"], errors="coerce").eq(month_key)
        ]
        with engine.connect() as conn:
            refreshed_db = _db_rows(conn, table, month)
        if len(refreshed_row) != 1 or len(refreshed_db) != 2:
            raise RuntimeError(
                f"恢复不完整：CSV={len(refreshed_row)} 行，DB={len(refreshed_db)} 行"
            )
        if set(refreshed_db["flow"]) != {"import", "export"}:
            raise RuntimeError(f"数据库 flow 异常: {refreshed_db['flow'].tolist()}")
        if refreshed_db["trade_value"].isna().any():
            raise RuntimeError("数据库恢复行包含空 trade_value")

        print("[3/3] 验证成功", flush=True)
        print(refreshed_db.to_string(index=False), flush=True)
        succeeded = True
    finally:
        if not succeeded:
            csv_path.write_bytes(original_csv)
            _restore_db(engine, table, month, original_db_rows)
            print("验证失败，已恢复原 CSV 和数据库行", file=sys.stderr, flush=True)
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), default="hongkong")
    parser.add_argument("--month", help="待验证月份，格式 YYYY-MM；默认选择该来源最新月份")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="运行 update_all.py 的 Python；默认使用当前解释器",
    )
    args = parser.parse_args()
    validate(args.source, args.month, args.python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
