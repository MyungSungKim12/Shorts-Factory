"""콘솔 인코딩이 제한돼도 비즈니스 로직을 중단하지 않는 출력."""
import sys
from typing import TextIO


def safe_print(*values, sep: str = " ", end: str = "\n", file: TextIO | None = None) -> None:
    stream = file or sys.stdout
    text = sep.join(str(value) for value in values)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    stream.write(safe + end)
    stream.flush()
