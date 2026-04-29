from __future__ import annotations

from dataclasses import replace

import pytest

from app.health import HealthCheckError, HealthChecker


class DummyStream:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_resolve_input_device_returns_first_input_when_no_preference(config, monkeypatch) -> None:
    checker = HealthChecker(config)

    fake_devices = [
        {"name": "Output Only", "max_input_channels": 0},
        {"name": "USB Mic", "max_input_channels": 1},
        {"name": "Another Mic", "max_input_channels": 2},
    ]

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)

    device_id = checker.resolve_input_device()
    assert device_id == 1


def test_resolve_input_device_by_preferred_name(config, monkeypatch) -> None:
    config = replace(config, preferred_input_device_name="usb mic")
    checker = HealthChecker(config)

    fake_devices = [
        {"name": "Built-in Mic", "max_input_channels": 1},
        {"name": "USB Mic Pro", "max_input_channels": 1},
    ]

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)

    device_id = checker.resolve_input_device()
    assert device_id == 1


def test_resolve_input_device_raises_when_preferred_name_missing(config, monkeypatch) -> None:
    config = replace(config, preferred_input_device_name="definitely-not-there")
    checker = HealthChecker(config)

    fake_devices = [
        {"name": "Built-in Mic", "max_input_channels": 1},
    ]

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)

    with pytest.raises(HealthCheckError) as exc_info:
        checker.resolve_input_device()

    assert exc_info.value.error_code == "MICROPHONE_DEVICE_NOT_FOUND"


def test_check_audio_capture_readiness_uses_resolved_device(config, monkeypatch) -> None:
    checker = HealthChecker(config)

    fake_devices = [
        {"name": "USB Mic", "max_input_channels": 1},
    ]

    captured: dict[str, object] = {}

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)

    def fake_input_stream(**kwargs):
        captured.update(kwargs)
        return DummyStream()

    monkeypatch.setattr("app.health.sd.InputStream", fake_input_stream)

    checker.check_audio_capture_readiness()

    assert captured["device"] == 0
    assert captured["samplerate"] == config.sample_rate
    assert captured["channels"] == config.channels
    assert captured["dtype"] == "int16"


def test_check_audio_capture_readiness_raises_health_error(config, monkeypatch) -> None:
    checker = HealthChecker(config)

    fake_devices = [
        {"name": "USB Mic", "max_input_channels": 1},
    ]

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)

    def fake_input_stream(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.health.sd.InputStream", fake_input_stream)

    with pytest.raises(HealthCheckError) as exc_info:
        checker.check_audio_capture_readiness()

    assert exc_info.value.error_code == "MICROPHONE_CAPTURE_NOT_READY"


def test_get_operational_readiness_returns_ok(config, monkeypatch) -> None:
    checker = HealthChecker(config)
    checker.ensure_directories()
    checker.ensure_health_file()

    fake_devices = [
        {"name": "USB Mic", "max_input_channels": 1},
    ]

    monkeypatch.setattr("app.health.sd.query_devices", lambda: fake_devices)
    monkeypatch.setattr("app.health.sd.InputStream", lambda **kwargs: DummyStream())

    readiness = checker.get_operational_readiness()

    assert readiness.ok is True
    assert readiness.error_code is None
    assert readiness.message is None


def test_mark_error_writes_error_code_to_health_json(config) -> None:
    checker = HealthChecker(config)
    checker.ensure_directories()
    checker.ensure_health_file()

    checker.mark_error(
        "Audio input stream could not be opened.",
        error_code="MICROPHONE_CAPTURE_NOT_READY",
        usb_storage_available=True,
        storage_writable=True,
    )

    payload = checker.read_health()

    assert payload["last_error"] == "Audio input stream could not be opened."
    assert payload["last_error_code"] == "MICROPHONE_CAPTURE_NOT_READY"
    assert payload["usb_storage_available"] is True
    assert payload["storage_writable"] is True