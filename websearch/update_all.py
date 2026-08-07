# -*- coding: utf-8 -*-
"""Update all trade data sources without allowing one failure to stop others."""
from __future__ import annotations

import argparse
import socket
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Callable, NamedTuple

import pandas as pd
import requests

import fetch_hongkong
import fetch_hongkong_port
import fetch_mainland_china
import fetch_mainland_china_english
import fetch_usa
import fetch_vietnam
from import_to_db import import_published_data
import regional_proxy

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
STAGE_DIR = HERE / "output"
OUTPUT_NAMES = (
    "mainlandChina.csv",
    "USA_Total.csv", "Honkong.csv", "Vietnam.csv", "HongKong_Port_Throughput.csv",
)


class PreflightResult(NamedTuple):
    label: str
    ok: bool
    detail: str


def _http_preflight(label: str, urls: tuple[str, ...]) -> PreflightResult:
    session = requests.Session()
    regional_proxy.configure_session(session)
    # mihomo 刚启动时 url-test 组尚未完成第一轮测速，首个请求可能落在坏节点上；
    # 重试并稍候，给测速留时间，避免预检误报。
    attempts = 3
    try:
        for attempt in range(1, attempts + 1):
            try:
                statuses = []
                for url in urls:
                    response = session.get(url, timeout=20)
                    if response.status_code >= 500:
                        raise requests.HTTPError(
                            f"{response.url} returned HTTP {response.status_code}",
                            response=response,
                        )
                    statuses.append(f"{response.url} -> HTTP {response.status_code}")
                return PreflightResult(label, True, "; ".join(statuses))
            except requests.RequestException as exc:
                if attempt == attempts:
                    return PreflightResult(label, False, f"{type(exc).__name__}: {exc}")
                time.sleep(5)
    finally:
        session.close()


def _mainland_china_preflight() -> PreflightResult:
    label = "Mainland China"
    # 与正式采集同序：中文站（全精度主来源）优先，英文站只作兜底探测。
    try:
        _, resolved, gateway = fetch_mainland_china.prepare_direct_bypass()
        ip = resolved["gdfs.customs.gov.cn"][0]
        with socket.create_connection((ip, 80), timeout=20):
            pass
        return PreflightResult(label, True, f"gdfs.customs.gov.cn -> {ip}:80 via {gateway}")
    except Exception as chinese_exc:
        try:
            content = fetch_mainland_china_english.http_get(
                fetch_mainland_china_english.LIST_URL_CURRENT, timeout=20, retries=1)
            if fetch_mainland_china_english.TABLE4_MARK.encode() not in content:
                raise RuntimeError("english monthly bulletin page lacks table (4) row")
            return PreflightResult(
                label, True,
                f"chinese direct bypass failed ({type(chinese_exc).__name__}: {chinese_exc}); "
                "english.customs.gov.cn bulletin reachable as fallback",
            )
        except Exception as english_exc:
            return PreflightResult(
                label, False,
                f"chinese: {type(chinese_exc).__name__}: {chinese_exc}; "
                f"english: {type(english_exc).__name__}: {english_exc}",
            )


def _vietnam_preflight() -> PreflightResult:
    label = "Vietnam"
    relay: fetch_vietnam.VietnamRelay | None = None
    try:
        config = fetch_vietnam.load_config(fetch_vietnam.DEFAULT_CONFIG)
        proxy_config = config.get("vietnam_proxy", {})
        preflight_config = config.get("preflight", {})
        if proxy_config.get("enabled", True):
            relay = fetch_vietnam.VietnamRelay(proxy_config).start()
            fetch_vietnam.preflight_check(relay, preflight_config)
            return PreflightResult(label, True, "Vietnam proxy and nso.gov.vn are reachable")

        test_url = preflight_config.get("test_url", "https://www.nso.gov.vn/en/")
        timeout = float(preflight_config.get("timeout_seconds", 40))
        request = urllib.request.Request(test_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(2048)
        return PreflightResult(label, True, f"direct connection to {test_url}")
    except Exception as exc:
        return PreflightResult(label, False, f"{type(exc).__name__}: {exc}")
    finally:
        if relay is not None:
            relay.stop()


def run_network_preflight(only: str | None = None) -> list[PreflightResult]:
    checks = {
        "mainland": _mainland_china_preflight,
        "usa": lambda: _http_preflight("USA", tuple(fetch_usa.BASE_URLS.values())),
        "hongkong": lambda: _http_preflight(
            "Hong Kong",
            (fetch_hongkong.HOME_URL,),
        ),
        "port": lambda: _http_preflight("Hong Kong port", (fetch_hongkong_port.HOME_URL,)),
        "vietnam": _vietnam_preflight,
    }
    selected = {only: checks[only]} if only else checks
    print(f"== Network preflight ({len(selected)} groups) ==")
    results = []
    with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="preflight") as pool:
        futures = [pool.submit(check) for check in selected.values()]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            marker = "OK" if result.ok else "FAILED"
            print(f"  [{marker}] {result.label}: {result.detail}")

    failed = [result.label for result in results if not result.ok]
    if failed:
        print(
            f"\nWARNING: Network preflight failed: {', '.join(failed)}; "
            "continuing with the remaining sources."
        )
    else:
        print("  All network groups are reachable.")
    return results


