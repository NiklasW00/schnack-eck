from __future__ import annotations

import logging
from pathlib import Path


class ResilientFileHandler(logging.FileHandler):
    def __init__(self, filename: Path, logger_name: str) -> None:
        super().__init__(filename, encoding="utf-8")
        self._logger_name = logger_name
        self._disabled_due_to_io_error = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._disabled_due_to_io_error:
            return

        try:
            super().emit(record)
        except OSError as exc:
            self._disabled_due_to_io_error = True

            logger = logging.getLogger(self._logger_name)
            logger.removeHandler(self)

            try:
                self.close()
            except Exception:
                pass

            logger.warning(
                "File logging disabled after I/O failure. Falling back to console/journal logging only. Reason: %s",
                exc,
            )


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

        file_handler = ResilientFileHandler(
            log_dir / "recorder.log",
            logger_name="schnack_eck",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except Exception as exc:
        logger.warning(
            "File logging unavailable for %s. Falling back to console/journal logging only. Reason: %s",
            log_dir,
            exc,
        )

    return logger