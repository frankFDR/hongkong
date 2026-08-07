import argparse
from pathlib import Path
from typing import Optional


def _resolve_translate_mode(
    yes_translate: bool,
    no_translate: bool,
    ask_translate: bool,
    default: Optional[bool],
) -> Optional[bool]:
    selected = sum(bool(flag) for flag in [yes_translate, no_translate, ask_translate])
    if selected > 1:
        raise ValueError("--yes-translate、--no-translate、--ask-translate 只能选择一个")
    if yes_translate:
        return True
    if no_translate:
        return False
    if ask_translate:
        return None
    return default


def _import_csv_to_db(*args, **kwargs):
    from database_utils import engine, import_csv_to_db
    from sqlalchemy import inspect, text

    success = import_csv_to_db(*args, **kwargs)
    if not success:
        return success

    # v4 可能会把中文表名翻译成英文，因此优先通过元数据表查找实际表名。
    requested_table_name = kwargs.get("table_name")
    if requested_table_name is None and len(args) > 1:
        requested_table_name = args[1]

    actual_table_name = requested_table_name
    with engine.begin() as conn:
        if inspect(conn).has_table("meta_table_info"):
            metadata_row = conn.execute(
                text(
                    "SELECT `table_name` FROM `meta_table_info` "
                    "WHERE `original_table_name` = :original_table_name "
                    "ORDER BY `import_time` DESC LIMIT 1"
                ),
                {"original_table_name": str(requested_table_name)},
            ).first()
            if metadata_row:
                actual_table_name = metadata_row._mapping["table_name"]

        if not actual_table_name or not inspect(conn).has_table(actual_table_name):
            raise RuntimeError(f"导入成功，但无法定位目标表: {requested_table_name}")

        columns = {column["name"] for column in inspect(conn).get_columns(actual_table_name)}
        if "id" not in columns:
            escaped_table_name = str(actual_table_name).replace("`", "``")
            conn.execute(
                text(
                    f"ALTER TABLE `{escaped_table_name}` "
                    "ADD COLUMN `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
                )
            )
            print(f"✅ 已为表 `{actual_table_name}` 添加自增主键 `id`")
        else:
            print(f"ℹ️ 表 `{actual_table_name}` 已有 `id` 字段，保持原有结构。")

    return success


def import_single_csv(
    csv_file: str,
    table_name: Optional[str] = None,
    frequency: Optional[str] = None,
    auto_translate_identifiers: Optional[bool] = None,
) -> bool:
    path = Path(csv_file)
    if not path.is_file():
        print(f"❌ 错误: `{csv_file}` 不是一个有效的 CSV 文件")
        return False

    target_table = table_name or path.stem
    print(f"🚀 导入单个 CSV: {path} -> 表: {target_table}")
    return _import_csv_to_db(
        str(path),
        target_table,
        frequency=frequency,
        auto_translate_identifiers=auto_translate_identifiers,
    )


