"""콘텐츠 포맷 선택을 한곳에서 정규화한다."""
import os
from typing import Literal


ContentFormat = Literal["ranking", "story"]


def get_content_format(value: str | None = None) -> ContentFormat:
    """명시값 또는 환경변수에서 포맷을 읽으며 기본값은 기존 ranking이다."""
    selected = (value or os.getenv("CONTENT_FORMAT", "ranking")).strip().lower()
    if selected not in {"ranking", "story"}:
        raise ValueError(f"지원하지 않는 CONTENT_FORMAT: {selected}")
    return selected
