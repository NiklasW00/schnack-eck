from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from gpiozero import LED

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

    @abstractmethod
    def stop(self) -> None:
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

    def stop(self) -> None:
        pass


class GPIOStatusIndicator(StatusIndicator):
    def __init__(self, led_gpio: int = 17) -> None:
        self.led_gpio = led_gpio
        self._led = LED(self.led_gpio)

        self._last_error_code: str | None = None
        self._state: SystemState | None = None

        self._stop_event = threading.Event()
        self._pattern_event = threading.Event()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="gpio-status-indicator",
            daemon=True,
        )
        self._thread.start()

    def set_state(self, state: SystemState) -> None:
        self._state = state
        self._pattern_event.set()

    def set_error(self, error_code: str | None) -> None:
        self._last_error_code = error_code
        self._pattern_event.set()

    def clear_error(self) -> None:
        self._last_error_code = None
        self._pattern_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._pattern_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        self._led.off()
        self._led.close()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            state = self._state

            if state is None:
                self._led.off()
                self._wait_for_pattern_change(timeout=0.1)
                continue

            if state == SystemState.READY:
                self._led.on()
                self._wait_for_pattern_change(timeout=0.1)

            elif state == SystemState.BOOTING:
                self._run_blink(on_time=0.5, off_time=0.5)

            elif state == SystemState.STARTING_RECORDING:
                self._run_pulse_sequence(pulses=2, on_time=0.12, off_time=0.12, pause_after=0.5)

            elif state == SystemState.RECORDING:
                self._run_blink(on_time=0.15, off_time=1.85)

            elif state == SystemState.STOPPING_RECORDING:
                self._run_blink(on_time=0.12, off_time=0.12)

            elif state == SystemState.SAVING:
                self._run_blink(on_time=0.2, off_time=0.2)

            elif state == SystemState.SHUTTING_DOWN:
                self._run_blink(on_time=0.4, off_time=0.4)

            elif state == SystemState.ERROR:
                blink_count = get_error_blink_count(self._last_error_code)
                self._run_pulse_sequence(
                    pulses=blink_count,
                    on_time=0.2,
                    off_time=0.2,
                    pause_after=1.0,
                )

            else:
                self._led.off()
                self._wait_for_pattern_change(timeout=0.1)

    def _run_blink(self, on_time: float, off_time: float) -> None:
        self._led.on()
        if self._wait_for_pattern_change(timeout=on_time):
            return

        self._led.off()
        self._wait_for_pattern_change(timeout=off_time)

    def _run_pulse_sequence(
        self,
        pulses: int,
        on_time: float,
        off_time: float,
        pause_after: float,
    ) -> None:
        for _ in range(pulses):
            self._led.on()
            if self._wait_for_pattern_change(timeout=on_time):
                return

            self._led.off()
            if self._wait_for_pattern_change(timeout=off_time):
                return

        self._wait_for_pattern_change(timeout=pause_after)

    def _wait_for_pattern_change(self, timeout: float) -> bool:
        triggered = self._pattern_event.wait(timeout=timeout)
        if triggered:
            self._pattern_event.clear()
        return triggered or self._stop_event.is_set()