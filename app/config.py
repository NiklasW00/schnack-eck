from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_root: Path = Path("data")
    sessions_dir: Path = Path("data/sessions")
    review_dir: Path = Path("data/review")
    logs_dir: Path = Path("data/logs")
    recovered_dir: Path = Path("data/recovered")
    health_path: Path = Path("data/health.json")

    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2

    min_free_space_mb: int = 500
    poll_interval_seconds: float = 0.1

    simulate_recorder: bool = True
    
    chunk_duration_seconds: int = 30
    
    max_session_duration_seconds: int = 3600
    
    min_free_space_mb: int = 2048
    
    input_device_id: int | None = None
    preferred_input_device_name: str | None = None