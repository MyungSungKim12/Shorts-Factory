from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeTypes:
    GenerateImagesConfig = FakeConfig


class FakeGeneratedImage:
    def __init__(self):
        self.image = self

    def save(self, path):
        Path(path).write_bytes(b"poster")


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_images(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(generated_images=[FakeGeneratedImage()])


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


def test_disabled_thumbnail_poster_never_constructs_client(tmp_path, monkeypatch):
    from app.services.vertex_image import ImagenUnavailable, generate_thumbnail_poster

    monkeypatch.setenv("LONGFORM_THUMBNAIL_AI_ENABLED", "false")

    with pytest.raises(ImagenUnavailable, match="disabled"):
        generate_thumbnail_poster(
            tmp_path / "poster.jpg",
            title="남극의 피폭포",
            brief="붉은 물이 얼지 않는 남극 빙하 미스터리",
        )


def test_thumbnail_poster_uses_landscape_youtube_prompt(tmp_path, monkeypatch):
    from app.services.vertex_image import generate_thumbnail_poster

    monkeypatch.setenv("LONGFORM_THUMBNAIL_AI_ENABLED", "true")
    monkeypatch.delenv("AI_CREDIT_MODE", raising=False)
    monkeypatch.setenv("IMAGEN_THUMBNAIL_MODEL", "imagen-3.0-generate-002")
    client = FakeClient()

    result = generate_thumbnail_poster(
        tmp_path / "poster.jpg",
        title="남극의 피폭포",
        brief="붉은 물이 얼지 않는 남극 빙하 미스터리",
        client=client,
        sdk_types=FakeTypes,
    )

    call = client.models.calls[0]
    config = call["config"]
    assert result.output.read_bytes() == b"poster"
    assert result.model == "imagen-3.0-generate-002"
    assert result.estimated_cost_usd > 0
    assert "photorealistic YouTube documentary thumbnail background poster" in call["prompt"]
    assert "no text" in call["prompt"].lower()
    assert config.aspect_ratio == "16:9"
    assert config.number_of_images == 1
    assert config.output_mime_type == "image/jpeg"


def test_credit_free_mode_prevents_new_thumbnail_poster(tmp_path, monkeypatch):
    from app.services.vertex_image import ImagenUnavailable, generate_thumbnail_poster

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LONGFORM_THUMBNAIL_AI_ENABLED", "true")
    monkeypatch.setenv("AI_CREDIT_MODE", "free")

    with pytest.raises(ImagenUnavailable, match="credit"):
        generate_thumbnail_poster(
            tmp_path / "poster.jpg",
            title="남극의 피폭포",
            brief="붉은 물이 얼지 않는 남극 빙하 미스터리",
            client=FakeClient(),
            sdk_types=FakeTypes,
        )
