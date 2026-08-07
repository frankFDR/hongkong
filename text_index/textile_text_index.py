from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ACTUAL_TABLE_NAME = "realtext_actual.csv"
DB_TABLE_NAME = "news_text"
HORIZON_MONTHS = 24
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATABASE_DIR = ROOT.parent / "database"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def feature_keys(config: dict) -> list[str]:
    return [entry["key"] for entry in config["features"]]


def resolve_path(relative_path: str) -> Path:
    path = (ROOT / relative_path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as error:
        raise ValueError(f"Path escapes the package: {relative_path}") from error
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read existing textile scores from MySQL and calculate the text index."
    )
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--month", help="One target month, for example 2025-12.")
    period.add_argument(
        "--range",
        nargs=2,
        metavar=("START_MONTH", "END_MONTH"),
        help="Inclusive target-month range.",
    )
    parser.add_argument("--table", default=DB_TABLE_NAME, help="MySQL news table.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output CSV. Single-month results are always printed as JSON.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Compare calculated values with data/text_index/realtext_actual.csv.",
    )
    parser.add_argument(
        "--tolerance", type=float, default=1e-12, help="Absolute validation tolerance."
    )
    return parser.parse_args()


def normalize_month(value: str) -> str:
    value = str(value).strip()
    for separator in ("-", "M"):
        if separator in value:
            year_text, month_text = value.split(separator, 1)
            year, month = int(year_text), int(month_text)
            if year < 1 or month not in range(1, 13):
                break
            return f"{year:04d}-{month:02d}"
    raise ValueError(f"Invalid month {value!r}; expected YYYY-MM or YYYYMmm")


def shift_month(month: str, offset: int) -> str:
    year, month_number = (int(part) for part in normalize_month(month).split("-"))
    ordinal = year * 12 + month_number - 1 + offset
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def month_range(first: str, last: str) -> list[str]:
    first, last = normalize_month(first), normalize_month(last)
    count = (int(last[:4]) - int(first[:4])) * 12 + int(last[5:]) - int(first[5:])
    if count < 0:
        raise ValueError(f"Start month {first} is after end month {last}")
    return [shift_month(first, offset) for offset in range(count + 1)]


def database_bounds(first_target: str, last_target: str) -> tuple[datetime, datetime]:
    first_source = shift_month(first_target, -(HORIZON_MONTHS - 1))
    start = datetime.strptime(first_source + "-01", "%Y-%m-%d")
    next_month = shift_month(last_target, 1)
    end = datetime.strptime(next_month + "-01", "%Y-%m-%d")
    # read_news_period uses a closed interval; one microsecond avoids the next month.
    return start, end - timedelta(microseconds=1)


def read_database_rows(first_target: str, last_target: str, table_name: str) -> list[dict]:
    if str(DATABASE_DIR) not in sys.path:
        sys.path.insert(0, str(DATABASE_DIR))
    try:
        import pandas as pd
        from sqlalchemy import text

        from news_text_utils import _safe_table_name, engine
    except ImportError as error:
        raise RuntimeError(
            "Database dependencies are missing. Install pandas, sqlalchemy and pymysql "
            "in the Python environment used to run this script."
        ) from error

    start, end = database_bounds(first_target, last_target)
    table_name = _safe_table_name(table_name)
    query = text(
        f"""
        SELECT `timestamp`, `textile_relevance`, `textile_score`
        FROM `{table_name}`
        WHERE `timestamp` BETWEEN :start AND :end
          AND `textile_relevance` IS NOT NULL
        ORDER BY `timestamp` ASC, `id` ASC
        """
    )
    with engine.connect() as connection:
        frame = pd.read_sql(query, connection, params={"start": start, "end": end})
    return frame.to_dict("records")


def parse_score(value: Any) -> dict[str, Any] | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"textile_score must be a JSON object, got {type(value).__name__}")
    return value


def row_month(row: dict) -> str:
    timestamp = row["timestamp"]
    if hasattr(timestamp, "strftime"):
        return timestamp.strftime("%Y-%m")
    return normalize_month(str(timestamp)[:7])


