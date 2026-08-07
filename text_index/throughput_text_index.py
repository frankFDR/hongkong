#!/usr/bin/env python3
"""直接从 MySQL news_text 构建指定季度的吞吐量文本总指数。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

DATABASE_DIR = Path(__file__).resolve().parent.parent / "database"
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

from news_text_utils import _safe_table_name, engine


THROUGHPUT_KEYS = (
    "sea_import",
    "sea_export",
    "river_import",
    "river_export",
)


def _parse_quarter(quarter: str) -> tuple[str, datetime, datetime]:
    match = re.fullmatch(r"\s*(\d{4})\s*[-_]?\s*[Qq]([1-4])\s*", str(quarter))
    if not match:
        raise ValueError("quarter 必须采用 YYYYQ1～YYYYQ4 格式，例如 2025Q2")
    year, number = int(match.group(1)), int(match.group(2))
    start_month = (number - 1) * 3 + 1
    start = datetime(year, start_month, 1)
    if number == 4:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, start_month + 3, 1)
    return f"{year}Q{number}", start, end


def _decode_score(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else None
    return None


def aggregate_throughput_scores(
    rows: Iterable[dict[str, Any]],
    confidence_threshold: float = 0.3,
) -> tuple[float, float]:
    """
    聚合吞吐量得分，返回 (sea_index, river_index)
    
    复刻 build_st_nt_align.py 的 ST raw h1 聚合规则：
    - 使用下一季度新闻的 scores[0]
    - 过滤 confidence <= 0.3
    - Sea/River 各自除以非零 score 的维度数量
    """
    component_weighted_sum = {key: 0.0 for key in THROUGHPUT_KEYS}
    component_nonzero_count = {key: 0 for key in THROUGHPUT_KEYS}

    for row in rows:
        payload = _decode_score(row.get("throughput_score"))
        if not payload:
            continue
        
        for key in THROUGHPUT_KEYS:
            dimension = payload.get(key)
            if not isinstance(dimension, dict):
                continue
            scores = dimension.get("scores")
            try:
                confidence = float(dimension.get("confidence"))
                score_h1 = float(scores[0])
            except (TypeError, ValueError, IndexError):
                continue
            if confidence <= confidence_threshold:
                continue
            component_weighted_sum[key] += confidence * score_h1
            if score_h1 != 0:
                component_nonzero_count[key] += 1

    # 计算 Sea 指数
    sea_sum = component_weighted_sum["sea_import"] + component_weighted_sum["sea_export"]
    sea_count = component_nonzero_count["sea_import"] + component_nonzero_count["sea_export"]
    sea_index = sea_sum / max(sea_count, 1)
    
    # 计算 River 指数
    river_sum = component_weighted_sum["river_import"] + component_weighted_sum["river_export"]
    river_count = component_nonzero_count["river_import"] + component_nonzero_count["river_export"]
    river_index = river_sum / max(river_count, 1)
    
    return sea_index, river_index


def _next_quarter(quarter: str) -> str:
    normalized, _, _ = _parse_quarter(quarter)
    year, number = int(normalized[:4]), int(normalized[-1])
    return f"{year + 1}Q1" if number == 4 else f"{year}Q{number + 1}"


def build_quarter_throughput_text_index(
    quarter: str,
    table_name: str = "news_text",
    confidence_threshold: float = 0.3,
) -> dict[str, float]:
    """
    从 news_text 读取指定季度并计算吞吐量文本指数。
    
    返回: {"quarter": "2025Q2", "sea_index": 1.2345, "river_index": 0.6789}
    
    严格采用 build_st_nt_align.py 的 ST raw h1 规则：目标季度 Q 使用下一季度
    发表新闻的 scores[0]，过滤 confidence <= 0.3，Sea/River 各自除以非零
    score 的维度数量。如果对应季度没有数据，所有指数返回0。
    """
    normalized_quarter, _, _ = _parse_quarter(quarter)
    table_name = _safe_table_name(table_name)
    
    # 目标季度 Q 使用下一季度 (Q+1) 的新闻数据
    article_quarter = _next_quarter(normalized_quarter)
    _, start, end = _parse_quarter(article_quarter)
    
    statement = text(
        f"""
        SELECT `throughput_score`
        FROM `{table_name}`
        WHERE `timestamp` >= :start
          AND `timestamp` < :end
          AND `throughput_score` IS NOT NULL
          AND JSON_TYPE(`throughput_score`) = 'OBJECT'
        """
    )
    
    with engine.connect() as connection:
        rows = connection.execute(statement, {"start": start, "end": end}).mappings()
        sea_index, river_index = aggregate_throughput_scores(rows, confidence_threshold)
    
    return {
        "quarter": normalized_quarter,
        "sea_index": round(sea_index, 4),
        "river_index": round(river_index, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quarter", help="目标季度，例如 2025Q2")
    parser.add_argument("--table", default="news_text")
    parser.add_argument("--confidence-threshold", type=float, default=0.3)
    args = parser.parse_args()
    
    result = build_quarter_throughput_text_index(
        args.quarter,
        args.table,
        confidence_threshold=args.confidence_threshold,
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