def compare_overlap(old_path: Path, new_path: Path, key_cols: list[str]) -> None:
    old = pd.read_csv(old_path, encoding="utf-8-sig")
    new = pd.read_csv(new_path, encoding="utf-8-sig")
    missing_keys = [column for column in key_cols if column not in old.columns or column not in new.columns]
    if missing_keys:
        print(f"  skipped {old_path.name}: output structure changed to {', '.join(new.columns)}")
        return
    merged = old.merge(new, on=key_cols, suffixes=("_old", "_new"))
    differences = 0
    value_columns = [
        column for column in old.columns
        if column not in key_cols and column in new.columns
    ]
    for column in value_columns:
        old_values = pd.to_numeric(merged[f"{column}_old"], errors="coerce")
        new_values = pd.to_numeric(merged[f"{column}_new"], errors="coerce")
        mask = ~old_values.sub(new_values).abs().le(0.5)
        for _, row in merged[mask].iterrows():
            key = ", ".join(str(row[key_column]) for key_column in key_cols)
            print(f"  revision [{key}] {column}: {row[f'{column}_old']} -> {row[f'{column}_new']}")
            differences += 1
    print(
        f"  compared {old_path.name}: overlap={len(merged)}, "
        f"revisions={differences}, added={len(new) - len(merged)}, removed={len(old) - len(merged)}"
    )


def _backup() -> None:
    if not DATA_DIR.exists():
        return
    backup_dir = HERE / "backup" / date.today().isoformat()
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_NAMES:
        source = DATA_DIR / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)
    print(f"Backed up existing data to {backup_dir}")


