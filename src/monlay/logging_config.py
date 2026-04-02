"""
Centralized logging setup for monlay.

Provides a single ``setup_logging()`` function that configures the root
logger with appropriate formatting for interactive CLI use, systemd
journal, and optional file logging.
"""

from __future__ import annotations

import logging
import os
import sys


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """
    Configure the root logger for monlay.

    - When running under systemd (``JOURNAL_STREAM`` is set), timestamps
      are omitted because journald adds its own.
    - Otherwise a full ``asctime [LEVEL] name: message`` format is used.
    - An optional *log_file* adds a secondary file handler (always with
      timestamps, always DEBUG level) for diagnostics.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        log_file: If given, also write logs to this file at DEBUG level.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(min(numeric_level, logging.DEBUG) if log_file else numeric_level)

    # Remove any pre-existing handlers (e.g. from basicConfig in daemon)
    root.handlers.clear()

    # --- stderr handler ---
    if os.environ.get("JOURNAL_STREAM"):
        # Running under systemd: journald already adds timestamps
        fmt = "[%(levelname)s] %(name)s: %(message)s"
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(numeric_level)
    stderr_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(stderr_handler)

    # --- optional file handler ---
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
