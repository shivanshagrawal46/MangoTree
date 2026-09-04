"""Structured logging with a console-friendly renderer."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def get_logger(name: str = "mangotree") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)-24s %(message)s", "%H:%M:%S"
        )

        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        logger.addHandler(stream)

        log_dir = Path(__file__).resolve().parents[2] / "logs"
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "mangotree.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

        logger.propagate = False
        _CONFIGURED = True
    return logger


logger = get_logger()
