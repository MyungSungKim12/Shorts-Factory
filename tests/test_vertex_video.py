from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeImage:
    @classmethod
    def from_file(cls, *, location):
        return {"location": location}


class FakeConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeTypes:
    Image = FakeImage
    GenerateVideosConfig = FakeConfig


class FakeVideo:
    def save(self, path):
        Path(path).write_bytes(b"video")


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_videos(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(name="operations/1", done=False)


class FakeOperations:
    def get(self, operation):
        return SimpleNamespace(
            name=operation.name,
            done=True,
            response=SimpleNamespace(
                generated_videos=[SimpleNamespace(video=FakeVideo())]
            ),
        )


class FakeClient:
    def __init__(self):
        self.models = FakeModels()
        self.operations = FakeOperations()


def test_disabled_generation_never_constructs_client(tmp_path, monkeypatch):
    from app.services.vertex_video import VeoUnavailable, generate_opening_video

    monkeypatch.setenv("VEO_OPENING_ENABLED", "false")
    with pytest.raises(VeoUnavailable, match="disabled"):
        generate_opening_video(
            tmp_path / "reference.jpg", tmp_path / "output.mp4", "Richat"
        )


def test_image_generation_is_vertical_four_seconds_without_audio(
    tmp_path, monkeypatch
):
    from app.services.vertex_video import generate_opening_video

    monkeypatch.setenv("VEO_OPENING_ENABLED", "true")
    monkeypatch.setenv("VEO_MODEL", "veo-3.1-fast-generate-001")
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"image")
    client = FakeClient()

    result = generate_opening_video(
        reference,
        tmp_path / "output.mp4",
        "Richat Structure",
        client=client,
        sdk_types=FakeTypes,
        sleep_fn=lambda seconds: None,
    )

    call = client.models.calls[0]
    config = call["config"]
    assert call["image"] == {"location": str(reference)}
    assert config.duration_seconds == 4
    assert config.aspect_ratio == "9:16"
    assert config.generate_audio is False
    assert config.person_generation == "dont_allow"
    assert result.output.read_bytes() == b"video"
    assert result.estimated_cost_usd == 2.0


def test_generation_timeout_is_normalized(tmp_path, monkeypatch):
    from app.services.vertex_video import VeoGenerationFailed, generate_opening_video

    monkeypatch.setenv("VEO_OPENING_ENABLED", "true")
    monkeypatch.setenv("VEO_TIMEOUT_SEC", "1")
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"image")
    client = FakeClient()
    client.operations.get = lambda operation: operation
    clock = iter([0.0, 2.0])

    with pytest.raises(VeoGenerationFailed, match="timeout"):
        generate_opening_video(
            reference,
            tmp_path / "output.mp4",
            "Richat Structure",
            client=client,
            sdk_types=FakeTypes,
            sleep_fn=lambda seconds: None,
            monotonic_fn=lambda: next(clock),
        )


def test_motion_prompt_forbids_geometry_changes():
    from app.services.vertex_video import motion_prompt

    prompt = motion_prompt("Richat Structure")

    assert "Preserve the exact geography" in prompt
    assert "Do not add" in prompt
    assert "slow" in prompt.lower()
