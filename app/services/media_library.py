"""스토리형 Shorts용 무료 미디어 검색, 선별, 중복 방지."""
import os
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass(frozen=True)
class MediaCandidate:
    provider: str
    media_id: str
    source_url: str
    download_url: str
    width: int
    height: int
    media_type: str
    keyword: str

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.media_id}"


def _quality(candidate: MediaCandidate) -> tuple:
    portrait = candidate.height > candidate.width
    resolution = min(candidate.width, candidate.height)
    video = candidate.media_type == "video"
    return portrait, resolution, video


def choose_candidate(
    candidates: list[MediaCandidate],
    used_ids: set[str],
) -> MediaCandidate | None:
    """이미 사용한 소스를 제외하고 세로·고해상도·비디오 순으로 선택한다."""
    available = [item for item in candidates if item.unique_id not in used_ids]
    return max(available, key=_quality) if available else None


def _best_variant(variants: list[dict], url_key: str) -> dict | None:
    usable = [item for item in variants if item.get(url_key)]
    if not usable:
        return None
    return max(
        usable,
        key=lambda item: (
            int(item.get("height", 0)) > int(item.get("width", 0)),
            min(int(item.get("width", 0)), int(item.get("height", 0))),
        ),
    )


def _pexels_video_candidates(keyword: str) -> list[MediaCandidate]:
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "per_page": 8, "orientation": "portrait"},
            timeout=15,
        )
        response.raise_for_status()
        candidates = []
        for video in response.json().get("videos", []):
            variant = _best_variant(video.get("video_files", []), "link")
            if not variant:
                continue
            candidates.append(MediaCandidate(
                provider="pexels_video",
                media_id=str(video.get("id", "")),
                source_url=video.get("url", ""),
                download_url=variant["link"],
                width=int(variant.get("width", video.get("width", 0))),
                height=int(variant.get("height", video.get("height", 0))),
                media_type="video",
                keyword=keyword,
            ))
        return candidates
    except (requests.RequestException, ValueError, TypeError):
        return []


def _pixabay_video_candidates(keyword: str) -> list[MediaCandidate]:
    api_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": api_key, "q": keyword, "per_page": 8},
            timeout=15,
        )
        response.raise_for_status()
        candidates = []
        for hit in response.json().get("hits", []):
            variants = list((hit.get("videos") or {}).values())
            variant = _best_variant(variants, "url")
            if not variant:
                continue
            candidates.append(MediaCandidate(
                provider="pixabay_video",
                media_id=str(hit.get("id", "")),
                source_url=hit.get("pageURL", ""),
                download_url=variant["url"],
                width=int(variant.get("width", 0)),
                height=int(variant.get("height", 0)),
                media_type="video",
                keyword=keyword,
            ))
        return candidates
    except (requests.RequestException, ValueError, TypeError):
        return []


def _pexels_photo_candidates(keyword: str) -> list[MediaCandidate]:
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": keyword, "per_page": 8, "orientation": "portrait"},
            timeout=15,
        )
        response.raise_for_status()
        candidates = []
        for photo in response.json().get("photos", []):
            source = photo.get("src") or {}
            download_url = source.get("large2x") or source.get("portrait") or source.get("original")
            if not download_url:
                continue
            candidates.append(MediaCandidate(
                provider="pexels_image",
                media_id=str(photo.get("id", "")),
                source_url=photo.get("url", ""),
                download_url=download_url,
                width=int(photo.get("width", 0)),
                height=int(photo.get("height", 0)),
                media_type="image",
                keyword=keyword,
            ))
        return candidates
    except (requests.RequestException, ValueError, TypeError):
        return []


def _download_candidate(candidate: MediaCandidate, output: Path) -> None:
    response = requests.get(candidate.download_url, timeout=45)
    response.raise_for_status()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)


async def fetch_story_media(
    keywords: list[str],
    output_stem: Path,
    used_ids: set[str],
) -> tuple[Path | None, dict]:
    """검색어 순서를 지키며 무료 소스를 내려받고 출처 메타데이터를 반환한다."""
    clean_keywords = list(dict.fromkeys(value.strip() for value in keywords if value.strip()))
    providers = (
        _pexels_video_candidates,
        _pixabay_video_candidates,
        _pexels_photo_candidates,
    )
    for keyword_index, keyword in enumerate(clean_keywords):
        for collect in providers:
            candidate = choose_candidate(collect(keyword), used_ids)
            if not candidate:
                continue
            suffix = ".mp4" if candidate.media_type == "video" else ".jpg"
            output = Path(f"{output_stem}{suffix}")
            try:
                _download_candidate(candidate, output)
            except requests.RequestException:
                continue
            used_ids.add(candidate.unique_id)
            return output, {
                "provider": candidate.provider,
                "media_id": candidate.media_id,
                "source_url": candidate.source_url,
                "keyword": keyword,
                "fallback": keyword_index > 0,
                "width": candidate.width,
                "height": candidate.height,
            }

    return None, {
        "provider": "black_bg",
        "media_id": "",
        "source_url": "",
        "keyword": clean_keywords[0] if clean_keywords else "",
        "fallback": True,
        "width": 1080,
        "height": 1920,
    }
