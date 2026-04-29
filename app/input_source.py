from __future__ import annotations

import queue
import threading
from abc import ABC, abstractmethod
from typing import Optional


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