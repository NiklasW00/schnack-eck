from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_root: Path = field(
        default_factory=lambda: Path(os.getenv("SCHNACK_ECK_DATA_ROOT", "data"))
    )

    sessions_dir: Path = field(init=False)
    review_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    recovered_dir: Path = field(init=False)
    health_path: Path = field(init=False)

    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2

    min_free_space_mb: int = 2048
    poll_interval_seconds: float = 0.1

    simulate_recorder: bool = True
    chunk_duration_seconds: int = 30
    max_session_duration_seconds: int = 3600

    input_device_id: int | None = None
    preferred_input_device_name: str | None = None
    
    use_gpio_input: bool = True
    button_gpio_pin: int = 4
    shutdown_hold_seconds: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "sessions_dir", self.data_root / "sessions")
        object.__setattr__(self, "review_dir", self.data_root / "review")
        object.__setattr__(self, "logs_dir", self.data_root / "logs")
        object.__setattr__(self, "recovered_dir", self.data_root / "recovered")
        object.__setattr__(self, "health_path", self.data_root / "health.json")