def _run_source(
    label: str,
    outputs: tuple[str, ...],
    operation: Callable[[], object],
    succeeded: set[str],
    failures: dict[str, str],
) -> None:
    print(f"== Update {label} ==")
    for name in outputs:
        (STAGE_DIR / name).unlink(missing_ok=True)
    try:
        operation()
        missing = [name for name in outputs if not (STAGE_DIR / name).exists()]
        if missing:
            raise RuntimeError(f"missing staged output: {', '.join(missing)}")
        succeeded.update(outputs)
    except Exception as exc:  # intentional per-source isolation boundary
        failures[label] = f"{type(exc).__name__}: {exc}"
        if all((STAGE_DIR / name).exists() for name in outputs):
            succeeded.update(outputs)
        print(f"  FAILED: {failures[label]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="write output/ without publishing data/")
    parser.add_argument("--refresh", choices=("auto", "all", "data"), default="data",
                        help="auto=用 cache/ 且近期重抓(默认); all=忽略缓存全量重抓; "
                             "data=以 data/ 现有 CSV 为缓存,已有月份一律不抓,只补缺月")
    parser.add_argument("--china-import-source", type=Path, action="append")
    parser.add_argument("--china-export-source", type=Path, action="append")
    parser.add_argument("--skip-db", action="store_true",
                        help="publish CSV files without loading successful outputs into MySQL")
    parser.add_argument("--only", choices=("mainland", "usa", "hongkong", "port", "vietnam"),
                        help="只更新并入库一个来源；默认更新全部来源")
    args = parser.parse_args(argv)

    run_network_preflight(args.only)
    if not args.dry_run:
        _backup()
    STAGE_DIR.mkdir(exist_ok=True)
    succeeded: set[str] = set()
    failures: dict[str, str] = {}

    refresh = args.refresh
    source_jobs: list[tuple[str, tuple[str, ...], Callable[[], object]]] = []
    if args.only in (None, "mainland"):
        source_jobs.append((
            "Mainland China (Customs HS 50-63)", ("mainlandChina.csv",),
            lambda: fetch_mainland_china.main(
                STAGE_DIR / "mainlandChina.csv", offline=args.dry_run,
                import_sources=args.china_import_source, export_sources=args.china_export_source,
                refresh_all=refresh == "all",
                existing_path=DATA_DIR / "mainlandChina.csv" if refresh == "data" else None,
            ),
        ))
    if args.only in (None, "usa"):
        source_jobs.append((
            "USA (Census HS 50-63)", ("USA_Total.csv",),
            lambda: fetch_usa.main(STAGE_DIR / "USA_Total.csv", refresh=refresh,
                                   existing_path=DATA_DIR / "USA_Total.csv"),
        ))
    if args.only in (None, "hongkong"):
        source_jobs.append((
            "Hongkong (IDDS SITC 65)", ("Honkong.csv",),
            lambda: fetch_hongkong.main(STAGE_DIR / "Honkong.csv", refresh=refresh,
                                        existing_path=DATA_DIR / "Honkong.csv"),
        ))
    if args.only in (None, "port"):
        source_jobs.append((
            "Hong Kong port throughput", ("HongKong_Port_Throughput.csv",),
            lambda: fetch_hongkong_port.main(STAGE_DIR / "HongKong_Port_Throughput.csv"),
        ))
    if args.only in (None, "vietnam"):
        source_jobs.append((
            "Vietnam (NSO/GSO textile groups)", ("Vietnam.csv",),
            lambda: fetch_vietnam.main(STAGE_DIR / "Vietnam.csv", preflight=False,
                                       refresh=refresh,
                                       existing_path=DATA_DIR / "Vietnam.csv"),
        ))

    if len(source_jobs) == 1:
        label, outputs, operation = source_jobs[0]
        _run_source(label, outputs, operation, succeeded, failures)
    else:
        print(f"== Parallel update ({len(source_jobs)} sources) ==")
        with ThreadPoolExecutor(max_workers=len(source_jobs), thread_name_prefix="source") as pool:
            futures = {
                pool.submit(_run_source, label, outputs, operation, succeeded, failures): label
                for label, outputs, operation in source_jobs
            }
            for future in as_completed(futures):
                # _run_source isolates expected source errors; this still surfaces an
                # unexpected executor/programming failure without cancelling siblings.
                try:
                    future.result()
                except Exception as exc:
                    label = futures[future]
                    failures[label] = f"{type(exc).__name__}: {exc}"
                    print(f"  FAILED: {label}: {failures[label]}")

    key_columns = {
        "USA_Total.csv": ["年月", "年", "月"],
        "Honkong.csv": ["年月", "年", "月"],
        "mainlandChina.csv": ["年月", "年", "月"],
        "Vietnam.csv": ["年月", "年", "月"],
        "HongKong_Port_Throughput.csv": ["年月", "年", "月"],
    }
    print("== Compare with existing data ==")
    for name in sorted(succeeded):
        old_path, new_path = DATA_DIR / name, STAGE_DIR / name
        if old_path.exists():
            compare_overlap(old_path, new_path, key_columns[name])

    if not args.dry_run:
        DATA_DIR.mkdir(exist_ok=True)
        for name in sorted(succeeded):
            shutil.copy2(STAGE_DIR / name, DATA_DIR / name)
        print(f"Published {len(succeeded)} successful files to {DATA_DIR}")
        if not args.skip_db and succeeded:
            print("== Import published data to MySQL ==")
            try:
                imported = import_published_data(DATA_DIR, succeeded)
                print(f"Imported {len(imported)} tables: {', '.join(imported)}")
            except Exception as exc:
                failures["MySQL import"] = f"{type(exc).__name__}: {exc}"
                print(f"  FAILED: {failures['MySQL import']}")
    else:
        print(f"dry-run: staged successful files in {STAGE_DIR}")

    print("== Update summary ==")
    print(f"  succeeded files: {', '.join(sorted(succeeded)) or 'none'}")
    for label, error in failures.items():
        print(f"  failed source: {label}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
