"""교체 가능한 한국어 TTS 공급자 — Google Neural2 우선, gTTS 폴백."""
import base64
import os
from dataclasses import dataclass
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession
from gtts import gTTS

from app.console import safe_print


@dataclass(frozen=True)
class TTSResult:
    path: Path
    provider: str
    voice: str
    speaking_rate: float


def _supports_audio_controls(voice: str) -> bool:
    """Cloud TTS 음성이 속도와 음높이 제어를 지원하는지 반환한다."""
    return "-Chirp3-HD-" not in voice


def _effective_rate(voice: str, requested_rate: float) -> float:
    return requested_rate if _supports_audio_controls(voice) else 1.0


def _synthesize_google(
    text: str,
    output: Path,
    voice: str,
    rate: float,
    pitch: float,
    ssml: str | None = None,
) -> float:
    """ADC 인증으로 Google Cloud Text-to-Speech REST API를 호출한다."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    audio_config = {"audioEncoding": "MP3"}
    if _supports_audio_controls(voice):
        audio_config.update({"speakingRate": rate, "pitch": pitch})
    speech_input = {"ssml": ssml} if ssml else {"text": text}
    response = AuthorizedSession(credentials).post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        json={
            "input": speech_input,
            "voice": {"languageCode": "ko-KR", "name": voice},
            "audioConfig": audio_config,
        },
        timeout=30,
    )
    response.raise_for_status()
    audio_content = response.json().get("audioContent", "")
    if not audio_content:
        raise RuntimeError("Google TTS 응답에 audioContent가 없습니다")
    output.write_bytes(base64.b64decode(audio_content))
    return _effective_rate(voice, rate)


def _synthesize_gtts(text: str, output: Path) -> None:
    gTTS(text=text, lang="ko", slow=False).save(str(output))


def synthesize(
    text: str,
    output_path: Path,
    provider: str | None = None,
    ssml: str | None = None,
) -> TTSResult:
    """음성을 합성하고 실제 사용된 공급자를 반환한다."""
    selected = (provider or os.getenv("TTS_PROVIDER", "gtts")).strip().lower()
    if selected not in {"google", "gtts"}:
        raise ValueError(f"지원하지 않는 TTS_PROVIDER: {selected}")

    voice = os.getenv("TTS_VOICE", "ko-KR-Chirp3-HD-Kore")
    rate = float(os.getenv("TTS_SPEAKING_RATE", "1.0"))
    pitch = float(os.getenv("TTS_PITCH", "0.0"))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if selected == "google":
        try:
            if ssml:
                effective_rate = _synthesize_google(
                    text, output_path, voice, rate, pitch, ssml=ssml
                )
            else:
                effective_rate = _synthesize_google(
                    text, output_path, voice, rate, pitch
                )
            if not isinstance(effective_rate, (int, float)):
                effective_rate = _effective_rate(voice, rate)
            return TTSResult(output_path, "google", voice, float(effective_rate))
        except Exception as exc:
            safe_print(f"  ⚠️ Google TTS 실패, gTTS 폴백: {exc}")

    _synthesize_gtts(text, output_path)
    return TTSResult(output_path, "gtts", "ko", 1.0)
