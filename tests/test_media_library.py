"""무료 미디어 후보 선별, 출처 기록, 중복 방지 테스트."""
import asyncio

import pytest

from app.services import media_library
from app.services.media_library import MediaCandidate, choose_candidate, exact_candidate_matches


def candidate(media_id, width, height, provider="pexels_video", media_type="video", keyword="desert lake"):
    return MediaCandidate(
        provider=provider,
        media_id=str(media_id),
        source_url=f"https://source/{media_id}",
        download_url=f"https://download/{media_id}",
        width=width,
        height=height,
        media_type=media_type,
        keyword=keyword,
    )


def test_portrait_unique_candidate_wins():
    chosen = choose_candidate(
        [candidate(1, 1920, 1080), candidate(2, 1080, 1920), candidate(3, 720, 1280)],
        {"pexels_video:2"},
    )
    assert chosen.media_id == "3"


def test_higher_resolution_wins_between_portrait_candidates():
    chosen = choose_candidate([candidate(1, 720, 1280), candidate(2, 1080, 1920)], set())
    assert chosen.media_id == "2"


def test_output_sized_variant_wins_over_4k():
    chosen = media_library._best_variant([
        {"link": "4k", "width": 2160, "height": 3840},
        {"link": "output", "width": 1080, "height": 1920},
        {"link": "small", "width": 720, "height": 1280},
    ], "link")
    assert chosen["link"] == "output"


def test_all_duplicates_return_none():
    assert choose_candidate([candidate(1, 1080, 1920)], {"pexels_video:1"}) is None


def test_fetch_records_provenance_and_marks_id_used(tmp_path, monkeypatch):
    picked = candidate(9, 1080, 1920)
    monkeypatch.setattr(media_library, "_wikimedia_image_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_video_candidates", lambda keyword: [picked])
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])

    def fake_download(item, output):
        output.write_bytes(b"video")

    monkeypatch.setattr(media_library, "_download_candidate", fake_download)
    monkeypatch.setattr(media_library, "_is_usable_download", lambda path: True)
    used = set()
    path, meta = asyncio.run(
        media_library.fetch_story_media(["desert lake", "dry desert"], tmp_path / "shot", used)
    )

    assert path == tmp_path / "shot.mp4"
    assert used == {"pexels_video:9"}
    assert meta == {
        "provider": "pexels_video",
        "media_id": "9",
        "source_url": "https://source/9",
        "keyword": "desert lake",
        "fallback": False,
        "width": 1080,
        "height": 1920,
        "download_bytes": 5,
        "rejected_candidates": 0,
    }


def test_fetch_uses_next_keyword_without_reusing_media(tmp_path, monkeypatch):
    duplicate = candidate(1, 1080, 1920, keyword="first keyword")
    fresh = candidate(2, 1080, 1920, keyword="second keyword")
    monkeypatch.setattr(media_library, "_wikimedia_image_candidates", lambda keyword: [])

    def videos(keyword):
        return [duplicate] if keyword == "first keyword" else [fresh]

    monkeypatch.setattr(media_library, "_pexels_video_candidates", videos)
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_download_candidate", lambda item, output: output.write_bytes(b"ok"))
    monkeypatch.setattr(media_library, "_is_usable_download", lambda path: True)

    path, meta = asyncio.run(
        media_library.fetch_story_media(
            ["first keyword", "second keyword"],
            tmp_path / "fallback",
            {"pexels_video:1"},
        )
    )
    assert path.name == "fallback.mp4"
    assert meta["media_id"] == "2"
    assert meta["fallback"] is True


def test_fetch_returns_black_metadata_when_no_source_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(media_library, "_wikimedia_image_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])
    path, meta = asyncio.run(media_library.fetch_story_media(["missing place"], tmp_path / "none", set()))
    assert path is None
    assert meta["provider"] == "black_bg"
    assert meta["fallback"] is True


def test_fetch_tries_next_candidate_after_invalid_download(tmp_path, monkeypatch):
    broken = candidate(1, 1080, 1920)
    valid = candidate(2, 1080, 1920)
    monkeypatch.setattr(
        media_library, "_pexels_video_candidates", lambda keyword: [broken, valid]
    )
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])
    monkeypatch.setattr(
        media_library,
        "_is_usable_download",
        lambda path: path.read_bytes() == b"valid",
        raising=False,
    )
    monkeypatch.setattr(
        media_library,
        "_download_candidate",
        lambda item, output: output.write_bytes(
            b"broken" if item.media_id == "1" else b"valid"
        ),
    )

    path, metadata = asyncio.run(
        media_library.fetch_story_media(["storm"], tmp_path / "shot", set())
    )

    assert path.read_bytes() == b"valid"
    assert metadata["media_id"] == "2"


