"""스토리형 Shorts용 무료 미디어 검색, 선별, 중복 방지."""
import os
import html
import re
from dataclasses import dataclass
from pathlib import Path

import requests


DEFAULT_MAX_VIDEO_BYTES = 80 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_QUERY_NOISE_TOKENS = frozenset({
    "file", "image", "photo", "landscape", "the", "of", "in", "at",
    "aerial", "view", "closeup", "drone",
})
_GENERIC_EXACT_TOKENS = _QUERY_NOISE_TOKENS


class MediaTooLarge(requests.RequestException):
    """무료 미디어가 설정된 다운로드 상한을 초과했다."""


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
    description: str = ""
    alternate_download_url: str = ""

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.media_id}"


def _distinctive_tokens(value: str) -> set[str]:
    """Return normalized title tokens that can identify a real subject."""
    return {
        token
        for token in re.findall(r"[^\W_]+", (value or "").lower())
        if token not in _GENERIC_EXACT_TOKENS
    }


def _canonical_anchor_tokens(value: str) -> set[str]:
    """Extract every canonical subject term while dropping search-only modifiers."""
    return {
        token
        for token in re.findall(r"[^\W_]+", (value or "").lower())
        if token not in _GENERIC_EXACT_TOKENS
    }


def exact_candidate_matches(query: str, candidate: MediaCandidate) -> bool:
    """Require a Wikimedia title to share the query's canonical subject anchor."""
    normalized_query = (query or "").removeprefix("exact:").strip()
    anchors = _canonical_anchor_tokens(normalized_query)
    evidence = _distinctive_tokens(f"{candidate.media_id} {candidate.description}")
    return bool(anchors) and anchors.issubset(evidence)


def exact_source_matches(source: dict) -> bool:
    """Revalidate persisted producer metadata instead of trusting exact_match."""
    if source.get("provider") != "wikimedia_image" or not source.get("exact_match"):
        return False
    query = source.get("keyword")
    media_id = source.get("media_id")
    if not isinstance(query, str) or not isinstance(media_id, str):
        return False
    candidate = MediaCandidate(
        provider="wikimedia_image",
        media_id=media_id,
        source_url="",
        download_url="",
        width=0,
        height=0,
        media_type="image",
        keyword=query,
        description=str(source.get("subject_evidence") or ""),
    )
    return exact_candidate_matches(query, candidate)


def _resolution_quality(width: int, height: int) -> tuple:
    adequate = width >= 720 and height >= 1280
    within_output = width <= 1080 and height <= 1920
    pixels = width * height
    if adequate and within_output:
        return 3, pixels
    if adequate:
        excess = max(width - 1080, 0) * max(height - 1920, 0)
        return 2, -excess, -pixels
    return 1, pixels


def _quality(candidate: MediaCandidate) -> tuple:
    portrait = candidate.height > candidate.width
    video = candidate.media_type == "video"
    return portrait, _resolution_quality(candidate.width, candidate.height), video


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
            _resolution_quality(
                int(item.get("width", 0)), int(item.get("height", 0))
            ),
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
                description=" ".join(filter(None, (
                    _plain_text((metadata.get("ObjectName") or {}).get("value", "")),
                    _plain_text((metadata.get("ImageDescription") or {}).get("value", "")),
                ))),
                alternate_download_url=(
                    image_info.get("url", "")
                    if image_info.get("url") != download_url else ""
                ),
            ))
        return candidates
    except (requests.RequestException, ValueError, TypeError):
        return []


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


