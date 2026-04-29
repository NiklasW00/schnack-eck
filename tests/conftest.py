from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.config import Config
from app.health import HealthChecker
from app.logger_setup import setup_logger
from app.models import SystemState
from app.state_machine import RecorderStateMachine
from app.status_led import StatusIndicator
from app.storage import StorageManager


def write_test_wav(
    path: Path,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width_bytes: int = 2,
    frames: int = 100,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width_bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00" * frames * channels * sample_width_bytes)


class FakeRecorder:
    def __init__(self) -> None:
        self._is_recording = False
        self._current_path: Path | None = None

        self.fail_on_start = False
        self.fail_on_stop = False
        self.fail_runtime = False

        self._get_initial_chunk_path = None
        self._rotate_chunk = None

        self.device: int | None = None

    def start(self, get_initial_chunk_path, rotate_chunk) -> None:
        if self.fail_on_start:
            raise RuntimeError("Simulated start failure")
        if self._is_recording:
            raise RuntimeError("Recorder already running")

        self._get_initial_chunk_path = get_initial_chunk_path
        self._rotate_chunk = rotate_chunk

        first_path = self._get_initial_chunk_path()
        write_test_wav(first_path)
        self._current_path = first_path
        self._is_recording = True

    def rotate_once(self) -> None:
        if not self._is_recording:
            raise RuntimeError("Recorder not running")
        if self._rotate_chunk is None:
            raise RuntimeError("No rotate callback configured")

        next_path = self._rotate_chunk()
        write_test_wav(next_path)
        self._current_path = next_path

    def stop(self) -> None:
        if self.fail_on_stop:
            raise RuntimeError("Simulated stop failure")
        if not self._is_recording:
            raise RuntimeError("Recorder not running")

        self._is_recording = False
        self._current_path = None
        self._get_initial_chunk_path = None
        self._rotate_chunk = None

    def is_recording(self) -> bool:
        return self._is_recording

    def check_runtime_health(self) -> None:
        if self.fail_runtime:
            raise RuntimeError("Simulated runtime failure")


class DummyStatusIndicator(StatusIndicator):
    def __init__(self) -> None:
        self.states: list[SystemState] = []
        self.error_codes: list[str | None] = []
        self.current_error_code: str | None = None

    def set_state(self, state: SystemState) -> None:
        self.states.append(state)

    def set_error(self, error_code: str | None) -> None:
        self.current_error_code = error_code
        self.error_codes.append(error_code)

    def clear_error(self) -> None:
        self.current_error_code = None


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path / "data",
        sessions_dir=tmp_path / "data" / "sessions",
        review_dir=tmp_path / "data" / "review",
        logs_dir=tmp_path / "data" / "logs",
        recovered_dir=tmp_path / "data" / "recovered",
        health_path=tmp_path / "data" / "health.json",
        sample_rate=16000,
        channels=1,
        sample_width_bytes=2,
        chunk_duration_seconds=3,
        max_session_duration_seconds=3600,
        min_free_space_mb=1,
        poll_interval_seconds=0.01,
        simulate_recorder=True,
        input_device_id=None,
        preferred_input_device_name=None,
    )


@pytest.fixture
def machine(config: Config) -> RecorderStateMachine:
    logger = setup_logger(config.logs_dir)
    health = HealthChecker(config)
    storage = StorageManager(config)
    recorder = FakeRecorder()
    status_indicator = DummyStatusIndicator()

    machine = RecorderStateMachine(
        config=config,
        logger=logger,
        health=health,
        storage=storage,
        recorder=recorder,
        status_indicator=status_indicator,
    )
    return machine