def batch_import_csv_from_folder(
    folder_path: str,
    limit: int = -1,
    recursive: bool = False,
    frequency: Optional[str] = None,
    auto_translate_identifiers: Optional[bool] = True,
):
    """
    批量导入指定文件夹下的 CSV 文件。

    默认 auto_translate_identifiers=True，适合批量处理中文文件名/字段名：
    v4 会自动翻译为英文表名和英文字段名后入库。
    """
    path = Path(folder_path)

    if not path.is_dir():
        print(f"❌ 错误: 路径 `{folder_path}` 不是一个有效的目录")
        return {"success": 0, "failed": [path]}

    pattern = "**/*.csv" if recursive else "*.csv"
    csv_files = sorted(path.glob(pattern))

    if not csv_files:
        print("📁 文件夹中未发现 CSV 文件")
        return {"success": 0, "failed": []}

    if limit is not None and limit > 0:
        csv_files = csv_files[:limit]

    print(f"🚀 开始批量导入，共发现 {len(csv_files)} 个文件...")
    if auto_translate_identifiers is True:
        print("ℹ️ 批量模式默认自动翻译非英文表名/字段名为英文标识符。")

    summary = {"success": 0, "failed": []}

    for index, file_path in enumerate(csv_files, start=1):
        table_name = file_path.stem
        print("-" * 60)
        print(f"[{index}/{len(csv_files)}] 正在处理: {file_path.name} -> 表: {table_name}")

        success = _import_csv_to_db(
            str(file_path),
            table_name,
            frequency=frequency,
            auto_translate_identifiers=auto_translate_identifiers,
        )

        if success:
            summary["success"] += 1
        else:
            summary["failed"].append(file_path)

    print("-" * 60)
    print(f"📊 导入完成！成功: {summary['success']}, 失败: {len(summary['failed'])}")
    if summary["failed"]:
        print("失败的文件列表:")
        for failed_file in summary["failed"]:
            print(f" - {failed_file}")
    return summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import CSV file(s) into MySQL port database using database_utils."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="/data5/zhimo/port/processed_csv",
        help="CSV 文件路径或包含 CSV 的文件夹路径。默认导入 processed_csv 文件夹。",
    )
    parser.add_argument("--table-name", help="单文件导入时指定表名；不指定则使用文件名。")
    parser.add_argument("--batch", action="store_true", help="强制按文件夹批量导入。")
    parser.add_argument("--recursive", action="store_true", help="批量导入时递归搜索子目录。")
    parser.add_argument("--limit", type=int, default=-1, help="批量导入文件数量上限；-1 表示不限制。")
    parser.add_argument(
        "--frequency",
        choices=["yearly", "quarterly", "monthly", "daily", "unknown"],
        help="手动指定采样频率；不指定则由 v4 自动推断。",
    )
    parser.add_argument(
        "--yes-translate",
        action="store_true",
        help="发现非英文库名/表名/字段名时自动翻译后继续。",
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="发现非英文库名/表名/字段名时直接退出当前文件导入。",
    )
    parser.add_argument(
        "--ask-translate",
        action="store_true",
        help="发现非英文库名/表名/字段名时逐个询问。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    path = Path(args.path)
    is_batch = args.batch or path.is_dir()

    # 批量导入默认自动翻译，单文件导入默认沿用 v4 的交互询问。
    default_translate = True if is_batch else None
    try:
        translate_mode = _resolve_translate_mode(
            args.yes_translate,
            args.no_translate,
            args.ask_translate,
            default_translate,
        )
    except ValueError as e:
        print(f"❌ {e}")
        return

    if is_batch:
        batch_import_csv_from_folder(
            str(path),
            limit=args.limit,
            recursive=args.recursive,
            frequency=args.frequency,
            auto_translate_identifiers=translate_mode,
        )
    else:
        import_single_csv(
            str(path),
            table_name=args.table_name,
            frequency=args.frequency,
            auto_translate_identifiers=translate_mode,
        )

# --- 示例调用 ---
if __name__ == "__main__":
    # main()
    # csv_folder_path = '/data5/zhimo/port/纺织物数据/vietnam_textile_trade_monthly.csv'
    # table_name = '越南纺织物总表'
    # import_single_csv(str(csv_folder_path), table_name)
    # csv_folder_path = '/data5/zhimo/port/processed_csv'
    # csv_folder_path = '/data5/zhimo/port/纺织物_0628'
    # csv_folder_path = '/data5/zhimo/port/0331/对外贸易_商品贸易'
    csv_folder_path = '/data5/zhimo/port/纺织物_0710/data_for_mysql'
    csv_folder_path = '/data5/zhimo/port/database/sea_river_import_export_throughput.csv'
    # batch_import_csv_from_folder(csv_folder_path, limit=100)
    
    import_single_csv(csv_folder_path, 'sea_river_import_export_throughput')
