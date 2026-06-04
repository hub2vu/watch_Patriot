from __future__ import annotations

import logging
from pathlib import Path

from .config import AppConfig


def setup_logging(config: AppConfig) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.log_dir / "dc_watch.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def log_file_path(config: AppConfig) -> Path:
    return config.log_dir / "dc_watch.log"
