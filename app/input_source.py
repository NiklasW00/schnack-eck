from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from gpiozero import Button


class InputSource(ABC):
    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def get_next_command(self) -> Optional[str]:
        pass


class ConsoleInputSource(InputSource):
    def __init__(self) -> None:
        self._command_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._input_worker,
            name="console-input",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def get_next_command(self) -> Optional[str]:
        try:
            return self._command_queue.get_nowait()
        except queue.Empty:
            return None

    def _input_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                cmd = input("> ").strip().lower()
            except EOFError:
                break
            except KeyboardInterrupt:
                self._command_queue.put("q")
                break

            if cmd:
                self._command_queue.put(cmd)


class GPIOInputSource(InputSource):
    def __init__(
        self,
        button_gpio: int = 4,
        bounce_time: float = 0.05,
        shutdown_hold_seconds: float = 15.0,
    ) -> None:
        self.button_gpio = button_gpio
        self.bounce_time = bounce_time
        self.shutdown_hold_seconds = shutdown_hold_seconds

        self._command_queue: queue.Queue[str] = queue.Queue()
        self._button: Button | None = None

        self._pressed_at: float | None = None
        self._long_press_fired = False

    def start(self) -> None:
        if self._button is not None:
            return

        self._button = Button(
            self.button_gpio,
            pull_up=True,
            bounce_time=self.bounce_time,
        )
        self._button.when_pressed = self._on_pressed
        self._button.when_released = self._on_released
        self._button.when_held = self._on_held
        self._button.hold_time = self.shutdown_hold_seconds

    def stop(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None

        self._pressed_at = None
        self._long_press_fired = False

    def get_next_command(self) -> Optional[str]:
        try:
            return self._command_queue.get_nowait()
        except queue.Empty:
            return None

    def _on_pressed(self) -> None:
        self._pressed_at = time.monotonic()
        self._long_press_fired = False

    def _on_held(self) -> None:
        if self._long_press_fired:
            return

        self._command_queue.put("s")
        self._long_press_fired = True

    def _on_released(self) -> None:
        if self._pressed_at is None:
            return

        pressed_duration = time.monotonic() - self._pressed_at
        self._pressed_at = None

        if self._long_press_fired:
            self._long_press_fired = False
            return

        if pressed_duration < self.shutdown_hold_seconds:
            self._command_queue.put("r")