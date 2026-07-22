"""영구 보존되는 AI 오프닝 파일과 SQLite 색인을 관리한다."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

from app.services.media_probe import ffprobe_path_for, probe_ai_video
from app.services.process_runner import run_checked


@dataclass(frozen=True)
class AiOpeningAsset:
    asset_id: str
    subject_key: str
    reuse_scope: str
    reference_path: Path
    master_path: Path
    opening_path: Path
    status: str
    source_url: str
    license: str
    model: str
    prompt: str
    source_metadata: dict
    generated_at: str
    last_used_at: str | None
    use_count: int


def normalize_subject_key(value: str) -> str:
    """실제 대상 이름을 재사용 색인에 적합한 안정 문자열로 정규화한다."""
    text = str(value or "").removeprefix("exact:").strip().lower()
    text = re.sub(r"[^0-9a-z가-힣]+", "-", text, flags=re.IGNORECASE)
    return text.strip("-")


def _average_hash(path: Path) -> tuple[bool, ...]:
    with Image.open(path) as source:
        image = ImageOps.fit(source.convert("L"), (16, 16))
        values = list(image.getdata())
    average = sum(values) / len(values)
    return tuple(value >= average for value in values)


def frame_distance(reference: Path, video: Path, ffmpeg_path: str) -> int:
    """기준 이미지와 생성 영상 첫 프레임의 256비트 해밍 거리를 계산한다."""
    with tempfile.TemporaryDirectory(prefix="ai-opening-frame-") as temporary:
        frame = Path(temporary) / "first-frame.png"
        run_checked(
            [
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video), "-frames:v", "1", str(frame),
            ],
            timeout=180,
            text=True,
        )
        left = _average_hash(Path(reference))
        right = _average_hash(frame)
    return sum(a != b for a, b in zip(left, right))


def validate_ai_opening(
    reference: Path,
    master: Path,
    *,
    ffmpeg_path: str,
) -> dict:
    """실제 기준 이미지 보존과 세로형 무음 영상 규격을 검증한다."""
    failures = []
    try:
        report = probe_ai_video(Path(master), ffprobe_path_for(ffmpeg_path))
    except Exception as exc:
        return {
            "passed": False,
            "failures": ["unreadable_video"],
            "error": " ".join(str(exc).split())[:300],
        }
    width = int(report.get("width") or 0)
    height = int(report.get("height") or 0)
    duration = float(report.get("duration") or 0)
    if width <= 0 or height <= width:
        failures.append("vertical_resolution")
    if not 3.5 <= duration <= 8.5:
        failures.append("duration")
    if report.get("video_codec") != "h264":
        failures.append("video_codec")
    if report.get("has_audio"):
        failures.append("unexpected_audio")
    distance = None
    try:
        distance = frame_distance(Path(reference), Path(master), ffmpeg_path)
        threshold = max(0, int(os.getenv("VEO_FRAME_DISTANCE_MAX", "32")))
        if distance > threshold:
            failures.append("reference_frame_mismatch")
    except Exception as exc:
        failures.append("reference_frame_unreadable")
        report["reference_error"] = " ".join(str(exc).split())[:300]
    report["reference_frame_distance"] = distance
    return {"passed": not failures, "failures": failures, "report": report}


def build_opening_derivative(
    master: Path,
    output: Path,
    *,
    ffmpeg_path: str,
    max_duration: float | None = None,
) -> Path:
    """Veo 원본을 보존하면서 Shorts용 최대 3초 무음 편집본을 만든다."""
    duration = max_duration
    if duration is None:
        duration = float(os.getenv("VEO_OPENING_MAX_SEC", "3.0"))
    duration = min(3.0, max(0.5, float(duration)))
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(master), "-t", f"{duration:.3f}", "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
        ],
        timeout=300,
        text=True,
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("AI 오프닝 편집본 생성 실패")
    return destination


class AiOpeningLibrary:
    """`data/media/ai_openings`를 자동 정리와 분리해 영구 관리한다."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / "media" / "ai_openings"
        self.db_path = self.data_dir / "videos.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_opening_assets (
                    asset_id TEXT PRIMARY KEY,
                    subject_key TEXT NOT NULL,
                    reuse_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reference_path TEXT NOT NULL,
                    master_path TEXT NOT NULL,
                    opening_path TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    license TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    validation_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_ai_opening_subject
                ON ai_opening_assets(subject_key, status);
                CREATE TABLE IF NOT EXISTS ai_opening_usage (
                    asset_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    used_at TEXT NOT NULL,
                    PRIMARY KEY(asset_id, run_id)
                );
            """)

    def _store_path(self, value: str | Path) -> str:
        path = Path(value).resolve()
        try:
            return path.relative_to(self.data_dir).as_posix()
        except ValueError:
            return str(path)

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.data_dir / path

    def _from_row(self, row: sqlite3.Row) -> AiOpeningAsset:
        source_metadata = {}
        try:
            metadata_path = self.root / row["asset_id"] / "metadata.json"
            source_metadata = json.loads(metadata_path.read_text(encoding="utf-8")).get(
                "source_metadata", {}
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return AiOpeningAsset(
            asset_id=row["asset_id"],
            subject_key=row["subject_key"],
            reuse_scope=row["reuse_scope"],
            reference_path=self._resolve_path(row["reference_path"]),
            master_path=self._resolve_path(row["master_path"]),
            opening_path=self._resolve_path(row["opening_path"]),
            status=row["status"],
            source_url=row["source_url"],
            license=row["license"],
            model=row["model"],
            prompt=row["prompt"],
            source_metadata=source_metadata,
            generated_at=row["generated_at"],
            last_used_at=row["last_used_at"],
            use_count=int(row["use_count"]),
        )

    def create_asset_workspace(self, subject_key: str) -> tuple[str, Path]:
        key = normalize_subject_key(subject_key) or "subject"
        asset_id = f"{key[:48]}-{uuid.uuid4().hex[:12]}"
        directory = self.root / asset_id
        directory.mkdir(parents=False, exist_ok=False)
        return asset_id, directory

    def register_asset(self, *, metadata: dict) -> AiOpeningAsset:
        asset_id = str(metadata["asset_id"]).strip()
        subject_key = normalize_subject_key(metadata["subject_key"])
        reuse_scope = str(metadata.get("reuse_scope") or "exact_subject")
        status = str(metadata.get("status") or "rejected")
        if not asset_id or not subject_key:
            raise ValueError("asset_id와 subject_key는 필수입니다")
        if reuse_scope not in {"exact_subject", "concept"}:
            raise ValueError(f"지원하지 않는 reuse_scope: {reuse_scope}")
        if status not in {"ready", "rejected", "generating"}:
            raise ValueError(f"지원하지 않는 status: {status}")

        generated_at = str(
            metadata.get("generated_at") or datetime.now(timezone.utc).isoformat()
        )
        stored = dict(metadata)
        stored.update(
            asset_id=asset_id,
            subject_key=subject_key,
            reuse_scope=reuse_scope,
            status=status,
            generated_at=generated_at,
        )
        for field in ("reference_path", "master_path", "opening_path"):
            stored[field] = self._store_path(metadata[field])

        asset_dir = self.root / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = asset_dir / "metadata.json"
        temporary = metadata_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(metadata_path)

        with self._connect() as db:
            db.execute("""
                INSERT OR REPLACE INTO ai_opening_assets (
                    asset_id, subject_key, reuse_scope, status,
                    reference_path, master_path, opening_path,
                    source_url, license, model, prompt, generated_at,
                    last_used_at, use_count, error, validation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT last_used_at FROM ai_opening_assets WHERE asset_id=?), NULL),
                    COALESCE((SELECT use_count FROM ai_opening_assets WHERE asset_id=?), 0),
                    ?, ?)
            """, (
                asset_id, subject_key, reuse_scope, status,
                stored["reference_path"], stored["master_path"], stored["opening_path"],
                str(stored.get("source_url") or ""), str(stored.get("license") or ""),
                str(stored.get("model") or ""), str(stored.get("prompt") or ""),
                generated_at, asset_id, asset_id, str(stored.get("error") or ""),
                json.dumps(stored.get("validation") or {}, ensure_ascii=False),
            ))
            row = db.execute(
                "SELECT * FROM ai_opening_assets WHERE asset_id=?", (asset_id,)
            ).fetchone()
        return self._from_row(row)

    def find_reusable_asset(
        self,
        subject_key: str,
        *,
        cooldown_days: int = 14,
        now: datetime | None = None,
    ) -> AiOpeningAsset | None:
        key = normalize_subject_key(subject_key)
        if not key:
            return None
        with self._connect() as db:
            rows = db.execute("""
                SELECT * FROM ai_opening_assets
                WHERE subject_key=? AND reuse_scope='exact_subject' AND status='ready'
                ORDER BY use_count ASC, generated_at ASC
            """, (key,)).fetchall()
        assets = [self._from_row(row) for row in rows]
        assets = [
            asset for asset in assets
            if asset.reference_path.is_file()
            and asset.master_path.is_file()
            and asset.opening_path.is_file()
        ]
        if not assets:
            return None

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(days=max(0, cooldown_days))
        for asset in assets:
            if not asset.last_used_at:
                return asset
            try:
                used_at = datetime.fromisoformat(asset.last_used_at)
                if used_at.tzinfo is None:
                    used_at = used_at.replace(tzinfo=timezone.utc)
                if used_at < cutoff:
                    return asset
            except ValueError:
                return asset
        return assets[0]

    def mark_asset_used(
        self,
        asset_id: str,
        run_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        used_at = now or datetime.now(timezone.utc)
        if used_at.tzinfo is None:
            used_at = used_at.replace(tzinfo=timezone.utc)
        stamp = used_at.isoformat()
        with self._connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO ai_opening_usage(asset_id, run_id, used_at) VALUES (?, ?, ?)",
                (asset_id, str(run_id), stamp),
            ).rowcount
            if inserted:
                db.execute("""
                    UPDATE ai_opening_assets
                    SET last_used_at=?, use_count=use_count+1
                    WHERE asset_id=?
                """, (stamp, asset_id))
