"""웹 검색 서비스 (Claude가 내부적으로 사용)."""


def search_ranking_topics(keywords: list, limit: int = 5) -> list:
    """
    랭킹 관련 검색어로 수집할 후보 데이터.
    실제 웹 크롤링은 Claude가 프롬프트 내에서 처리하고,
    이 함수는 외부 API 호출 시 사용될 수 있는 스텁이다.

    Args:
        keywords: 검색 키워드 리스트
        limit: 반환할 결과 수

    Returns:
        검색 결과 리스트 (현재는 스텁)
    """
    # 실제 구현은 나중에: requests + BeautifulSoup 또는 Google Search API
    # 현재는 Claude가 프롬프트에서 직접 처리
    return []
