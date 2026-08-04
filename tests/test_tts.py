"""Google Neural2 ADC 어댑터와 gTTS 폴백 테스트."""
import base64

import pytest

from app.services import tts


def _clear_voice_env(monkeypatch):
    for name in ("TTS_VOICE", "TTS_SPEAKING_RATE", "TTS_PITCH"):
        monkeypatch.delenv(name, raising=False)


def test_google_provider_uses_configured_defaults(tmp_path, monkeypatch):
    _clear_voice_env(monkeypatch)
    seen = {}

    def fake_google(text, output, voice, rate, pitch):
        seen.update(text=text, voice=voice, rate=rate, pitch=pitch)
        output.write_bytes(b"mp3")

    monkeypatch.setattr(tts, "_synthesize_google", fake_google)
    result = tts.synthesize("안녕하세요.", tmp_path / "voice.mp3", provider="google")

    assert result.provider == "google"
    assert result.path.read_bytes() == b"mp3"
    assert seen == {
        "text": "안녕하세요.",
        "voice": "ko-KR-Chirp3-HD-Kore",
        "rate": 1.0,
        "pitch": 0.0,
    }


def test_chirp3_request_omits_unsupported_audio_controls(tmp_path, monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"audioContent": base64.b64encode(b"mp3").decode("ascii")}

    class Session:
        def __init__(self, credentials):
            pass

        def post(self, url, json, timeout):
            seen.update(url=url, body=json, timeout=timeout)
            return Response()

    monkeypatch.setattr(tts.google.auth, "default", lambda scopes: (object(), None))
    monkeypatch.setattr(tts, "AuthorizedSession", Session)

    output = tmp_path / "chirp.mp3"
    effective_rate = tts._synthesize_google(
        "차분한 여성 내레이션",
        output,
        "ko-KR-Chirp3-HD-Kore",
        1.05,
        -0.5,
    )

    assert seen["body"]["audioConfig"] == {"audioEncoding": "MP3"}
    assert seen["body"]["voice"]["name"] == "ko-KR-Chirp3-HD-Kore"
    assert output.read_bytes() == b"mp3"
    assert effective_rate == 1.0


def test_chirp3_title_ssml_sends_explicit_pause(tmp_path, monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"audioContent": base64.b64encode(b"mp3").decode("ascii")}

    class Session:
        def __init__(self, credentials):
            pass

        def post(self, url, json, timeout):
            seen.update(body=json)
            return Response()

    monkeypatch.setattr(tts.google.auth, "default", lambda scopes: (object(), None))
    monkeypatch.setattr(tts, "AuthorizedSession", Session)

    tts._synthesize_google(
        "사하라의 눈, 리차트 구조의 비밀",
        tmp_path / "title.mp3",
        "ko-KR-Chirp3-HD-Kore",
        1.0,
        0.0,
        ssml=(
            '<speak>사하라의 눈<break time="250ms"/>'
            "리차트 구조의 비밀</speak>"
        ),
    )

    assert seen["body"]["input"] == {
        "ssml": (
            '<speak>사하라의 눈<break time="250ms"/>'
            "리차트 구조의 비밀</speak>"
        )
    }


def test_google_failure_falls_back_to_gtts(tmp_path, monkeypatch):
    _clear_voice_env(monkeypatch)
    monkeypatch.setattr(
        tts,
        "_synthesize_google",
        lambda *args: (_ for _ in ()).throw(RuntimeError("ADC unavailable")),
    )
    monkeypatch.setattr(
        tts,
        "_synthesize_gtts",
        lambda text, output: output.write_bytes(b"fallback"),
    )

    result = tts.synthesize("문장입니다.", tmp_path / "voice.mp3", provider="google")
    assert result.provider == "gtts"
    assert result.voice == "ko"
    assert result.path.read_bytes() == b"fallback"


def test_explicit_gtts_does_not_attempt_google(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tts,
        "_synthesize_google",
        lambda *args: (_ for _ in ()).throw(AssertionError("Google must not be called")),
    )
    monkeypatch.setattr(
        tts,
        "_synthesize_gtts",
        lambda text, output: output.write_bytes(text.encode("utf-8")),
    )
    result = tts.synthesize("기존 음성", tmp_path / "nested" / "voice.mp3", provider="gtts")
    assert result.provider == "gtts"
    assert result.path.exists()


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="TTS_PROVIDER"):
        tts.synthesize("문장", tmp_path / "voice.mp3", provider="unknown")
