from __future__ import annotations

import json

from app.models import SystemState
from tests.conftest import write_test_wav


def test_boot_recovers_session_with_valid_chunks_and_incomplete_chunk(machine, config) -> None:
    storage = machine.storage

    session = storage.create_session()

    chunk1 = session.chunks_dir / "000001.wav"
    chunk2 = session.chunks_dir / "000002.wav"
    chunk3 = session.chunks_dir / "000003.wav"

    write_test_wav(chunk1)
    write_test_wav(chunk2)
    write_test_wav(chunk3)

    incomplete = session.chunks_dir / "000004.wav.part"
    incomplete.write_bytes(b"incomplete")

    machine.boot()

    assert machine.state == SystemState.READY

    recovered_audio = config.review_dir / session.started_at.strftime("%Y-%m-%d") / f"{session.session_id}_session_recovered.wav"
    recovered_json = config.review_dir / session.started_at.strftime("%Y-%m-%d") / f"{session.session_id}_session_recovered.json"

    assert recovered_audio.exists()
    assert recovered_json.exists()

    with recovered_json.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    assert payload["status"] == "recovered_partial"
    assert payload["source"]["chunk_count_total"] == 4
    assert payload["source"]["chunk_count_merged"] == 3
    assert "chunks/000004.wav.part" in [p.replace("\\", "/") for p in payload["source"]["excluded_chunks"]]


def test_boot_does_not_recover_clean_completed_session(machine, config) -> None:
    machine.boot()

    machine.handle_button_press()
    machine.handle_button_press()

    assert machine.state == SystemState.READY

    review_files_before = sorted(config.review_dir.rglob("*_recovered.wav"))

    machine.boot()

    review_files_after = sorted(config.review_dir.rglob("*_recovered.wav"))

    assert review_files_before == review_files_after


def test_recovery_archives_incomplete_chunks(machine, config) -> None:
    storage = machine.storage

    session = storage.create_session()

    chunk1 = session.chunks_dir / "000001.wav"
    write_test_wav(chunk1)

    incomplete = session.chunks_dir / "000002.wav.part"
    incomplete.write_bytes(b"incomplete")

    machine.boot()

    archived = sorted(config.recovered_dir.glob(f"*__{session.session_id}__000002.wav.part"))
    assert len(archived) == 1


def test_manual_recover_from_error_runs_startup_recovery(machine, config) -> None:
    storage = machine.storage

    session = storage.create_session()
    chunk1 = session.chunks_dir / "000001.wav"
    write_test_wav(chunk1)

    incomplete = session.chunks_dir / "000002.wav.part"
    incomplete.write_bytes(b"incomplete")

    machine.state = SystemState.ERROR
    machine.try_recover()

    assert machine.state == SystemState.READY

    recovered_audio = config.review_dir / session.started_at.strftime("%Y-%m-%d") / f"{session.session_id}_session_recovered.wav"
    assert recovered_audio.exists()