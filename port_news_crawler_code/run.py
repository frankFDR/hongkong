#!/usr/bin/env python3
"""Entrypoint for the Port News Crawler.

Usage:
    python run.py                 # run forever (continuous polling)
    python run.py --once          # run a single cycle over all sites
    python run.py --site cnn      # restrict to one site (repeatable)
    python run.py --config other.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

from crawler.pipeline import Crawler


def main():
    ap = argparse.ArgumentParser(description="Continuous Selenium news crawler")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"),
                    help="path to config.yaml")
    ap.add_argument("--once", action="store_true",
                    help="run a single crawl cycle then exit")
    ap.add_argument("--site", action="append", default=None,
                    help="only crawl this site name (repeatable)")
    args = ap.parse_args()

    crawler = Crawler(args.config)

    if args.site:
        wanted = set(args.site)
        crawler.sites = [s for s in crawler.sites if s.name in wanted]
        if not crawler.sites:
            raise SystemExit(f"No matching enabled sites for {sorted(wanted)}")

    if args.once:
        crawler.run_once()
    else:
        crawler.run_forever()


if __name__ == "__main__":
    main()
