from __future__ import annotations

import json
import shutil
import wave
from datetime import datetime
from pathlib import Path

from app.config import Config
from app.models import Session, SessionStatus


class StorageManager:
    def __init__(self, config: Config):
        self.config = config

    def create_session(self) -> Session:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        session_id = now.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3]

        day_dir = self.config.sessions_dir / date_str
        session_dir = day_dir / f"{session_id}_session"
        chunks_dir = session_dir / "chunks"

        review_day_dir = self.config.review_dir / date_str

        session_dir.mkdir(parents=True, exist_ok=True)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        review_day_dir.mkdir(parents=True, exist_ok=True)

        temp_metadata_path = session_dir / "session.json.part"
        final_metadata_path = session_dir / "session.json"

        current_chunk_temp_path = chunks_dir / "000001.wav.part"
        current_chunk_final_path = chunks_dir / "000001.wav"

        review_audio_path = review_day_dir / f"{session_id}_session.wav"
        review_metadata_path = review_day_dir / f"{session_id}_session.json"

        return Session(
            session_id=session_id,
            started_at=now,
            session_dir=session_dir,
            chunks_dir=chunks_dir,
            temp_metadata_path=temp_metadata_path,
            final_metadata_path=final_metadata_path,
            review_audio_path=review_audio_path,
            review_metadata_path=review_metadata_path,
            current_chunk_temp_path=current_chunk_temp_path,
            current_chunk_final_path=current_chunk_final_path,
        )

    def build_session_from_dir(self, session_dir: Path) -> Session:
        session_id = session_dir.name.removesuffix("_session")
        started_at = self._parse_session_id_timestamp(session_id)

        chunks_dir = session_dir / "chunks"
        temp_metadata_path = session_dir / "session.json.part"
        final_metadata_path = session_dir / "session.json"

        review_day_dir = self.config.review_dir / started_at.strftime("%Y-%m-%d")
        review_audio_path = review_day_dir / f"{session_id}_session.wav"
        review_metadata_path = review_day_dir / f"{session_id}_session.json"

        return Session(
            session_id=session_id,
            started_at=started_at,
            session_dir=session_dir,
            chunks_dir=chunks_dir,
            temp_metadata_path=temp_metadata_path,
            final_metadata_path=final_metadata_path,
            review_audio_path=review_audio_path,
            review_metadata_path=review_metadata_path,
            current_chunk_temp_path=chunks_dir / "000001.wav.part",
            current_chunk_final_path=chunks_dir / "000001.wav",
        )

    def finalize_current_chunk(self, session: Session) -> None:
        if not session.current_chunk_temp_path.exists():
            raise FileNotFoundError(
                f"Temporary chunk file missing: {session.current_chunk_temp_path}"
            )

        session.current_chunk_temp_path.replace(session.current_chunk_final_path)

        if not session.current_chunk_final_path.exists():
            raise RuntimeError("Final chunk file was not created successfully.")

        if session.current_chunk_final_path not in session.chunk_paths:
            session.chunk_paths.append(session.current_chunk_final_path)

    def prepare_next_chunk_paths(self, session: Session) -> None:
        session.chunk_index += 1
        chunk_name = f"{session.chunk_index:06d}"

        session.current_chunk_temp_path = session.chunks_dir / f"{chunk_name}.wav.part"
        session.current_chunk_final_path = session.chunks_dir / f"{chunk_name}.wav"

    def get_next_chunk_temp_path(self, session: Session) -> Path:
        return session.current_chunk_temp_path

    def finalize_current_chunk_and_get_next_temp_path(self, session: Session) -> Path:
        self.finalize_current_chunk(session)
        self.prepare_next_chunk_paths(session)
        return session.current_chunk_temp_path

    def get_valid_chunk_paths(self, session: Session) -> list[Path]:
        return sorted(session.chunks_dir.glob("*.wav"))

    def get_incomplete_chunk_paths(self, session: Session) -> list[Path]:
        return sorted(session.chunks_dir.glob("*.wav.part"))

    def calculate_total_chunk_size_bytes(self, session: Session) -> int:
        return sum(path.stat().st_size for path in self.get_valid_chunk_paths(session))

    def write_metadata(self, session: Session, sample_rate: int, channels: int) -> None:
        valid_chunks = self.get_valid_chunk_paths(session)
        incomplete_chunks = self.get_incomplete_chunk_paths(session)

        review_created = session.review_audio_path.exists() and session.review_metadata_path.exists()

        payload = {
            "schema_version": 2,
            "session_id": session.session_id,
            "status": session.status.value,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "duration_seconds": session.duration_seconds,
            "storage_mode": "chunked_wav",
            "audio": {
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_width_bytes": self.config.sample_width_bytes,
                "chunk_count": len(valid_chunks),
                "chunks": [str(path.relative_to(session.session_dir)) for path in valid_chunks],
                "incomplete_chunk_count": len(incomplete_chunks),
                "incomplete_chunks": [
                    str(path.relative_to(session.session_dir)) for path in incomplete_chunks
                ],
                "file_size_bytes": session.file_size_bytes,
            },
            "review_export": {
                "created": review_created,
                "audio_path": str(session.review_audio_path),
                "metadata_path": str(session.review_metadata_path),
            },
            "device": {
                "input_device": None,
            },
            "error": {
                "message": session.error_message,
            },
        }

        with session.temp_metadata_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        session.temp_metadata_path.replace(session.final_metadata_path)

    def finalize_session_storage(self, session: Session) -> None:
        valid_chunks = self.get_valid_chunk_paths(session)
        if not valid_chunks:
            raise RuntimeError("No valid chunks found for session finalization.")
        session.file_size_bytes = self.calculate_total_chunk_size_bytes(session)

    def merge_chunks_to_review_wav(
        self,
        chunk_paths: list[Path],
        output_path: Path,
        sample_rate: int,
        channels: int,
    ) -> int:
        valid_chunk_paths = [p for p in chunk_paths if p.suffix.lower() == ".wav" and p.exists()]
        valid_chunk_paths = sorted(valid_chunk_paths)

        if not valid_chunk_paths:
            raise RuntimeError("No valid .wav chunks available for review export.")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(output_path), "wb") as out_wav:
            out_wav.setnchannels(channels)
            out_wav.setsampwidth(self.config.sample_width_bytes)
            out_wav.setframerate(sample_rate)

            for chunk_path in valid_chunk_paths:
                with wave.open(str(chunk_path), "rb") as in_wav:
                    if in_wav.getnchannels() != channels:
                        raise RuntimeError(
                            f"Chunk channel mismatch in {chunk_path}: "
                            f"{in_wav.getnchannels()} != {channels}"
                        )
                    if in_wav.getframerate() != sample_rate:
                        raise RuntimeError(
                            f"Chunk sample rate mismatch in {chunk_path}: "
                            f"{in_wav.getframerate()} != {sample_rate}"
                        )
                    if in_wav.getsampwidth() != self.config.sample_width_bytes:
                        raise RuntimeError(
                            f"Chunk sample width mismatch in {chunk_path}: "
                            f"{in_wav.getsampwidth()} != {self.config.sample_width_bytes}"
                        )

                    out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))

        return output_path.stat().st_size

    def write_review_metadata(
        self,
        session: Session,
        sample_rate: int,
        channels: int,
        merged_chunk_paths: list[Path],
        export_status: str,
        export_error_message: str | None,
        excluded_chunks: list[Path] | None = None,
    ) -> None:
        valid_chunks = self.get_valid_chunk_paths(session)
        excluded_chunks = sorted(excluded_chunks or [])

        payload = {
            "schema_version": 1,
            "session_id": session.session_id,
            "status": export_status,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "duration_seconds": session.duration_seconds,
            "audio": {
                "filename": session.review_audio_path.name,
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_width_bytes": self.config.sample_width_bytes,
                "file_size_bytes": session.review_audio_path.stat().st_size
                if session.review_audio_path.exists()
                else None,
            },
            "source": {
                "session_dir": str(session.session_dir),
                "chunk_count_total": len(valid_chunks) + len(excluded_chunks),
                "chunk_count_merged": len(merged_chunk_paths),
                "merged_chunks": [
                    str(path.relative_to(session.session_dir)) for path in merged_chunk_paths
                ],
                "excluded_chunks": [
                    str(path.relative_to(session.session_dir))
                    if path.is_relative_to(session.session_dir)
                    else str(path)
                    for path in excluded_chunks
                ],
            },
            "error": {
                "message": export_error_message,
            },
        }

        with session.review_metadata_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def export_review_audio(
        self,
        session: Session,
        sample_rate: int,
        channels: int,
        recovered: bool = False,
        excluded_chunks: list[Path] | None = None,
    ) -> int:
        valid_chunks = self.get_valid_chunk_paths(session)
        if not valid_chunks:
            raise RuntimeError("No valid chunks available for review export.")

        if recovered:
            review_audio_path = session.review_audio_path.with_name(
                session.review_audio_path.stem + "_recovered.wav"
            )
            review_metadata_path = session.review_metadata_path.with_name(
                session.review_metadata_path.stem + "_recovered.json"
            )
        else:
            review_audio_path = session.review_audio_path
            review_metadata_path = session.review_metadata_path

        merged_size = self.merge_chunks_to_review_wav(
            chunk_paths=valid_chunks,
            output_path=review_audio_path,
            sample_rate=sample_rate,
            channels=channels,
        )

        original_review_audio_path = session.review_audio_path
        original_review_metadata_path = session.review_metadata_path

        try:
            session.review_audio_path = review_audio_path
            session.review_metadata_path = review_metadata_path

            self.write_review_metadata(
                session=session,
                sample_rate=sample_rate,
                channels=channels,
                merged_chunk_paths=valid_chunks,
                export_status="recovered_partial" if recovered else "completed",
                export_error_message=(
                    "Recovered after unexpected interruption. Incomplete chunk files were excluded from merge."
                    if recovered
                    else None
                ),
                excluded_chunks=excluded_chunks,
            )
        finally:
            session.review_audio_path = original_review_audio_path
            session.review_metadata_path = original_review_metadata_path

        return merged_size

    def mark_session_interrupted(self, session: Session, error_message: str) -> None:
        session.ended_at = datetime.now()
        session.duration_seconds = (
            session.ended_at - session.started_at
        ).total_seconds()
        session.status = SessionStatus.INTERRUPTED
        session.error_message = error_message

    def find_recoverable_sessions(self) -> list[Path]:
        recoverable: list[Path] = []

        for session_dir in sorted(self.config.sessions_dir.rglob("*_session")):
            if not session_dir.is_dir():
                continue

            chunks_dir = session_dir / "chunks"
            if not chunks_dir.exists():
                continue

            valid_chunks = sorted(chunks_dir.glob("*.wav"))
            incomplete_chunks = sorted(chunks_dir.glob("*.wav.part"))
            session_json = session_dir / "session.json"

            if not valid_chunks:
                continue

            review_audio = self._review_audio_path_for_session_dir(session_dir)
            review_recovered_audio = review_audio.with_name(review_audio.stem + "_recovered.wav")

            has_any_review_export = review_audio.exists() or review_recovered_audio.exists()
            needs_recovery = incomplete_chunks or not session_json.exists() or not has_any_review_export

            if needs_recovery:
                recoverable.append(session_dir)

        return recoverable

    def recover_session(
        self,
        session_dir: Path,
        sample_rate: int,
        channels: int,
    ) -> dict:
        session = self.build_session_from_dir(session_dir)

        valid_chunks = self.get_valid_chunk_paths(session)
        incomplete_chunks = self.get_incomplete_chunk_paths(session)

        if not valid_chunks:
            raise RuntimeError(f"No valid chunks available for recovery in {session_dir}")

        session.ended_at = datetime.now()
        session.duration_seconds = (session.ended_at - session.started_at).total_seconds()
        session.status = SessionStatus.RECOVERED
        session.error_message = (
            "Recovered after unexpected interruption. Incomplete chunk files were excluded from merge."
        )

        self.finalize_session_storage(session)

        review_size = self.export_review_audio(
            session=session,
            sample_rate=sample_rate,
            channels=channels,
            recovered=True,
            excluded_chunks=incomplete_chunks,
        )

        moved_incomplete = self.archive_incomplete_chunks(session)

        self.write_metadata(
            session=session,
            sample_rate=sample_rate,
            channels=channels,
        )

        return {
            "session_id": session.session_id,
            "session_dir": session.session_dir,
            "valid_chunk_count": len(valid_chunks),
            "incomplete_chunk_count": len(incomplete_chunks),
            "review_audio_path": session.review_audio_path.with_name(
                session.review_audio_path.stem + "_recovered.wav"
            ),
            "review_metadata_path": session.review_metadata_path.with_name(
                session.review_metadata_path.stem + "_recovered.json"
            ),
            "review_size": review_size,
            "moved_incomplete_paths": moved_incomplete,
        }

    def archive_incomplete_chunks(self, session: Session) -> list[tuple[Path, Path]]:
        moved_items: list[tuple[Path, Path]] = []
        self.config.recovered_dir.mkdir(parents=True, exist_ok=True)

        for path in self.get_incomplete_chunk_paths(session):
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3]
            target_name = f"{timestamp}__{session.session_id}__{path.name}"
            target_path = self.config.recovered_dir / target_name
            shutil.move(str(path), str(target_path))
            moved_items.append((path, target_path))

        return moved_items

    def recover_incomplete_sessions(self) -> list[tuple[Path, Path]]:
        """
        Legacy-Fallback für lose .part-Dateien außerhalb der neuen Session-Recovery.
        Kann vorerst bestehen bleiben, sollte aber im Boot-Prozess nach Möglichkeit
        von find_recoverable_sessions()/recover_session() abgelöst werden.
        """
        recovered_items: list[tuple[Path, Path]] = []
        self.config.recovered_dir.mkdir(parents=True, exist_ok=True)

        for path in self.config.sessions_dir.rglob("*.part"):
            if path.parent.name == "chunks" and path.parent.parent.name.endswith("_session"):
                continue

            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3]
            target_name = f"{timestamp}__{path.name}"
            target_path = self.config.recovered_dir / target_name

            shutil.move(str(path), str(target_path))
            recovered_items.append((path, target_path))

        return recovered_items

    def _parse_session_id_timestamp(self, session_id: str) -> datetime:
        return datetime.strptime(session_id, "%Y-%m-%dT%H-%M-%S-%f")

    def _review_audio_path_for_session_dir(self, session_dir: Path) -> Path:
        session_id = session_dir.name.removesuffix("_session")
        started_at = self._parse_session_id_timestamp(session_id)
        review_day_dir = self.config.review_dir / started_at.strftime("%Y-%m-%d")
        return review_day_dir / f"{session_id}_session.wav"