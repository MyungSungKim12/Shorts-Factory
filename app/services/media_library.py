"""스토리형 Shorts용 무료 미디어 검색, 선별, 중복 방지."""
import os
import html
import re
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
    license: str = ""
    attribution: str = ""

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
    ranked = choose_candidates(candidates, used_ids)
    return ranked[0] if ranked else None


def choose_candidates(
    candidates: list[MediaCandidate],
    used_ids: set[str],
) -> list[MediaCandidate]:
    available = [item for item in candidates if item.unique_id not in used_ids]
    return sorted(available, key=_quality, reverse=True)


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


def _plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _wikimedia_image_candidates(keyword: str) -> list[MediaCandidate]:
    """허용 라이선스가 명시된 Wikimedia Commons 비트맵을 검색한다."""
    try:
        response = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"{keyword} filetype:bitmap",
                "gsrnamespace": 6,
                "gsrlimit": 12,
                "prop": "imageinfo|info",
                "inprop": "url",
                "iiprop": "url|size|extmetadata",
                "iiurlwidth": 1800,
                "format": "json",
                "origin": "*",
            },
            headers={"User-Agent": "ShortsFactory/1.0 (local sample generator)"},
            timeout=20,
        )
        response.raise_for_status()
        candidates = []
        pages = (response.json().get("query") or {}).get("pages", {})
        for page in pages.values():
            image_info = (page.get("imageinfo") or [{}])[0]
            metadata = image_info.get("extmetadata") or {}
            license_name = _plain_text((metadata.get("LicenseShortName") or {}).get("value", ""))
            normalized = license_name.lower().replace("-", " ")
            if not any(token in normalized for token in ("public domain", "cc0", "cc by")):
                continue
            download_url = image_info.get("thumburl") or image_info.get("url")
            if not download_url:
                continue
            candidates.append(MediaCandidate(
                provider="wikimedia_image",
                media_id=str(page.get("title") or page.get("pageid") or ""),
                source_url=page.get("canonicalurl") or page.get("fullurl") or "",
                download_url=download_url,
                width=int(image_info.get("thumbwidth") or image_info.get("width") or 0),
                height=int(image_info.get("thumbheight") or image_info.get("height") or 0),
                media_type="image",
                keyword=keyword,
                license=license_name,
                attribution=_plain_text(
                    (metadata.get("Artist") or metadata.get("Credit") or {}).get("value", "")
                ),
            ))
        return candidates
    except (requests.RequestException, ValueError, TypeError):
        return []


def _download_candidate(candidate: MediaCandidate, output: Path) -> None:
    headers = (
        {"User-Agent": "ShortsFactory/1.0 (local sample generator)"}
        if candidate.provider == "wikimedia_image" else None
    )
    response = requests.get(candidate.download_url, headers=headers, timeout=45)
    response.raise_for_status()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)


def _is_usable_download(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        header = path.read_bytes()[:12]
    except OSError:
        return False
    if path.suffix.lower() == ".mp4":
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8")
    return True


async def fetch_story_media(
    keywords: list[str],
    output_stem: Path,
    used_ids: set[str],
) -> tuple[Path | None, dict]:
    """검색어 순서를 지키며 무료 소스를 내려받고 출처 메타데이터를 반환한다."""
    clean_keywords = list(dict.fromkeys(value.strip() for value in keywords if value.strip()))
    for keyword_index, raw_keyword in enumerate(clean_keywords):
        exact = raw_keyword.lower().startswith("exact:")
        keyword = raw_keyword.split(":", 1)[1].strip() if exact else raw_keyword
        providers = (
            (_wikimedia_image_candidates,) if exact else ()
        ) + (
            _pexels_video_candidates,
            _pixabay_video_candidates,
            _pexels_photo_candidates,
        )
        for collect in providers:
            for candidate in choose_candidates(collect(keyword), used_ids):
                suffix = ".mp4" if candidate.media_type == "video" else ".jpg"
                output = Path(f"{output_stem}{suffix}")
                try:
                    _download_candidate(candidate, output)
                except (requests.RequestException, OSError):
                    continue
                if not _is_usable_download(output):
                    output.unlink(missing_ok=True)
                    continue
                used_ids.add(candidate.unique_id)
                metadata = {
                    "provider": candidate.provider,
                    "media_id": candidate.media_id,
                    "source_url": candidate.source_url,
                    "keyword": keyword,
                    "fallback": keyword_index > 0,
                    "width": candidate.width,
                    "height": candidate.height,
                }
                if candidate.license:
                    metadata["license"] = candidate.license
                if candidate.attribution:
                    metadata["attribution"] = candidate.attribution
                return output, metadata

    return None, {
        "provider": "black_bg",
        "media_id": "",
        "source_url": "",
        "keyword": clean_keywords[0] if clean_keywords else "",
        "fallback": True,
        "width": 1080,
        "height": 1920,
    }
