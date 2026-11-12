"""Google Neural2 ADC 어댑터와 gTTS 폴백 테스트."""
import base64
import sys
from types import SimpleNamespace

import pytest

from app.services import tts


def test_supertonic_engine_is_reused_within_process(tmp_path, monkeypatch):
    created = []

    class FakeWaveform:
        def squeeze(self):
            return b"samples"

    class FakeEngine:
        sample_rate = 44100

        def __init__(self, **kwargs):
            created.append(kwargs)

        def get_voice_style(self, voice):
            return f"style:{voice}"

        def synthesize(self, text, **kwargs):
            return FakeWaveform(), [1.0]

        def save_audio(self, waveform, output):
            from pathlib import Path

            Path(output).write_bytes(b"wav")

    monkeypatch.setitem(sys.modules, "supertonic", SimpleNamespace(TTS=FakeEngine))
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        SimpleNamespace(
            write=lambda output, samples, sample_rate, format: __import__(
                "pathlib"
            ).Path(output).write_bytes(b"wav")
        ),
    )
    monkeypatch.setattr(tts, "_SUPERTONIC_ENGINE", None)

    tts._synthesize_supertonic("첫 문장", tmp_path / "one.wav", "F3", 1.0)
    tts._synthesize_supertonic("둘째 문장", tmp_path / "two.wav", "F3", 1.0)

    assert len(created) == 1


def test_supertonic_writes_wav_container_even_when_pipeline_uses_mp3_suffix(
    tmp_path, monkeypatch
):
    seen = {}

    class FakeWaveform:
        def squeeze(self):
            return b"samples"

    class FakeEngine:
        sample_rate = 44100

        def get_voice_style(self, voice):
            return f"style:{voice}"

        def synthesize(self, text, **kwargs):
            return FakeWaveform(), [1.0]

        def save_audio(self, *args, **kwargs):
            raise AssertionError("extension-based save must not be used")

    def fake_write(output, samples, sample_rate, format):
        from pathlib import Path

        seen.update(sample_rate=sample_rate, format=format)
        Path(output).write_bytes(b"wav")

    monkeypatch.setattr(tts, "_SUPERTONIC_ENGINE", FakeEngine())
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=fake_write))
    output = tmp_path / "narration.mp3"

    tts._synthesize_supertonic("테스트 문장", output, "F3", 1.0)

    assert output.read_bytes() == b"wav"
    assert seen == {"sample_rate": 44100, "format": "WAV"}


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


def test_supertonic_provider_uses_free_korean_female_voice_without_paid_api(
    tmp_path, monkeypatch
):
    seen = {}
    monkeypatch.delenv("SUPERTONIC_VOICE", raising=False)
    monkeypatch.delenv("SUPERTONIC_SPEED", raising=False)
    monkeypatch.setattr(
        tts,
        "_synthesize_google",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Google paid TTS must not run")
        ),
    )

    def fake_supertonic(text, output, voice, speed):
        seen.update(text=text, voice=voice, speed=speed)
        output.write_bytes(b"wav")

    monkeypatch.setattr(tts, "_synthesize_supertonic", fake_supertonic, raising=False)

    result = tts.synthesize(
        "무료 여성 음성입니다.", tmp_path / "voice.mp3", provider="supertonic"
    )

    assert result.provider == "supertonic"
    assert result.voice == "F4"
    assert result.path.read_bytes() == b"wav"
    assert seen == {
        "text": "무료 여성 음성입니다.",
        "voice": "F4",
        "speed": 1.2,
    }


def test_supertonic_uses_short_natural_sentence_gap_by_default(tmp_path, monkeypatch):
    seen = {}

    class FakeWaveform:
        def squeeze(self):
            return b"samples"

    class FakeEngine:
        sample_rate = 44100

        def get_voice_style(self, voice):
            return f"style:{voice}"

        def synthesize(self, text, **kwargs):
            seen.update(kwargs)
            return FakeWaveform(), [1.0]

    monkeypatch.delenv("SUPERTONIC_SILENCE_DURATION", raising=False)
    monkeypatch.setattr(tts, "_SUPERTONIC_ENGINE", FakeEngine())
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        SimpleNamespace(
            write=lambda output, samples, sample_rate, format: __import__(
                "pathlib"
            ).Path(output).write_bytes(b"wav")
        ),
    )

    tts._synthesize_supertonic(
        "첫 문장입니다. 다음 문장입니다.", tmp_path / "voice.wav", "F4", 1.2
    )

    assert seen["speed"] == 1.2
    assert seen["silence_duration"] == 0.08


def test_supertonic_failure_stops_instead_of_silently_using_gtts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        tts,
        "_synthesize_google",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Google paid TTS must not run")
        ),
    )
    monkeypatch.setattr(
        tts,
        "_synthesize_supertonic",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("local model unavailable")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        tts,
        "_synthesize_gtts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("gTTS must not hide a Supertonic failure")
        ),
    )

    with pytest.raises(RuntimeError, match="local model unavailable"):
        tts.synthesize("무료 음성", tmp_path / "voice.mp3", provider="supertonic")


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="TTS_PROVIDER"):
        tts.synthesize("문장", tmp_path / "voice.mp3", provider="unknown")
