from __future__ import annotations

from app.health import ReadinessResult
from app.models import SystemState


def test_poll_error_recovery_stays_in_error_when_not_ready(machine, monkeypatch) -> None:
    machine.state = SystemState.ERROR

    monkeypatch.setattr(
        machine.health,
        "get_operational_readiness",
        lambda: ReadinessResult(
            ok=False,
            error_code="MICROPHONE_CAPTURE_NOT_READY",
            message="still not ready",
        ),
    )

    machine.poll_error_recovery()

    assert machine.state == SystemState.ERROR


def test_poll_error_recovery_returns_to_ready_when_ready(machine, monkeypatch) -> None:
    machine.state = SystemState.ERROR

    monkeypatch.setattr(
        machine.health,
        "get_operational_readiness",
        lambda: ReadinessResult(ok=True),
    )

    machine.poll_error_recovery()

    assert machine.state == SystemState.READY


def test_start_recording_uses_resolved_input_device(machine, monkeypatch) -> None:
    machine.boot()

    monkeypatch.setattr(machine.health, "resolve_input_device", lambda: 7)

    machine.handle_button_press()

    assert machine.state == SystemState.RECORDING
    assert machine.recorder.device == 7