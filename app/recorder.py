from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd


class Recorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width_bytes: int = 2,
        chunk_duration_seconds: int = 30,
        device: int | None = None,
    ) -> None:
        if sample_width_bytes != 2:
            raise ValueError("This recorder currently supports 16-bit PCM only.")

        if chunk_duration_seconds <= 0:
            raise ValueError("chunk_duration_seconds must be > 0.")

        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width_bytes = sample_width_bytes
        self.chunk_duration_seconds = chunk_duration_seconds
        self.device = device

        self._is_recording = False

        self._audio_queue: queue.Queue[bytes] | None = None
        self._writer_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._stream: Optional[sd.InputStream] = None
        self._wave_file: Optional[wave.Wave_write] = None
        self._exception: Optional[Exception] = None

        self._current_chunk_path: Optional[Path] = None
        self._chunk_started_at: Optional[float] = None

        self._get_initial_chunk_path: Optional[Callable[[], Path]] = None
        self._rotate_chunk: Optional[Callable[[], Path]] = None

    def start(
        self,
        get_initial_chunk_path: Callable[[], Path],
        rotate_chunk: Callable[[], Path],
    ) -> None:
        if self._is_recording:
            raise RuntimeError("Recorder is already running.")

        self._audio_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._exception = None
        self._get_initial_chunk_path = get_initial_chunk_path
        self._rotate_chunk = rotate_chunk

        try:
            self._open_initial_chunk()

            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name="audio-writer",
                daemon=True,
            )
            self._writer_thread.start()

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=self._audio_callback,
                device=self.device,
            )
            self._stream.start()
            self._is_recording = True

        except Exception:
            self._cleanup_on_failed_start()
            raise

    def _audio_callback(self, indata, frames, callback_time, status) -> None:
        if status:
            self._exception = RuntimeError(f"Audio input status error: {status}")

        if self._audio_queue is not None:
            self._audio_queue.put(bytes(indata))

    def _writer_loop(self) -> None:
        assert self._audio_queue is not None
        assert self._stop_event is not None

        try:
            while not self._stop_event.is_set() or not self._audio_queue.empty():
                try:
                    audio_bytes = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                self._rotate_chunk_if_needed()
                self._write_audio_bytes(audio_bytes)

        except Exception as exc:
            self._exception = exc

    def _rotate_chunk_if_needed(self) -> None:
        if self._chunk_started_at is None:
            raise RuntimeError("Chunk start time is not initialized.")

        elapsed = time.monotonic() - self._chunk_started_at
        if elapsed < self.chunk_duration_seconds:
            return

        self._rotate_to_next_chunk()

    def _open_initial_chunk(self) -> None:
        if self._get_initial_chunk_path is None:
            raise RuntimeError("Initial chunk path provider is not configured.")

        first_chunk_path = self._get_initial_chunk_path()
        self._open_chunk_at_path(first_chunk_path)

    def _rotate_to_next_chunk(self) -> None:
        if self._rotate_chunk is None:
            raise RuntimeError("Chunk rotation callback is not configured.")

        self._close_current_chunk()

        next_chunk_path = self._rotate_chunk()
        self._open_chunk_at_path(next_chunk_path)

    def _open_chunk_at_path(self, chunk_path: Path) -> None:
        chunk_path.parent.mkdir(parents=True, exist_ok=True)

        self._wave_file = wave.open(str(chunk_path), "wb")
        self._wave_file.setnchannels(self.channels)
        self._wave_file.setsampwidth(self.sample_width_bytes)
        self._wave_file.setframerate(self.sample_rate)

        self._current_chunk_path = chunk_path
        self._chunk_started_at = time.monotonic()

    def _close_current_chunk(self) -> None:
        if self._wave_file is not None:
            self._wave_file.close()
            self._wave_file = None

        self._current_chunk_path = None
        self._chunk_started_at = None

    def _write_audio_bytes(self, audio_bytes: bytes) -> None:
        if self._wave_file is None:
            raise RuntimeError("No open WAV file available for writing.")

        self._wave_file.writeframes(audio_bytes)

    def stop(self) -> None:
        if not self._is_recording:
            raise RuntimeError("Recorder is not running.")

        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            if self._stop_event is not None:
                self._stop_event.set()

            if self._writer_thread is not None:
                self._writer_thread.join(timeout=5)

            self._close_current_chunk()

            if self._exception is not None:
                exc = self._exception
                self._exception = None
                raise RuntimeError(f"Recording failed: {exc}") from exc

        finally:
            self._is_recording = False
            self._audio_queue = None
            self._writer_thread = None
            self._stop_event = None
            self._get_initial_chunk_path = None
            self._rotate_chunk = None
            self._current_chunk_path = None
            self._chunk_started_at = None

    def is_recording(self) -> bool:
        return self._is_recording

    def check_runtime_health(self) -> None:
        if self._exception is not None:
            exc = self._exception
            self._exception = None
            raise RuntimeError(f"Recorder runtime error: {exc}") from exc

    def _cleanup_on_failed_start(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        finally:
            self._stream = None

        try:
            self._close_current_chunk()
        finally:
            pass

        self._is_recording = False
        self._audio_queue = None
        self._writer_thread = None
        self._stop_event = None
        self._get_initial_chunk_path = None
        self._rotate_chunk = None
        self._exception = None