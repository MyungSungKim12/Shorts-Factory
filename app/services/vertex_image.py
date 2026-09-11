"""Vertex AI Imagen adapter for longform thumbnail poster backgrounds."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.services.credit_guard import (
    PaidFeatureDisabled,
    cancel_cost,
    commit_cost,
    paid_features_enabled,
    reserve_cost,
)


class ImagenUnavailable(RuntimeError):
    """Imagen cannot be called because it is disabled, unconfigured, or blocked."""


class ImagenGenerationFailed(RuntimeError):
    """Imagen returned no usable image."""


@dataclass(frozen=True)
class ImagenGenerationResult:
    output: Path
    model: str
    estimated_cost_usd: float


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_sdk():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ImagenUnavailable("google-genai SDK is not installed") from exc
    return genai, types


def _client_from_environment():
    genai, sdk_types = _load_sdk()
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"
    if not project:
        raise ImagenUnavailable("GOOGLE_CLOUD_PROJECT is missing")
    try:
        return genai.Client(vertexai=True, project=project, location=location), sdk_types
    except Exception as exc:
        raise ImagenUnavailable(f"Vertex AI client initialization failed: {exc}") from exc


def _estimated_cost_usd() -> float:
    try:
        return max(0.0, float(os.getenv("IMAGEN_THUMBNAIL_COST_USD", "0.04")))
    except (TypeError, ValueError):
        return 0.04


def thumbnail_poster_prompt(*, title: str, brief: str) -> str:
    subject = " ".join(str(title or "mysterious earth record").split())
    context = " ".join(str(brief or subject).split())
    return (
        "Create a photorealistic YouTube documentary thumbnail background poster, "
        "16:9 landscape, high contrast, dramatic but realistic, made for a Korean "
        "mystery documentary channel. "
        f"Subject: {subject}. Context: {context}. "
        "Composition: leave the left 45 percent dark and readable for large Korean "
        "headline text that will be added later; put the strongest visual evidence "
        "or mystery object on the right 55 percent. "
        "Lighting: stormy, cinematic, deep shadows, strong red or amber accent if "
        "the subject allows it, crisp photographic detail, not a flat card design. "
        "Style target: serious viral documentary thumbnail, not childish, not PPT, "
        "not cartoon, not infographic. "
        "Important constraints: no text, no letters, no captions, no logo, no "
        "watermark, no people, no faces, no fantasy creature, no fake UI."
    )


def generate_thumbnail_poster(
    output: Path,
    *,
    title: str,
    brief: str,
    run_id: str | None = None,
    client=None,
    sdk_types=None,
) -> ImagenGenerationResult:
    """Generate a text-free 16:9 poster background for a longform thumbnail."""
    if not _enabled(os.getenv("LONGFORM_THUMBNAIL_AI_ENABLED", "false")):
        raise ImagenUnavailable("longform thumbnail AI poster is disabled")

    if client is None:
        client, sdk_types = _client_from_environment()
    elif sdk_types is None:
        _, sdk_types = _load_sdk()

    model = os.getenv("IMAGEN_THUMBNAIL_MODEL", "imagen-3.0-generate-002").strip()
    estimated_cost = _estimated_cost_usd()
    reservation = None
    if os.getenv("AI_CREDIT_MODE"):
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        if not paid_features_enabled(data_dir):
            raise ImagenUnavailable("credit guard disabled new thumbnail poster generation")
        try:
            reservation = reserve_cost(
                data_dir,
                "imagen_thumbnail",
                estimated_cost,
                run_id or title,
            )
        except PaidFeatureDisabled as exc:
            raise ImagenUnavailable(
                f"credit guard disabled new thumbnail poster generation: {exc}"
            ) from exc

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = client.models.generate_images(
            model=model,
            prompt=thumbnail_poster_prompt(title=title, brief=brief),
            config=sdk_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
                include_rai_reason=True,
                output_mime_type="image/jpeg",
                person_generation="dont_allow",
            ),
        )
        generated = getattr(response, "generated_images", None) or []
        if not generated:
            raise ImagenGenerationFailed("Imagen returned no image")
        image = getattr(generated[0], "image", generated[0])
        if hasattr(image, "save"):
            image.save(str(destination))
        elif hasattr(image, "image_bytes"):
            destination.write_bytes(image.image_bytes)
        else:
            raise ImagenGenerationFailed("Imagen image object cannot be saved")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ImagenGenerationFailed("Imagen output file is empty")
    except (ImagenUnavailable, ImagenGenerationFailed):
        if reservation is not None:
            cancel_cost(reservation)
        raise
    except Exception as exc:
        if reservation is not None:
            cancel_cost(reservation)
        raise ImagenGenerationFailed(f"Imagen request failed: {exc}") from exc

    if reservation is not None:
        commit_cost(reservation, actual_usd=estimated_cost)

    return ImagenGenerationResult(
        output=destination,
        model=model,
        estimated_cost_usd=estimated_cost,
    )
