from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SystemState(Enum):
    BOOTING = "BOOTING"
    READY = "READY"
    STARTING_RECORDING = "STARTING_RECORDING"
    RECORDING = "RECORDING"
    STOPPING_RECORDING = "STOPPING_RECORDING"
    SAVING = "SAVING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    ERROR = "ERROR"


class SessionStatus(Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    RECOVERED = "recovered"
    AUTO_STOPPED = "auto_stopped"


@dataclass
class Session:
    session_id: str
    started_at: datetime

    session_dir: Path
    chunks_dir: Path

    temp_metadata_path: Path
    final_metadata_path: Path

    review_audio_path: Path
    review_metadata_path: Path

    current_chunk_temp_path: Path
    current_chunk_final_path: Path

    chunk_index: int = 1
    chunk_paths: list[Path] = field(default_factory=list)

    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: SessionStatus = SessionStatus.IN_PROGRESS
    error_message: Optional[str] = None
    file_size_bytes: Optional[int] = None


@dataclass
class TransitionResult:
    success: bool
    next_state: SystemState
    message: str = ""
    exception: Optional[Exception] = None