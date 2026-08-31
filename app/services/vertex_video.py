"""검증된 실제 이미지만 입력으로 받는 Vertex AI Veo 어댑터."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.services.credit_guard import (
    PaidFeatureDisabled,
    cancel_cost,
    commit_cost,
    paid_features_enabled,
    reserve_cost,
)


class VeoUnavailable(RuntimeError):
    """설정, SDK 또는 인증 문제로 Veo를 호출할 수 없음."""


class VeoGenerationFailed(RuntimeError):
    """Veo 장기 작업이 실패하거나 유효한 영상을 반환하지 않음."""


@dataclass(frozen=True)
class VeoGenerationResult:
    output: Path
    model: str
    duration_sec: int
    estimated_cost_usd: float


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _video_cost_usd(model: str, duration: int, resolution: str = "720p") -> float:
    name = model.lower()
    if "lite" in name:
        per_second = 0.03 if resolution == "720p" else 0.05
    elif "fast" in name:
        per_second = 0.08 if resolution == "720p" else 0.10
    else:
        per_second = 0.20
    return round(duration * per_second, 4)


def _veo_resolution() -> str:
    value = os.getenv("VEO_RESOLUTION", "720p").strip().lower()
    return value if value in {"720p", "1080p"} else "720p"


def _cost_feature_name(model: str) -> str:
    name = model.lower()
    if "lite" in name:
        return "veo_3_1_lite"
    if "fast" in name:
        return "veo_3_1_fast"
    return "veo_3_1"


def motion_prompt(subject: str) -> str:
    name = " ".join(str(subject or "verified subject").split())
    return (
        f"Documentary motion from this verified real image of {name}. "
        "Preserve the exact geography, structure, proportions, colors, and identity "
        "shown in the first frame. Add only a very slow cinematic camera push or "
        "gentle parallax and subtle natural atmospheric motion already implied by "
        "the image. Do not add, remove, reshape, invent, or relocate any object, "
        "person, animal, building, terrain feature, text, logo, or landmark. "
        "No morphing, no fantasy, no dramatic transformation, no scene change."
    )


def _load_sdk():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise VeoUnavailable("google-genai SDK is not installed") from exc
    return genai, types


def _client_from_environment():
    genai, sdk_types = _load_sdk()
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"
    if not project:
        raise VeoUnavailable("GOOGLE_CLOUD_PROJECT is missing")
    try:
        return genai.Client(vertexai=True, project=project, location=location), sdk_types
    except Exception as exc:
        raise VeoUnavailable(f"Vertex AI client initialization failed: {exc}") from exc


def generate_opening_video(
    reference_image: Path,
    output: Path,
    subject: str,
    *,
    client=None,
    sdk_types=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> VeoGenerationResult:
    """실제 이미지를 첫 프레임으로 사용해 4초 무음 세로 영상을 생성한다."""
    if not _enabled(os.getenv("VEO_OPENING_ENABLED", "false")):
        raise VeoUnavailable("Veo opening is disabled")
    reference = Path(reference_image)
    if not reference.is_file():
        raise VeoUnavailable(f"verified reference image is missing: {reference}")

    if client is None:
        client, sdk_types = _client_from_environment()
    elif sdk_types is None:
        _, sdk_types = _load_sdk()

    model = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-001").strip()
    duration = 4
    resolution = _veo_resolution()
    estimated_cost = _video_cost_usd(model, duration, resolution)
    reservation = None
    if os.getenv("AI_CREDIT_MODE"):
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        if not paid_features_enabled(data_dir):
            raise VeoUnavailable("credit guard disabled new Veo generation")
        try:
            reservation = reserve_cost(
                data_dir,
                _cost_feature_name(model),
                estimated_cost,
                os.getenv("PIPELINE_RUN_ID", subject),
            )
        except PaidFeatureDisabled as exc:
            raise VeoUnavailable(f"credit guard disabled new Veo generation: {exc}") from exc
    timeout = max(1.0, float(os.getenv("VEO_TIMEOUT_SEC", "900")))
    poll = max(1.0, float(os.getenv("VEO_POLL_SEC", "15")))
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        operation = client.models.generate_videos(
            model=model,
            prompt=motion_prompt(subject),
            image=sdk_types.Image.from_file(location=str(reference)),
            config=sdk_types.GenerateVideosConfig(
                number_of_videos=1,
                duration_seconds=duration,
                aspect_ratio="9:16",
                resolution=resolution,
                generate_audio=False,
                person_generation="dont_allow",
                negative_prompt=(
                    "morphing, invented geography, geometry changes, new objects, "
                    "people, faces, animals, buildings, text, subtitles, logos, "
                    "fantasy, scene transitions"
                ),
            ),
        )
        started = monotonic_fn()
        while not getattr(operation, "done", False):
            if monotonic_fn() - started > timeout:
                raise VeoGenerationFailed("Veo generation timeout")
            sleep_fn(poll)
            operation = client.operations.get(operation)

        error = getattr(operation, "error", None)
        if error:
            raise VeoGenerationFailed(f"Veo operation failed: {error}")
        response = getattr(operation, "response", None) or getattr(
            operation, "result", None
        )
        generated = getattr(response, "generated_videos", None) if response else None
        video = getattr(generated[0], "video", None) if generated else None
        if video is None:
            raise VeoGenerationFailed("Veo returned no video")
        video.save(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise VeoGenerationFailed("Veo output file is empty")
    except (VeoUnavailable, VeoGenerationFailed):
        if reservation is not None:
            cancel_cost(reservation)
        raise
    except Exception as exc:
        if reservation is not None:
            cancel_cost(reservation)
        raise VeoGenerationFailed(f"Veo request failed: {exc}") from exc

    if reservation is not None:
        commit_cost(reservation, actual_usd=estimated_cost)

    return VeoGenerationResult(
        output=destination,
        model=model,
        duration_sec=duration,
        estimated_cost_usd=estimated_cost,
    )
