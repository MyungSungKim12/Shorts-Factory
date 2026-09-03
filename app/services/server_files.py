"""Read-only operational file inventory for the dashboard."""
from __future__ import annotations

import base64
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_CATEGORIES = {
    "work": ("work",),
    "longform": ("longform",),
    "staging": ("staging",),
    "rejected": ("rejected",),
    "recovery": ("recovery",),
    "ai_cache": ("media", "ai_openings"),
    "logs": ("logs",),
    "reports": ("reports",),
    "billing": ("billing",),
}

ALLOWED_TOP_LEVEL_FILES = {
    "cron.log": "logs",
    "recomposite.log": "logs",
}

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".webm",
    ".mp3", ".wav", ".m4a",
    ".png", ".jpg", ".jpeg", ".webp",
    ".json", ".log", ".txt",
}

SAFE_JSON_NAMES = {
    "topic.json", "script.json", "prepared.json", "produce_log.json",
    "manual_review.json", "latest.json", "performance_latest.json",
    "credit_state.json",
}

SENSITIVE_PARTS = {
    ".env", "credentials", "credential", "secrets", "secret", ".ssh",
    "token", "tokens", "auth", "oauth",
}


def _file_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_file_id(file_id: str) -> str:
    padded = file_id + "=" * (-len(file_id) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _relative(path: Path, data_dir: Path) -> str:
    return path.relative_to(data_dir).as_posix()


def _safe_path(path: Path, data_dir: Path) -> bool:
    try:
        relative = path.relative_to(data_dir)
    except ValueError:
        return False
    parts = {part.lower() for part in relative.parts}
    if parts & SENSITIVE_PARTS:
        return False
    if path.name.startswith("."):
        return False
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return False
    if (
        suffix == ".json"
        and path.name not in SAFE_JSON_NAMES
        and "work" not in parts
        and "longform" not in parts
    ):
        return False
    return True


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix in {".mp3", ".wav", ".m4a"}:
        return "audio"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix == ".json":
        return "json"
    return "log"


def _previewable(path: Path) -> bool:
    return _kind(path) in {"video", "audio", "image", "json", "log"}


def _category_roots(data_dir: Path) -> dict[str, Path]:
    return {
        category: data_dir.joinpath(*parts)
        for category, parts in ALLOWED_CATEGORIES.items()
    }


def _category_for(path: Path, data_dir: Path) -> str | None:
    if path.parent == data_dir and path.name in ALLOWED_TOP_LEVEL_FILES:
        return ALLOWED_TOP_LEVEL_FILES[path.name]
    for category, root in _category_roots(data_dir).items():
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return category
    return None


def _file_item(path: Path, data_dir: Path, category: str) -> dict:
    stat = path.stat()
    relative = _relative(path, data_dir)
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "id": _file_id(relative),
        "category": category,
        "relative_path": relative,
        "name": path.name,
        "kind": _kind(path),
        "mime_type": mime_type,
        "size_bytes": stat.st_size,
        "modified_at": modified,
        "previewable": _previewable(path),
        "download_url": f"/api/server-files/{_file_id(relative)}/download",
    }


def _iter_safe_files(data_dir: Path):
    roots = _category_roots(data_dir)
    for category, root in roots.items():
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_relative_to(data_dir.resolve()):
                continue
            if _safe_path(path, data_dir):
                yield _file_item(path, data_dir, category)
    for name, category in ALLOWED_TOP_LEVEL_FILES.items():
        path = data_dir / name
        if path.exists() and path.is_file() and not path.is_symlink() and _safe_path(path, data_dir):
            yield _file_item(path, data_dir, category)


def list_server_files(
    data_dir: Path,
    *,
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
) -> dict:
    data_dir = Path(data_dir).resolve()
    files = [
        item for item in _iter_safe_files(data_dir)
        if category is None or item["category"] == category
    ]
    files.sort(key=lambda item: (item["modified_at"], item["relative_path"]), reverse=True)
    categories: dict[str, dict] = {}
    for item in files:
        info = categories.setdefault(item["category"], {"files": 0, "bytes": 0})
        info["files"] += 1
        info["bytes"] += item["size_bytes"]
    total_items = len(files)
    offset = (page - 1) * page_size
    usage = shutil.disk_usage(data_dir)
    return {
        "files": files[offset:offset + page_size],
        "summary": {
            "total_files": total_items,
            "total_bytes": sum(item["size_bytes"] for item in files),
            "categories": categories,
            "disk": {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            },
        },
    }


def resolve_download(data_dir: Path, file_id: str) -> Path | None:
    try:
        relative = decode_file_id(file_id)
    except Exception:
        return None
    if relative.startswith("/") or "\\" in relative:
        return None
    data_dir = Path(data_dir).resolve()
    path = (data_dir / relative).resolve()
    try:
        path.relative_to(data_dir)
    except ValueError:
        return None
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    if _category_for(path, data_dir) is None:
        return None
    if not _safe_path(path, data_dir):
        return None
    return path
