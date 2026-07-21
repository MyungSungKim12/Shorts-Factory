"""검증 캐시 + 회차/카테고리 + 일 업로드 한도 로직 테스트."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.fact_cache import save_verified, pick_cached, cache_size
from app.agents.researcher import SLOT_CATEGORIES


def _topic(name, method="grounded_search"):
    return {
        "topic": name, "ranking_size": 3,
        "items": [{"rank": r, "name": f"x{r}", "fact": f"{r}", "source": "s"} for r in (1, 2, 3)],
        "verification_method": method,
    }


def test_캐시_저장_후_재사용():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        save_verified(d, 1, _topic("가장 빠른 개 TOP 3"))
        assert cache_size(d, 1) == 1
        got = pick_cached(d, 1, exclude_topics=[])
        assert got["topic"] == "가장 빠른 개 TOP 3"
        assert got["verification_method"] == "verified_cache"  # 재사용 시 방식 갱신

def test_캐시_최근사용소재_제외():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        save_verified(d, 1, _topic("소재A"))
        # 유일한 소재가 exclude에 있으면 재사용 불가 → None (회차 중단 유도)
        assert pick_cached(d, 1, exclude_topics=["소재A"]) is None

def test_캐시_회차_분리():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        save_verified(d, 1, _topic("동물소재"))
        save_verified(d, 3, _topic("역사소재"))
        assert pick_cached(d, 1, []) ["topic"] == "동물소재"   # 회차1은 동물만
        assert pick_cached(d, 3, []) ["topic"] == "역사소재"

def test_회차_카테고리_매핑():
    assert SLOT_CATEGORIES[1]["name"] == "동물/펫"
    assert SLOT_CATEGORIES[4]["name"] == "미스터리"
    # 모든 카테고리에 영상 폴백어 존재
    for c in SLOT_CATEGORIES.values():
        assert c.get("visual_fallback")


def test_cached_topics_supports_warmer_exclusions():
    from app.services.fact_cache import cached_topics

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        save_verified(d, 1, _topic("animal topic"))
        save_verified(d, 2, _topic("travel topic"))

        assert cached_topics(d) == {"animal topic", "travel topic"}
