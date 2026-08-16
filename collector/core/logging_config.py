"""Collector実行ログの共通設定。

要求されるログ項目（collector名、開始/終了時刻、取得件数、新規/更新/エラー件数、
エラー内容）は core.models.RunStats が保持し、run_due.py がこのロガーと
collector_runs テーブルの両方へ書き出す（DB書き込みはPhase 3以降）。
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def get_logger(collector_key: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"collector.{collector_key}")
    if logger.handlers:
        return logger  # 二重登録防止

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOG_DIR / f"{collector_key}.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
