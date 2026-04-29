from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import SystemState


ERROR_BLINK_COUNTS: dict[str, int] = {
    "STORAGE_MISSING": 1,
    "STORAGE_NOT_WRITABLE": 2,
    "STORAGE_LOW_SPACE": 3,
    "MICROPHONE_MISSING": 4,
    "MICROPHONE_DEVICE_NOT_FOUND": 4,
    "MICROPHONE_CAPTURE_NOT_READY": 5,
    "MICROPHONE_QUERY_FAILED": 5,
    "RECOVERY_FAILED": 6,
    "REVIEW_EXPORT_FAILED": 6,
    "METADATA_WRITE_FAILED": 6,
    "UNKNOWN_ERROR": 7,
}


def get_error_blink_count(error_code: str | None) -> int:
    if error_code is None:
        return ERROR_BLINK_COUNTS["UNKNOWN_ERROR"]
    return ERROR_BLINK_COUNTS.get(error_code, ERROR_BLINK_COUNTS["UNKNOWN_ERROR"])


class StatusIndicator(ABC):
    @abstractmethod
    def set_state(self, state: SystemState) -> None:
        pass

    @abstractmethod
    def set_error(self, error_code: str | None) -> None:
        pass

    @abstractmethod
    def clear_error(self) -> None:
        pass


class ConsoleStatusIndicator(StatusIndicator):
    def __init__(self) -> None:
        self._last_error_code: str | None = None

    def set_state(self, state: SystemState) -> None:
        if state == SystemState.ERROR:
            blink_count = get_error_blink_count(self._last_error_code)
            error_code = self._last_error_code or "UNKNOWN_ERROR"
            print(f"[LED] State => ERROR | Code => {error_code} | Blink => {blink_count}x")
            return

        print(f"[LED] State => {state.value}")

    def set_error(self, error_code: str | None) -> None:
        self._last_error_code = error_code

    def clear_error(self) -> None:
        self._last_error_code = None