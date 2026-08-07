"""Delete all 2026 rows from the four monthly textile trade tables.

The default mode is a read-only preview. Pass --execute to perform the deletion.
"""
from __future__ import annotations

import argparse
import os
import urllib.parse
from datetime import datetime

from sqlalchemy import create_engine, text


TABLES = (
    "hongkong_textile_trade_monthly",
    "mainland_textile_trade_monthly",
    "usa_textile_trade_monthly",
    "vietnam_textile_trade_monthly",
)
START = "2026-01-01 00:00:00"
END = "2027-01-01 00:00:00"
DEFAULT_DB_URL = (
    "mysql+pymysql://root:"
    f"{urllib.parse.quote_plus('pku')}@localhost:3306/port?charset=utf8mb4"
)


def count_rows(conn, table: str) -> int:
    return int(
        conn.execute(
            text(
                f"SELECT COUNT(*) FROM `{table}` "
                "WHERE `timestamp` >= :start AND `timestamp` < :end"
            ),
            {"start": START, "end": END},
        ).scalar_one()
    )


def update_metadata(conn, table: str) -> None:
    stats = conn.execute(
        text(
            f"SELECT COUNT(*) AS row_count, MIN(`timestamp`) AS start_time, "
            f"MAX(`timestamp`) AS end_time FROM `{table}`"
        )
    ).one()._mapping
    conn.execute(
        text(
            "UPDATE `meta_table_info` SET "
            "`row_count`=:row_count, `start_time`=:start_time, "
            "`end_time`=:end_time, `import_time`=:import_time "
            "WHERE `table_name`=:table_name"
        ),
        {
            "table_name": table,
            "row_count": stats["row_count"],
            "start_time": stats["start_time"],
            "end_time": stats["end_time"],
            "import_time": datetime.now(),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际删除；不加此参数时只显示将删除的行数",
    )
    args = parser.parse_args()

    engine = create_engine(os.getenv("PORT_DB_URL", DEFAULT_DB_URL), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            counts = {table: count_rows(conn, table) for table in TABLES}

        print("2026 年待删除数据：")
        for table, count in counts.items():
            print(f"  {table}: {count} 行")
        print(f"  合计: {sum(counts.values())} 行")

        if not args.execute:
            print("预览完成，未删除任何数据。确认后添加 --execute 执行。")
            return 0

        with engine.begin() as conn:
            for table in TABLES:
                result = conn.execute(
                    text(
                        f"DELETE FROM `{table}` "
                        "WHERE `timestamp` >= :start AND `timestamp` < :end"
                    ),
                    {"start": START, "end": END},
                )
                if result.rowcount != counts[table]:
                    raise RuntimeError(
                        f"{table} 预计删除 {counts[table]} 行，实际删除 {result.rowcount} 行"
                    )
                update_metadata(conn, table)
                print(f"  已删除 {table}: {result.rowcount} 行")

        with engine.connect() as conn:
            remaining = {table: count_rows(conn, table) for table in TABLES}
        if any(remaining.values()):
            raise RuntimeError(f"删除后仍存在 2026 年数据: {remaining}")

        print("删除完成：4 张表中已无 2026 年数据，meta_table_info 已同步更新。")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
