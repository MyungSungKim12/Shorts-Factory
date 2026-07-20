"""Windows cp949 등 제한된 콘솔에서도 로그가 파이프라인을 중단하지 않는지 검증."""
import io

from app.console import safe_print


def test_safe_print_replaces_unencodable_symbols():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
    safe_print("⚠️ 제공자 실패 → 폴백", file=stream)
    stream.flush()
    assert b"provider" not in raw.getvalue()
    assert b"?" in raw.getvalue()
