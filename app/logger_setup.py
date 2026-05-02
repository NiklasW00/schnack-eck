from __future__ import annotations

import logging
from pathlib import Path


def setup_logger(log_dir: Path) -> logging.Logger:
    logger = logging.getLogger("schnack_eck")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_dir / "recorder.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except Exception as exc:
        logger.warning(
            "File logging unavailable for %s. Falling back to console/journal logging only. Reason: %s",
            log_dir,
            exc,
        )

    return logger