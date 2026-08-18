"""YouTube Analytics 읽기 전용 OAuth 토큰을 로컬에서 발급한다."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.youtube_performance import authorize_analytics  # noqa: E402


def main() -> None:
    result = authorize_analytics()
    print("YouTube Analytics 읽기 인증 완료")
    print(f"token_path: {result['token_path']}")
    print(f"test_query_rows: {result['test_query_rows']}")


if __name__ == "__main__":
    main()