def calculate_indices(
    rows: Iterable[dict],
    targets: list[str],
    target_months: list[str],
    threshold: float,
    score_start: int,
    score_end: int,
) -> dict[str, dict[str, float]]:
    target_set = set(target_months)
    positive_prefilter: defaultdict[str, int] = defaultdict(int)
    positive_channel: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    weighted: defaultdict[str, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    counts: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for row_number, row in enumerate(rows, start=1):
        source_month = row_month(row)
        relevance_raw = row.get("textile_relevance")
        relevance = 0.0 if relevance_raw is None else float(relevance_raw)
        if relevance > 0 and source_month in target_set:
            positive_prefilter[source_month] += 1

        score = parse_score(row.get("textile_score"))
        if score is None:
            continue
        for feature in targets:
            impact = score.get(feature)
            if not isinstance(impact, dict):
                raise ValueError(f"Database row {row_number} is missing score target {feature}")
            confidence = float(impact.get("confidence", 0.0))
            if confidence > 0 and source_month in target_set:
                positive_channel[source_month][feature] += 1
            if confidence <= threshold:
                continue
            scores = impact.get("scores")
            if not isinstance(scores, list) or len(scores) != HORIZON_MONTHS:
                raise ValueError(
                    f"Database row {row_number}, target {feature}: expected 24 scores"
                )
            for lag in range(score_start, score_end + 1):
                value = float(scores[lag])
                if not math.isfinite(value):
                    raise ValueError(
                        f"Database row {row_number}, target {feature}: non-finite score"
                    )
                if value == 0:
                    continue
                target_month = shift_month(source_month, lag)
                if target_month in target_set:
                    weighted[target_month][feature] += confidence * value
                    counts[target_month][feature] += 1

    values: dict[str, dict[str, float]] = {}
    for month in target_months:
        denominator = positive_prefilter[month]
        values[month] = {}
        for feature in targets:
            count = counts[month][feature]
            base_index = weighted[month][feature] / count if count else 0.0
            coefficient = positive_channel[month][feature] / denominator if denominator else 0.0
            values[month][feature] = base_index * coefficient
    return values


def write_csv(path: Path, months: list[str], features: list[str], values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["month", *features])
        for month in months:
            writer.writerow([month, *[values[month][feature] for feature in features]])


def validate_against_reference(
    reference: Path, months: list[str], features: list[str], values: dict, tolerance: float
) -> tuple[int, float]:
    expected: dict[str, dict[str, float]] = {}
    with reference.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["month", *features]:
            raise ValueError(f"Reference columns differ: {reader.fieldnames}")
        for row in reader:
            expected[row["month"]] = {feature: float(row[feature]) for feature in features}

    mismatches, maximum = 0, 0.0
    for month in months:
        if month not in expected:
            raise ValueError(f"Reference has no row for {month}")
        for feature in features:
            difference = abs(values[month][feature] - expected[month][feature])
            maximum = max(maximum, difference)
            if difference > tolerance:
                mismatches += 1
    return mismatches, maximum


def main() -> None:
    args = parse_args()
    config = load_config()
    text_config = config["text_index"]
    if args.month:
        first = last = normalize_month(args.month)
    elif args.range:
        first, last = (normalize_month(value) for value in args.range)
    else:
        first = config["data"]["test_start_date"]
        last = config["data"]["test_end_date"]
    months = month_range(first, last)
    features = feature_keys(config)
    rows = read_database_rows(first, last, args.table)
    values = calculate_indices(
        rows,
        features,
        months,
        float(text_config["confidence_threshold"]),
        int(text_config["score_start"]),
        int(text_config["score_end"]),
    )

    if len(months) == 1:
        print(json.dumps({"month": first, **values[first]}, ensure_ascii=False, indent=2))
    output = args.output
    if output is None and len(months) > 1:
        output = resolve_path("data/text_index/realtext_actual_from_db.csv")
    if output is not None:
        write_csv(output.resolve(), months, features, values)
        print(f"Wrote database text index to {output.resolve()}", file=sys.stderr)

    if args.validate:
        reference = resolve_path(f"data/text_index/{ACTUAL_TABLE_NAME}")
        mismatches, maximum = validate_against_reference(
            reference, months, features, values, args.tolerance
        )
        print(
            json.dumps(
                {
                    "validation_reference": str(reference),
                    "compared_values": len(months) * len(features),
                    "mismatches": mismatches,
                    "max_absolute_difference": maximum,
                    "tolerance": args.tolerance,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        if mismatches:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