def test_exact_keyword_prefers_licensed_wikimedia_image(tmp_path, monkeypatch):
    exact = MediaCandidate(
        provider="wikimedia_image",
        media_id="File:Blood Falls.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Blood_Falls.jpg",
        download_url="https://upload.wikimedia.org/blood-falls.jpg",
        width=1600,
        height=1200,
        media_type="image",
        keyword="Blood Falls Antarctica",
        license="CC BY-SA 4.0",
        attribution="Jane Scientist",
        description="Blood Falls Antarctica",
    )
    generic = candidate(88, 1080, 1920)
    monkeypatch.setattr(media_library, "_wikimedia_image_candidates", lambda keyword: [exact])
    monkeypatch.setattr(media_library, "_pexels_video_candidates", lambda keyword: [generic])
    monkeypatch.setattr(media_library, "_pixabay_video_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_pexels_photo_candidates", lambda keyword: [])
    monkeypatch.setattr(media_library, "_download_candidate", lambda item, output: output.write_bytes(b"image"))
    monkeypatch.setattr(media_library, "_is_usable_download", lambda path: True)

    path, meta = asyncio.run(media_library.fetch_story_media(
        ["exact: Blood Falls Antarctica", "antarctica glacier"],
        tmp_path / "exact-shot",
        set(),
    ))
    assert path.suffix == ".jpg"
    assert meta["provider"] == "wikimedia_image"
    assert meta["license"] == "CC BY-SA 4.0"
    assert meta["attribution"] == "Jane Scientist"


def test_exact_candidate_rejects_unrelated_title():
    moon = MediaCandidate(
        provider="wikimedia_image",
        media_id="File:Moon surface.jpg",
        source_url="x",
        download_url="x",
        width=1200,
        height=1600,
        media_type="image",
        keyword="Richat Structure Mauritania",
    )

    assert exact_candidate_matches("Richat Structure Mauritania", moon) is False


@pytest.mark.parametrize(
    ("query", "media_id"),
    [
        ("Richat Structure Mauritania", "File:Flag of Mauritania.jpg"),
        ("Eiffel Tower Paris", "File:Paris skyline at dusk.jpg"),
        ("Lake Baikal Russia", "File:Lake Tahoe in summer.jpg"),
        ("Mount Fuji Japan", "File:Mount Everest from base camp.jpg"),
        ("Blood Falls Antarctica", "File:Blood donation campaign.jpg"),
        ("Great Barrier Reef", "File:Great Barrier Island.jpg"),
        ("Golden Gate Bridge", "File:Golden Gate Park.jpg"),
        ("desert lake aerial", "File:Sahara desert dunes.jpg"),
    ],
)
def test_exact_candidate_rejects_context_only_overlap(query, media_id):
    context_only = MediaCandidate(
        provider="wikimedia_image",
        media_id=media_id,
        source_url="x",
        download_url="x",
        width=1200,
        height=1600,
        media_type="image",
        keyword=query,
    )

    assert exact_candidate_matches(query, context_only) is False


def test_required_exact_media_skips_unrelated_wikimedia_candidate(tmp_path, monkeypatch):
    unrelated = candidate(
        "File:Moon surface.jpg", 1200, 1600,
        provider="wikimedia_image", media_type="image",
        keyword="Richat Structure Mauritania",
    )
    monkeypatch.setattr(media_library, "_wikimedia_image_candidates", lambda query: [unrelated])

    with pytest.raises(RuntimeError, match="exact Wikimedia"):
        media_library.fetch_required_exact_media(
            {"exact_queries": ["exact: Richat Structure Mauritania"]},
            tmp_path / "required-exact",
            set(),
        )


def test_wikimedia_download_sends_identifying_user_agent(tmp_path, monkeypatch):
    exact = MediaCandidate(
        provider="wikimedia_image", media_id="File:Blood Falls.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Blood_Falls.jpg",
        download_url="https://upload.wikimedia.org/blood-falls.jpg",
        width=1600, height=1200, media_type="image", keyword="Blood Falls Antarctica",
        license="Public domain", attribution="US Antarctic Program",
        description="Blood Falls Antarctica",
    )
    captured = {}

    class Response:
        headers = {"Content-Length": "5"}
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            yield b"image"

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(media_library.requests, "get", fake_get)
    media_library._download_candidate(exact, tmp_path / "image.jpg")
    assert "ShortsFactory" in captured["headers"]["User-Agent"]


def test_download_rejects_oversized_content_length_without_reading(tmp_path, monkeypatch):
    picked = candidate(31, 1080, 1920)
    output = tmp_path / "large.mp4"

    class Response:
        headers = {"Content-Length": "7"}
        content = b"ignored"
        iterated = False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            self.iterated = True
            yield b"ignored"

    response = Response()
    monkeypatch.setenv("MEDIA_MAX_VIDEO_BYTES", "6")
    monkeypatch.setattr(media_library.requests, "get", lambda *args, **kwargs: response)

    with pytest.raises(media_library.MediaTooLarge):
        media_library._download_candidate(picked, output)

    assert response.iterated is False
    assert not output.exists()
    assert not (tmp_path / "large.mp4.part").exists()


def test_download_stops_unknown_length_stream_at_limit(tmp_path, monkeypatch):
    picked = candidate(32, 1080, 1920)
    output = tmp_path / "stream.mp4"

    class Response:
        headers = {}
        content = b"12345678"

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"1234"
            yield b"5678"

    monkeypatch.setenv("MEDIA_MAX_VIDEO_BYTES", "6")
    monkeypatch.setattr(media_library.requests, "get", lambda *args, **kwargs: Response())

    with pytest.raises(media_library.MediaTooLarge):
        media_library._download_candidate(picked, output)

    assert not output.exists()
    assert not (tmp_path / "stream.mp4.part").exists()
