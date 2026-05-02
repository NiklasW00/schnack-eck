from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import sounddevice as sd

from app.config import Config


@dataclass(frozen=True)
class ReadinessResult:
    ok: bool
    error_code: str | None = None
    message: str | None = None


class HealthCheckError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class HealthChecker:
    def __init__(self, config: Config):
        self.config = config
    
    def _can_write_to_data_root(self) -> bool:
        test_file = self.config.data_root / ".health_write_test.tmp"

        try:
            self.config.data_root.mkdir(parents=True, exist_ok=True)
            with test_file.open("w", encoding="utf-8") as f:
                f.write("ok")
            test_file.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def _get_active_health_path(self) -> Path:
        if self._can_write_to_data_root():
            return self.config.health_path

        self.config.fallback_health_path.parent.mkdir(parents=True, exist_ok=True)
        return self.config.fallback_health_path

    def ensure_directories(self) -> None:
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)

        if not self._can_write_to_data_root():
            return

        self.config.data_root.mkdir(parents=True, exist_ok=True)
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.config.review_dir.mkdir(parents=True, exist_ok=True)
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config.recovered_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------
    # Storage readiness
    # -----------------------------

    def check_storage_exists(self) -> None:
        if not self.config.data_root.exists():
            raise HealthCheckError(
                "STORAGE_MISSING",
                f"Storage path does not exist: {self.config.data_root}",
            )

    def check_storage_writable(self) -> None:
        test_file = self.config.data_root / ".write_test.tmp"

        try:
            self.config.data_root.mkdir(parents=True, exist_ok=True)
            with test_file.open("w", encoding="utf-8") as f:
                f.write("ok")
            test_file.unlink(missing_ok=True)
        except Exception as exc:
            raise HealthCheckError(
                "STORAGE_NOT_WRITABLE",
                f"Storage path is not writable: {self.config.data_root}",
            ) from exc

    def check_storage_space(self) -> None:
        usage = shutil.disk_usage(self.config.data_root)
        free_mb = usage.free / (1024 * 1024)

        if free_mb < self.config.min_free_space_mb:
            raise HealthCheckError(
                "STORAGE_LOW_SPACE",
                f"Insufficient free space: {free_mb:.1f} MB available, "
                f"{self.config.min_free_space_mb} MB required.",
            )

    def check_storage_readiness(self) -> None:
        self.check_storage_exists()
        self.check_storage_writable()
        self.check_storage_space()

    # -----------------------------
    # Audio presence
    # -----------------------------
            
    
    def resolve_input_device(self) -> int:
        try:
            devices = sd.query_devices()
        except Exception as exc:
            raise HealthCheckError(
                "MICROPHONE_QUERY_FAILED",
                "Failed to query audio input devices.",
            ) from exc

        input_devices: list[tuple[int, dict[str, Any]]] = [
            (index, device)
            for index, device in enumerate(devices)
            if device["max_input_channels"] > 0
        ]

        if not input_devices:
            raise HealthCheckError(
                "MICROPHONE_MISSING",
                "No input audio device available.",
            )

        if self.config.input_device_id is not None:
            for index, _device in input_devices:
                if index == self.config.input_device_id:
                    return index

            raise HealthCheckError(
                "MICROPHONE_DEVICE_NOT_FOUND",
                f"Configured input device id not found: {self.config.input_device_id}",
            )

        if self.config.preferred_input_device_name:
            preferred = self.config.preferred_input_device_name.lower()

            for index, device in input_devices:
                device_name = str(device.get("name", "")).lower()
                if preferred in device_name:
                    return index

            raise HealthCheckError(
                "MICROPHONE_DEVICE_NOT_FOUND",
                f"No input device matching preferred name: {self.config.preferred_input_device_name}",
            )

        return input_devices[0][0]
    
    def check_audio_presence(self) -> None:
        self.resolve_input_device()

    # -----------------------------
    # Audio capture readiness
    # -----------------------------

    def check_audio_capture_readiness(self) -> None:
        """
        Prüft, ob ein InputStream mit der echten Konfiguration und dem
        aufgelösten Eingabegerät geöffnet werden kann.
        """
        device_id = self.resolve_input_device()

        try:
            stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype="int16",
                device=device_id,
            )
            stream.start()
            stream.stop()
            stream.close()
        except Exception as exc:
            raise HealthCheckError(
                "MICROPHONE_CAPTURE_NOT_READY",
                f"Audio input stream could not be opened for device {device_id}: {exc}",
            ) from exc

    # -----------------------------
    # Combined readiness
    # -----------------------------

    def check_operational_readiness(self) -> None:
        self.check_storage_readiness()
        self.check_audio_presence()
        self.check_audio_capture_readiness()

    def get_operational_readiness(self) -> ReadinessResult:
        try:
            self.check_operational_readiness()
            return ReadinessResult(ok=True)
        except HealthCheckError as exc:
            return ReadinessResult(
                ok=False,
                error_code=exc.error_code,
                message=exc.message,
            )

    def boot_checks(self) -> None:
        self.ensure_directories()
        self.ensure_health_file()
        self.check_operational_readiness()
        self.update_health(
            usb_storage_available=True,
            storage_writable=True,
        )

    def pre_record_checks(self) -> None:
        self.check_operational_readiness()
        self.update_health(
            usb_storage_available=True,
            storage_writable=True,
        )

    # -----------------------------
    # health.json
    # -----------------------------

    def ensure_health_file(self) -> None:
        health_path = self._get_active_health_path()
        health_path.parent.mkdir(parents=True, exist_ok=True)

        if health_path.exists():
            return

        payload = {
            "schema_version": 1,
            "last_boot": None,
            "last_ready": None,
            "last_error": None,
            "last_error_code": None,
            "last_error_time": None,
            "last_session_id": None,
            "last_session_status": None,
            "usb_storage_available": False,
            "storage_writable": False,
            "recording_active": False,
            "shutdown_requested_at": None,
            "shutdown_completed_at": None,
        }

        with health_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def read_health(self) -> dict[str, Any]:
        self.ensure_health_file()
        health_path = self._get_active_health_path()

        with health_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def write_health(self, payload: dict[str, Any]) -> None:
        health_path = self._get_active_health_path()
        health_path.parent.mkdir(parents=True, exist_ok=True)

        with health_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def update_health(self, **updates: Any) -> None:
        payload = self.read_health()
        payload.update(updates)
        self.write_health(payload)

    def mark_boot(self, usb_storage_available: bool) -> None:
        self.update_health(
            last_boot=datetime.now().isoformat(),
            usb_storage_available=usb_storage_available,
        )

    def mark_ready(self) -> None:
        self.update_health(
            last_ready=datetime.now().isoformat(),
            recording_active=False,
        )

    def mark_recording_started(self, session_id: str) -> None:
        self.update_health(
            last_session_id=session_id,
            recording_active=True,
        )

    def mark_recording_stopped(self, session_id: str, session_status: str) -> None:
        self.update_health(
            last_session_id=session_id,
            last_session_status=session_status,
            recording_active=False,
        )

    def mark_error(
        self,
        message: str,
        error_code: str = "UNKNOWN_ERROR",
        *,
        usb_storage_available: bool | None = None,
        storage_writable: bool | None = None,
    ) -> None:
        updates: dict[str, Any] = {
            "last_error": message,
            "last_error_code": error_code,
            "last_error_time": datetime.now().isoformat(),
            "recording_active": False,
        }

        if usb_storage_available is not None:
            updates["usb_storage_available"] = usb_storage_available

        if storage_writable is not None:
            updates["storage_writable"] = storage_writable

        self.update_health(**updates)

    def mark_shutdown_requested(self) -> None:
        self.update_health(
            shutdown_requested_at=datetime.now().isoformat(),
        )

    def mark_shutdown_completed(self) -> None:
        self.update_health(
            shutdown_completed_at=datetime.now().isoformat(),
            recording_active=False,
        )