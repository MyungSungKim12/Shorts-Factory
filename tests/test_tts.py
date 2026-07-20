"""Google Neural2 ADC 어댑터와 gTTS 폴백 테스트."""
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
        "voice": "ko-KR-Neural2-C",
        "rate": 1.05,
        "pitch": -0.5,
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
