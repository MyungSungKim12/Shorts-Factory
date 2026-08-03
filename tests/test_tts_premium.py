from app.services import tts


def test_auto_provider_uses_prompt_controlled_gemini_female_voice_in_premium_mode(
    tmp_path, monkeypatch
):
    seen = {}
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GEMINI_TTS_STYLE_PROMPT", raising=False)
    monkeypatch.setattr(tts, "paid_features_enabled", lambda data_dir: True)
    monkeypatch.setattr(tts, "reserve_cost", lambda *args, **kwargs: object())
    monkeypatch.setattr(tts, "commit_cost", lambda *args, **kwargs: None)

    def fake_gemini(text, output, model, voice, prompt):
        seen.update(text=text, model=model, voice=voice, prompt=prompt)
        output.write_bytes(b"gemini")

    monkeypatch.setattr(tts, "_synthesize_gemini_tts", fake_gemini)
    result = tts.synthesize("놀라운 지구 이야기입니다.", tmp_path / "voice.mp3", provider="auto")

    assert result.provider == "gemini_tts"
    assert seen["model"] == "gemini-2.5-flash-tts"
    assert seen["voice"] == "Leda"
    assert "여성" in seen["prompt"]
    assert "미스터리" in seen["prompt"]


def test_auto_provider_uses_chirp_without_gemini_call_in_free_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TTS_VOICE", raising=False)
    monkeypatch.setattr(tts, "paid_features_enabled", lambda data_dir: False)
    monkeypatch.setattr(
        tts,
        "_synthesize_gemini_tts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Gemini TTS must not run")
        ),
    )
    monkeypatch.setattr(
        tts,
        "_synthesize_google",
        lambda text, output, voice, rate, pitch: output.write_bytes(b"chirp"),
    )

    result = tts.synthesize("무료 모드", tmp_path / "voice.mp3", provider="auto")

    assert result.provider == "google"
    assert result.voice == "ko-KR-Chirp3-HD-Leda"