def media_limit(media_type: str) -> int:
    if media_type == "video":
        return _positive_env_int("MEDIA_MAX_VIDEO_BYTES", DEFAULT_MAX_VIDEO_BYTES)
    return _positive_env_int("MEDIA_MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES)


def _download_candidate(candidate: MediaCandidate, output: Path) -> int:
    headers = (
        {"User-Agent": "ShortsFactory/1.0 (local sample generator)"}
        if candidate.provider == "wikimedia_image" else None
    )
    connect_timeout = _positive_env_int("MEDIA_CONNECT_TIMEOUT_SEC", 10)
    read_timeout = _positive_env_int("MEDIA_READ_TIMEOUT_SEC", 30)
    download_urls = [candidate.download_url]
    if candidate.alternate_download_url and candidate.alternate_download_url != candidate.download_url:
        download_urls.append(candidate.alternate_download_url)
    response = None
    for index, download_url in enumerate(download_urls):
        response = requests.get(
            download_url,
            headers=headers,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        )
        try:
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            has_alternate = index + 1 < len(download_urls)
            if status_code == 429 and has_alternate:
                continue
            raise
    assert response is not None
    limit = media_limit(candidate.media_type)
    try:
        declared = int(response.headers.get("Content-Length", "0"))
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        raise MediaTooLarge(f"미디어 크기 상한 초과: {declared}>{limit}")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(f"{output}.part")
    written = 0
    try:
        with partial.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                written += len(chunk)
                if written > limit:
                    raise MediaTooLarge(f"미디어 크기 상한 초과: {written}>{limit}")
                stream.write(chunk)
        partial.replace(output)
        return written
    except Exception:
        partial.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise


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
    rejected_candidates = 0
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
                if (
                    exact
                    and candidate.provider == "wikimedia_image"
                    and not exact_candidate_matches(keyword, candidate)
                ):
                    rejected_candidates += 1
                    continue
                suffix = ".mp4" if candidate.media_type == "video" else ".jpg"
                output = Path(f"{output_stem}{suffix}")
                try:
                    downloaded = _download_candidate(candidate, output)
                except MediaTooLarge:
                    rejected_candidates += 1
                    continue
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
                    "download_bytes": (
                        downloaded if isinstance(downloaded, int) else output.stat().st_size
                    ),
                    "rejected_candidates": rejected_candidates,
                }
                if candidate.license:
                    metadata["license"] = candidate.license
                if candidate.attribution:
                    metadata["attribution"] = candidate.attribution
                if candidate.description:
                    metadata["subject_evidence"] = candidate.description
                if exact and candidate.provider == "wikimedia_image":
                    metadata["exact_match"] = True
                return output, metadata

    return None, {
        "provider": "black_bg",
        "media_id": "",
        "source_url": "",
        "keyword": clean_keywords[0] if clean_keywords else "",
        "fallback": True,
        "width": 1080,
        "height": 1920,
        "download_bytes": 0,
        "rejected_candidates": rejected_candidates,
    }


def fetch_required_exact_media(
    identity: dict,
    destination: Path,
    used_ids: set[str],
) -> tuple[Path, dict]:
    """Download one licensed Wikimedia asset that matches the subject anchor."""
    raw_queries = identity.get("exact_queries") or []
    queries = list(dict.fromkeys(
        value.removeprefix("exact:").strip()
        for value in raw_queries
        if isinstance(value, str) and value.removeprefix("exact:").strip()
    ))
    output = destination if destination.suffix else destination.with_suffix(".jpg")
    rejected_candidates = 0
    for query_index, query in enumerate(queries):
        for candidate in choose_candidates(_wikimedia_image_candidates(query), used_ids):
            if not candidate.license.strip() or not exact_candidate_matches(query, candidate):
                rejected_candidates += 1
                continue
            try:
                downloaded = _download_candidate(candidate, output)
            except MediaTooLarge:
                rejected_candidates += 1
                continue
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
                "keyword": query,
                "fallback": query_index > 0,
                "width": candidate.width,
                "height": candidate.height,
                "download_bytes": (
                    downloaded if isinstance(downloaded, int) else output.stat().st_size
                ),
                "rejected_candidates": rejected_candidates,
                "license": candidate.license,
                "exact_match": True,
            }
            if candidate.attribution:
                metadata["attribution"] = candidate.attribution
            if candidate.description:
                metadata["subject_evidence"] = candidate.description
            return output, metadata

    raise RuntimeError("required exact Wikimedia media is unavailable")
