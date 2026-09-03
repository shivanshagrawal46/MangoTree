"""Logger configuration using loguru."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def configure_logger(logs_dir: Path, run_id: str | None = None) -> None:
    """Configure loguru with console and file sinks."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>"
        ),
        colorize=True,
    )

    logs_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{run_id}" if run_id else ""
    logger.add(
        logs_dir / f"ingestion{suffix}.log",
        level="DEBUG",
        rotation="50 MB",
        retention=10,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )


__all__ = ["logger", "configure_logger"]
