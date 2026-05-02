from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from typing import Optional

from app.config import Config
from app.health import HealthChecker, HealthCheckError
from app.models import Session, SessionStatus, SystemState
from app.recorder import Recorder
from app.status_led import StatusIndicator
from app.storage import StorageManager


class RecorderStateMachine:
    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        health: HealthChecker,
        storage: StorageManager,
        recorder: Recorder,
        status_indicator: StatusIndicator,
    ) -> None:
        self.config = config
        self.logger = logger
        self.health = health
        self.storage = storage
        self.recorder = recorder
        self.status_indicator = status_indicator

        self.state = SystemState.BOOTING
        self.current_session: Optional[Session] = None

        self._last_error_recovery_log_at: Optional[datetime] = None
        self._last_error_recovery_code: Optional[str] = None

    def set_state(self, new_state: SystemState) -> None:
        if self.state == new_state:
            return

        self.logger.info("State transition: %s -> %s", self.state.value, new_state.value)
        self.state = new_state

        if new_state != SystemState.ERROR:
            self.status_indicator.clear_error()

        self.status_indicator.set_state(new_state)

    def boot(self) -> None:
        try:
            self.health.boot_checks()
            self.health.mark_boot(usb_storage_available=True)

            self._run_startup_recovery()

            self.set_state(SystemState.READY)
            self.health.mark_ready()
            self.logger.info("System boot completed successfully.")
        except Exception as exc:
            self.handle_error("Boot failed", exc)

    def handle_button_press(self) -> None:
        if self.state == SystemState.READY:
            self.start_recording_flow()
        elif self.state == SystemState.RECORDING:
            self.stop_recording_flow()
        else:
            self.logger.warning("Button press ignored in state %s", self.state.value)

    def start_recording_flow(self) -> None:
        if self.state != SystemState.READY:
            self.logger.warning("Start request rejected in state %s", self.state.value)
            return

        self.set_state(SystemState.STARTING_RECORDING)

        try:
            self.health.pre_record_checks()
            session = self.storage.create_session()

            resolved_device_id = self.health.resolve_input_device()
            self.recorder.device = resolved_device_id

            self.recorder.start(
                get_initial_chunk_path=lambda: self.storage.get_next_chunk_temp_path(session),
                rotate_chunk=lambda: self.storage.finalize_current_chunk_and_get_next_temp_path(session),
            )

            self.current_session = session
            self.health.mark_recording_started(self.current_session.session_id)

            self.logger.info(
                "Recording started for session %s in %s",
                self.current_session.session_id,
                self.current_session.session_dir,
            )
            self.set_state(SystemState.RECORDING)

        except Exception as exc:
            self.current_session = None

            if not isinstance(exc, HealthCheckError):
                exc = HealthCheckError(
                    "MICROPHONE_CAPTURE_NOT_READY",
                    f"Audio input stream could not be opened: {exc}",
                )

            self.handle_error("Failed to start recording", exc)

    def stop_recording_flow(self) -> None:
        if self.state != SystemState.RECORDING:
            self.logger.warning("Stop request rejected in state %s", self.state.value)
            return

        if self.current_session is None:
            self.handle_error(
                "No active session during stop",
                RuntimeError("Missing session"),
            )
            return

        self.set_state(SystemState.STOPPING_RECORDING)

        try:
            self.recorder.stop()
            self.current_session.ended_at = datetime.now()
            self.current_session.duration_seconds = (
                self.current_session.ended_at - self.current_session.started_at
            ).total_seconds()

            self.set_state(SystemState.SAVING)
            self.finalize_current_session()

        except Exception as exc:
            self.storage.mark_session_interrupted(
                self.current_session,
                error_message=str(exc),
            )
            self.handle_error("Failed to stop recording", exc)

    def finalize_current_session(self) -> None:
        if self.current_session is None:
            self.handle_error(
                "Finalize called without active session",
                RuntimeError("Missing session"),
            )
            return

        try:
            if self.current_session.status == SessionStatus.IN_PROGRESS:
                self.current_session.status = SessionStatus.COMPLETED

            self.storage.finalize_current_chunk(self.current_session)
            self.storage.finalize_session_storage(self.current_session)

            review_size = self.storage.export_review_audio(
                session=self.current_session,
                sample_rate=self.config.sample_rate,
                channels=self.config.channels,
                recovered=False,
            )

            self.storage.write_metadata(
                self.current_session,
                sample_rate=self.config.sample_rate,
                channels=self.config.channels,
            )

            valid_chunk_count = len(self.storage.get_valid_chunk_paths(self.current_session))

            self.health.mark_recording_stopped(
                self.current_session.session_id,
                self.current_session.status.value,
            )

            self.logger.info(
                "Session %s saved successfully in %s (duration=%.2fs, chunk_count=%s, session_size=%s bytes, review_size=%s bytes, review_audio=%s)",
                self.current_session.session_id,
                self.current_session.session_dir,
                self.current_session.duration_seconds,
                valid_chunk_count,
                self.current_session.file_size_bytes,
                review_size,
                self.current_session.review_audio_path,
            )

            self.current_session = None
            self.set_state(SystemState.READY)
            self.health.mark_ready()

        except Exception as exc:
            if self.current_session is not None:
                self.current_session.status = SessionStatus.FAILED
                self.current_session.error_message = str(exc)
            self.handle_error("Failed to save session", exc)

    def poll_runtime_health(self) -> None:
        if self.state != SystemState.RECORDING:
            return

        if self.current_session is None:
            self.handle_error(
                "Runtime health check failed: missing active session",
                RuntimeError("Missing session during recording"),
            )
            return

        try:
            self.recorder.check_runtime_health()

            elapsed_seconds = (
                datetime.now() - self.current_session.started_at
            ).total_seconds()

            if elapsed_seconds >= self.config.max_session_duration_seconds:
                self.logger.warning(
                    "Max session duration reached for session %s after %.2f seconds. Auto-stopping recording.",
                    self.current_session.session_id,
                    elapsed_seconds,
                )
                self.current_session.status = SessionStatus.AUTO_STOPPED
                self.stop_recording_flow()

        except Exception as exc:
            self.storage.mark_session_interrupted(
                self.current_session,
                error_message=str(exc),
            )
            self.handle_error("Recorder runtime health check failed", exc)

    def poll_error_recovery(self) -> None:
        if self.state != SystemState.ERROR:
            self._last_error_recovery_log_at = None
            self._last_error_recovery_code = None
            return

        readiness = self.health.get_operational_readiness()
        if readiness.ok:
            self.set_state(SystemState.READY)
            self.health.mark_ready()
            self.logger.info("Automatic recovery succeeded.")
            self._last_error_recovery_log_at = None
            self._last_error_recovery_code = None
            return

        now = datetime.now()
        should_log = False

        if self._last_error_recovery_log_at is None:
            should_log = True
        elif (now - self._last_error_recovery_log_at).total_seconds() >= 5:
            should_log = True
        elif readiness.error_code != self._last_error_recovery_code:
            should_log = True

        if should_log:
            self.logger.info(
                "Automatic recovery still waiting: %s - %s",
                readiness.error_code,
                readiness.message,
            )
            self._last_error_recovery_log_at = now
            self._last_error_recovery_code = readiness.error_code

    def handle_error(self, message: str, exc: Exception) -> None:
        if self.current_session is not None:
            if self.current_session.ended_at is None:
                self.current_session.ended_at = datetime.now()
                self.current_session.duration_seconds = (
                    self.current_session.ended_at - self.current_session.started_at
                ).total_seconds()

            if self.current_session.status == SessionStatus.IN_PROGRESS:
                self.current_session.status = SessionStatus.FAILED

            if self.current_session.error_message is None:
                self.current_session.error_message = str(exc)

        error_code = "UNKNOWN_ERROR"
        usb_storage_available = None
        storage_writable = None

        if isinstance(exc, HealthCheckError):
            error_code = exc.error_code

            if error_code == "STORAGE_MISSING":
                usb_storage_available = False
                storage_writable = False
            elif error_code == "STORAGE_NOT_WRITABLE":
                usb_storage_available = True
                storage_writable = False
            elif error_code == "STORAGE_LOW_SPACE":
                usb_storage_available = True
                storage_writable = True
            elif error_code.startswith("MICROPHONE_"):
                usb_storage_available = True

        self.health.mark_error(
            str(exc),
            error_code=error_code,
            usb_storage_available=usb_storage_available,
            storage_writable=storage_writable,
        )

        self.status_indicator.set_error(error_code)
        self.logger.exception("%s [%s]: %s", message, error_code, exc)
        self.set_state(SystemState.ERROR)

    def can_shutdown(self) -> bool:
        return self.state in {SystemState.READY, SystemState.ERROR}

    def request_shutdown(self) -> bool:
        if not self.can_shutdown():
            self.logger.warning(
                "Shutdown request rejected in state %s",
                self.state.value,
            )
            return False

        self.health.mark_shutdown_requested()
        self.set_state(SystemState.SHUTTING_DOWN)
        self.health.mark_shutdown_completed()
        self.logger.info("Shutdown sequence completed.")

        try:
            self.logger.info("Triggering operating system shutdown.")
            subprocess.Popen(["sudo", "/usr/sbin/shutdown", "-h", "now"])
        except Exception as exc:
            self.handle_error("Failed to trigger operating system shutdown", exc)
            return False

        return True

    def try_recover(self) -> None:
        if self.state != SystemState.ERROR:
            return

        self.logger.info("Recovery attempt started.")

        try:
            if self.current_session is not None:
                if self.current_session.ended_at is None:
                    self.current_session.ended_at = datetime.now()
                    self.current_session.duration_seconds = (
                        self.current_session.ended_at - self.current_session.started_at
                    ).total_seconds()

                if self.current_session.status == SessionStatus.IN_PROGRESS:
                    self.current_session.status = SessionStatus.INTERRUPTED

                if self.current_session.error_message is None:
                    self.current_session.error_message = "Recovery triggered after error state."

            try:
                if self.recorder.is_recording():
                    self.recorder.stop()
            except Exception as exc:
                self.logger.exception("Recorder stop during recovery failed: %s", exc)

            self.current_session = None

            self.health.boot_checks()
            self.health.mark_boot(usb_storage_available=True)
            self._run_startup_recovery()

            self.set_state(SystemState.READY)
            self.health.mark_ready()
            self.logger.info("Recovery succeeded.")

        except Exception as exc:
            self.handle_error("Recovery failed", exc)

    def _run_startup_recovery(self) -> None:
        recoverable_sessions = self.storage.find_recoverable_sessions()
        for session_dir in recoverable_sessions:
            result = self.storage.recover_session(
                session_dir=session_dir,
                sample_rate=self.config.sample_rate,
                channels=self.config.channels,
            )

            self.health.mark_recording_stopped(
                result["session_id"],
                "recovered",
            )

            self.logger.warning(
                "Recovered session %s from %s (valid_chunks=%s, incomplete_chunks=%s, review_audio=%s)",
                result["session_id"],
                result["session_dir"],
                result["valid_chunk_count"],
                result["incomplete_chunk_count"],
                result["review_audio_path"],
            )

            moved_incomplete_paths = result.get("moved_incomplete_paths", [])
            for source, target in moved_incomplete_paths:
                self.logger.warning(
                    "Archived incomplete chunk from %s to %s",
                    source,
                    target,
                )

        leftovers = self.storage.recover_incomplete_sessions()
        if leftovers:
            for source, target in leftovers:
                self.logger.warning(
                    "Recovered leftover partial file from %s to %s",
                    source,
                    target,
                )