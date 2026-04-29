from __future__ import annotations

from pathlib import Path

from app.models import SystemState


def count_files(root: Path, suffix: str) -> int:
    return sum(1 for _ in root.rglob(f"*{suffix}"))


def test_boot_goes_to_ready(machine) -> None:
    machine.boot()
    assert machine.state == SystemState.READY


def test_single_recording_cycle_creates_session_and_review(machine, config) -> None:
    machine.boot()

    machine.handle_button_press()
    assert machine.state == SystemState.RECORDING

    machine.handle_button_press()
    assert machine.state == SystemState.READY

    wav_count_sessions = count_files(config.sessions_dir, ".wav")
    json_count_sessions = count_files(config.sessions_dir, ".json")

    wav_count_review = count_files(config.review_dir, ".wav")
    json_count_review = count_files(config.review_dir, ".json")

    assert wav_count_sessions == 1
    assert json_count_sessions == 1
    assert wav_count_review == 1
    assert json_count_review == 1


def test_chunk_rotation_creates_multiple_chunks(machine, config) -> None:
    machine.boot()

    machine.handle_button_press()
    assert machine.state == SystemState.RECORDING

    machine.recorder.rotate_once()
    machine.recorder.rotate_once()
    machine.recorder.rotate_once()

    machine.handle_button_press()
    assert machine.state == SystemState.READY

    session_chunk_files = sorted(config.sessions_dir.rglob("*.wav"))
    review_files = sorted(config.review_dir.rglob("*.wav"))

    assert len(session_chunk_files) == 4
    assert len(review_files) == 1


def test_start_failure_goes_to_error_and_recovers(machine) -> None:
    machine.boot()

    machine.recorder.fail_on_start = True
    machine.handle_button_press()

    assert machine.state == SystemState.ERROR

    machine.recorder.fail_on_start = False
    machine.try_recover()

    assert machine.state == SystemState.READY


def test_stop_failure_goes_to_error_and_recovers(machine) -> None:
    machine.boot()

    machine.handle_button_press()
    assert machine.state == SystemState.RECORDING

    machine.recorder.fail_on_stop = True
    machine.handle_button_press()

    assert machine.state == SystemState.ERROR

    machine.recorder.fail_on_stop = False
    machine.try_recover()

    assert machine.state == SystemState.READY


def test_runtime_failure_goes_to_error(machine) -> None:
    machine.boot()

    machine.handle_button_press()
    assert machine.state == SystemState.RECORDING

    machine.recorder.fail_runtime = True
    machine.poll_runtime_health()

    assert machine.state == SystemState.ERROR