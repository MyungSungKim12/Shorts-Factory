"""핵심 계약·검증 로직 회귀 테스트.

실수 비용이 큰 부분(순위 계약, JSON 추출, 검증 방식, 길이)을 고정한다.
그동안 rank=0, JSON Extra data, 60초 초과로 반복 회귀했던 것들을 영구 방지.
실행: cd shorts-factory-be && venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import validate_topic, validate_script, TopicContract
from app.services.json_extract import extract_json


# ---------- topic 계약 ----------

def _good_topic(**over):
    d = {
        "topic": "세계에서 가장 매운 고추 TOP 5",
        "ranking_size": 5,
        "items": [
            {"rank": r, "name": f"고추{r}", "fact": f"스코빌 {r}만", "source": "기네스"}
            for r in range(1, 6)
        ],
        "verification_method": "grounded_search",
    }
    d.update(over)
    return d


def test_topic_정상():
    t = validate_topic(_good_topic())
    assert t["ranking_size"] == 5

def test_topic_순위누락_차단():
    bad = _good_topic()
    bad["items"] = bad["items"][:4]  # 4개만 → 1~5 불완전
    with pytest.raises(Exception):
        validate_topic(bad)

def test_topic_자리표시자_차단():
    bad = _good_topic()
    bad["items"][0]["name"] = "..."
    with pytest.raises(Exception):
        validate_topic(bad)

def test_topic_순위중복_차단():
    bad = _good_topic()
    bad["items"][0]["rank"] = 2  # rank 2 중복, 1 누락
    with pytest.raises(Exception):
        validate_topic(bad)

def test_검증방식_업로드가능여부():
    # 규칙 완화(2026-07-16): grounded/cache/model_memory 모두 업로드 허용 (불변 기록 소재 한정)
    assert TopicContract.model_validate(_good_topic()).is_uploadable()
    assert TopicContract.model_validate(_good_topic(verification_method="verified_cache")).is_uploadable()
    assert TopicContract.model_validate(_good_topic(verification_method="model_memory")).is_uploadable()
    # 알 수 없는 방식은 여전히 차단
    bad = TopicContract.model_validate(_good_topic(verification_method="hallucinated"))
    assert not bad.is_uploadable()


# ---------- script 계약 ----------

def _good_script(**over):
    scenes = [{"n": 1, "rank": 0, "narration": "훅 문장입니다", "duration_sec": 3}]
    for i, r in enumerate([5, 4, 3, 2, 1], start=2):
        scenes.append({"n": i, "rank": r, "narration": f"{r}위 설명입니다", "duration_sec": 7})
    d = {"title": "테스트 제목입니다", "total_duration_sec": 38, "scenes": scenes}
    d.update(over)
    return d

def test_script_정상():
    s = validate_script(_good_script())
    assert s["title"]

def test_rank0_은_null로_정규화():
    s = validate_script(_good_script())
    ranks = [sc["rank"] for sc in s["scenes"]]
    assert ranks == [None, 5, 4, 3, 2, 1]  # hook의 0 → None

def test_순위_역순아니면_차단():
    bad = _good_script()
    bad["scenes"][1]["rank"] = 1
    bad["scenes"][-1]["rank"] = 5  # 오름차순 됨
    with pytest.raises(Exception):
        validate_script(bad)

def test_duration_합계로_자동보정():
    s = validate_script(_good_script(total_duration_sec=99))  # 실제 합계는 38
    assert abs(s["total_duration_sec"] - 38) < 0.1

def test_제목_100자초과_차단():
    with pytest.raises(Exception):
        validate_script(_good_script(title="가" * 101))


# ---------- JSON 견고 추출 ----------

def test_json_후행텍스트_제거():
    assert extract_json('{"a": 1}\n출처: 위키 [1][2]')["a"] == 1

def test_json_코드펜스_제거():
    assert extract_json('```json\n{"a": 2}\n```')["a"] == 2

def test_json_머리말_제거():
    assert extract_json('다음은 결과입니다:\n{"a": 3}\n감사합니다')["a"] == 3

def test_json_문자열내_중괄호():
    assert extract_json('{"a": "중괄호 {포함}"} 쓰레기')["a"] == "중괄호 {포함}"

def test_json_빈응답_예외():
    with pytest.raises(Exception):
        extract_json("")
