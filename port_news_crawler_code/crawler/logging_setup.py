"""Centralised logging configuration.

Logs go both to stdout (so the operator can watch the loop live) and to a
rotating file under ``<log_dir>/crawler.log``.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False


def setup_logging(log_dir: str | Path, level: int = logging.INFO) -> logging.Logger:
    """Configure root logging once and return the package logger."""
    global _CONFIGURED
    logger = logging.getLogger("crawler")

    if _CONFIGURED:
        return logger

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path / "crawler.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    logger.setLevel(level)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    logger.propagate = False

    # Quiet down noisy third-party loggers.
    for noisy in ("selenium", "urllib3", "undetected_chromedriver", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return logger
