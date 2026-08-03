#!/usr/bin/env python3
"""One-off stats for data_from_server verification report."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data_from_server" / "articles"
if not ROOT.exists():
    ROOT = Path(__file__).resolve().parents[1] / "data" / "articles"

FIELDS = ("url", "site", "title", "text", "published", "author", "language", "fetched_at", "raw_meta")


def main():
    files = list(ROOT.rglob("*.json"))
    stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "folder_dates": Counter(),
        "published_dates": Counter(),
        "fetched_dates": Counter(),
        "empty_text": 0,
        "empty_published": 0,
        "unknown_folder": 0,
        "missing_fields": Counter(),
        "nonempty_author": 0,
        "nonempty_raw_meta": 0,
    })
    titles = Counter()
    scmp_dirty: list[str] = []
    hket_paywall: list[str] = []
    manifold_titles = Counter()
    parse_ok = 0
    total_bytes = 0

    for fp in files:
        total_bytes += fp.stat().st_size
        site = fp.parts[-3]
        folder_date = fp.parts[-2]
        s = stats[site]
        s["count"] += 1
        s["folder_dates"][folder_date] += 1
        if folder_date == "unknown-date":
            s["unknown_folder"] += 1
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            parse_ok += 1
        except json.JSONDecodeError:
            continue
        for f in FIELDS:
            if f not in d:
                s["missing_fields"][f] += 1
        text = (d.get("text") or "").strip()
        title = (d.get("title") or "").strip()
        pub = d.get("published") or ""
        if not text:
            s["empty_text"] += 1
        if not pub:
            s["empty_published"] += 1
        if d.get("author"):
            s["nonempty_author"] += 1
        if d.get("raw_meta"):
            s["nonempty_raw_meta"] += 1
        if pub:
            s["published_dates"][str(pub)[:10]] += 1
        fetched = (d.get("fetched_at") or "")[:10]
        if fetched:
            s["fetched_dates"][fetched] += 1
        if title:
            titles[title] += 1
        if site == "scmp":
            low = (text + title).lower()
            if (
                not text
                or "new tab" in title.lower()
                or "chrome-error" in low
                or "err_" in low
                or title.endswith(".com")
            ):
                scmp_dirty.append(fp.name)
        if site == "manifold_times" and title:
            manifold_titles[title] += 1
        if site == "hket" and any(k in text for k in ("訂閱", "hketPRO", "解鎖全部")):
            hket_paywall.append(fp.name)

    print("TOTAL_FILES", len(files))
    print("PARSE_OK", parse_ok)
    print("TOTAL_SIZE_KB", round(total_bytes / 1024, 1))
    print("SITES", sorted(stats.keys()))

    all_fetched = Counter()
    all_folder = Counter()
    all_published = Counter()
    for site, s in stats.items():
        all_fetched.update(s["fetched_dates"])
        all_folder.update(s["folder_dates"])
        all_published.update(s["published_dates"])
        pub_dates = sorted(s["published_dates"])
        fold_dates = sorted(k for k in s["folder_dates"] if k != "unknown-date")
        fetch_dates = sorted(s["fetched_dates"])
        print("\n===", site, "===")
        print("count", s["count"])
        print("folder_date_count", len(s["folder_dates"]), "span", _span(fold_dates))
        print("published_date_count", len(s["published_dates"]), "span", _span(pub_dates))
        print("fetched_date_count", len(s["fetched_dates"]), "span", _span(fetch_dates))
        print("unknown_folder", s["unknown_folder"])
        print("empty_text", s["empty_text"], "empty_published", s["empty_published"])
        print("nonempty_author", s["nonempty_author"], "nonempty_raw_meta", s["nonempty_raw_meta"])
        print("folder_dist", dict(sorted(s["folder_dates"].items())))
        print("fetched_dist", dict(sorted(s["fetched_dates"].items())))
        print("published_dist_top", s["published_dates"].most_common(10))

    print("\n=== GLOBAL TIME ===")
    print("fetched_span", _span(sorted(all_fetched)))
    print("fetched_dist", dict(sorted(all_fetched.items())))
    print("folder_span", _span(sorted(k for k in all_folder if k != "unknown-date")))
    print("published_span", _span(sorted(all_published)))

    dup = [(t, c) for t, c in titles.items() if c > 1]
    print("\n=== DUP TITLES ===", len(dup))
    for t, c in sorted(dup, key=lambda x: -x[1])[:12]:
        print(c, t[:80])

    print("\n=== SCMP DIRTY ===", len(scmp_dirty), scmp_dirty)
    print("=== HKET PAYWALL ===", len(hket_paywall))
    mt_dup = sum(1 for c in manifold_titles.values() if c > 1)
    print("=== MANIFOLD unique titles ===", len(manifold_titles), "dup_groups", mt_dup)


def _span(dates: list) -> str:
    if not dates:
        return "N/A"
    return f"{dates[0]} ~ {dates[-1]}"


def avg_interval(dates):
    if len(dates) < 2:
        return None
    ds = sorted(set(dates))
    if len(ds) < 2:
        return None
    gaps = [(ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)]
    return sum(gaps) / len(gaps)


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def time_table():
    rows = []
    all_files = list(ROOT.rglob("*.json"))
    for site_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        site = site_dir.name
        files = list(site_dir.rglob("*.json"))
        pub_dates: list[datetime] = []
        fetch_dates: list[datetime] = []
        for fp in files:
            d = json.loads(fp.read_text(encoding="utf-8"))
            pd = parse_dt(d.get("published"))
            fd = parse_dt(d.get("fetched_at"))
            if pd:
                pub_dates.append(pd)
            if fd:
                fetch_dates.append(fd)
        rows.append((site, len(files), pub_dates, fetch_dates))

    print("\n=== TABLE A: published (news date) ===")
    for site, total, pub_dates, _ in rows:
        valid = len(pub_dates)
        if pub_dates:
            e = min(pub_dates).strftime("%Y/%m/%d")
            l = max(pub_dates).strftime("%Y/%m/%d")
            avg = avg_interval(pub_dates)
            avg_s = f"约{avg:.0f}天" if avg else "N/A"
        else:
            e = l = avg_s = "N/A"
        print(f"{site}\t{e}\t{l}\t{valid}/{total}\t{avg_s}")

    print("\n=== TABLE B: fetched (crawl date) ===")
    for site, total, _, fetch_dates in rows:
        uniq = sorted(set(fetch_dates))
        valid = len(fetch_dates)
        if fetch_dates:
            e = min(fetch_dates).strftime("%Y/%m/%d")
            l = max(fetch_dates).strftime("%Y/%m/%d")
            avg = avg_interval(fetch_dates)
            avg_s = f"约{avg:.0f}天" if avg else "N/A"
            uniq_s = f"{len(uniq)}/{total}"
        else:
            e = l = avg_s = uniq_s = "N/A"
        print(f"{site}\t{e}\t{l}\t{uniq_s}\t{avg_s}")

    all_pub = []
    all_fetch = []
    for _, _, pub_dates, fetch_dates in rows:
        all_pub.extend(pub_dates)
        all_fetch.extend(fetch_dates)
    print("\n=== GLOBAL published ===")
    print(
        min(all_pub).strftime("%Y/%m/%d"),
        max(all_pub).strftime("%Y/%m/%d"),
        f"{len(all_pub)}/{len(all_files)}",
        f"约{avg_interval(all_pub):.0f}天",
    )
    print("=== GLOBAL fetched ===")
    uf = sorted(set(all_fetch))
    print(
        min(all_fetch).strftime("%Y/%m/%d"),
        max(all_fetch).strftime("%Y/%m/%d"),
        f"{len(uf)}/{len(all_files)}",
        f"约{avg_interval(all_fetch):.0f}天",
    )
    from collections import Counter
    print("fetch_by_day", dict(Counter(d.strftime("%Y-%m-%d") for d in all_fetch)))


if __name__ == "__main__":
    main()
    time_table()
