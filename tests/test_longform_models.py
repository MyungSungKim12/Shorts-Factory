import pytest


def _longform_script(**overrides):
    scenes = [
        {
            "n": 1,
            "role": "hook",
            "chapter_title": "사라진 도시의 첫 단서",
            "narration": "첫 기록은 위성사진의 이상한 직선에서 시작됩니다.",
            "visuals": ["ancient desert ruin satellite image", "stone wall aerial"],
            "duration_sec": 35,
        },
        {
            "n": 2,
            "role": "context",
            "chapter_title": "왜 이상하게 보였나",
            "narration": "주변 지형과 달리 이 선은 일정한 각도로 이어졌습니다.",
            "visuals": ["desert plateau aerial", "archaeological survey"],
            "duration_sec": 45,
        },
        {
            "n": 3,
            "role": "evidence",
            "chapter_title": "남은 흔적",
            "narration": "조사 기록에는 흙벽과 물길의 흔적이 함께 남았습니다.",
            "visuals": ["ancient canal remains", "earthen wall close up"],
            "duration_sec": 50,
        },
        {
            "n": 4,
            "role": "mechanism",
            "chapter_title": "가능한 설명",
            "narration": "가장 조심스러운 해석은 방어와 물 관리가 결합된 구조입니다.",
            "visuals": ["ancient irrigation diagram", "desert fortress ruins"],
            "duration_sec": 55,
        },
        {
            "n": 5,
            "role": "counterpoint",
            "chapter_title": "아직 풀리지 않은 부분",
            "narration": "하지만 모든 선이 같은 시기에 만들어졌다는 증거는 부족합니다.",
            "visuals": ["archaeologist field notes", "old map texture"],
            "duration_sec": 45,
        },
        {
            "n": 6,
            "role": "payoff",
            "chapter_title": "기록이 말하는 것",
            "narration": "이 유적은 사라진 도시가 환경을 어떻게 읽었는지 보여줍니다.",
            "visuals": ["ruined city sunset", "desert archaeology"],
            "duration_sec": 50,
        },
        {
            "n": 7,
            "role": "close",
            "chapter_title": "다음 질문",
            "narration": "남은 질문은 이 구조가 어디까지 이어졌는지입니다.",
            "visuals": ["aerial desert mystery", "satellite map"],
            "duration_sec": 40,
        },
    ]
    value = {
        "format": "longform",
        "title": "사막 아래 사라진 도시의 흔적",
        "description": "사막 유적의 실제 기록을 따라가는 미스터리 다큐멘터리입니다.",
        "tags": ["사막", "고대도시", "미스터리"],
        "hook": "위성사진 속 직선은 왜 사막 한가운데 남았을까요?",
        "scenes": scenes,
        "cta": "이런 지구의 기록이 더 궁금하다면 구독과 좋아요 부탁드립니다.",
    }
    value.update(overrides)
    return value


def test_validate_longform_script_accepts_documentary_duration():
    from app.models import validate_longform_script

    result = validate_longform_script(_longform_script())

    assert result["format"] == "longform"
    assert result["total_duration_sec"] == 320


def test_validate_longform_script_rejects_short_video():
    from app.models import validate_longform_script

    script = _longform_script()
    for scene in script["scenes"]:
        scene["duration_sec"] = 20

    with pytest.raises(ValueError, match="롱폼"):
        validate_longform_script(script)


def test_validate_longform_script_requires_sequential_chapters():
    from app.models import validate_longform_script

    script = _longform_script()
    script["scenes"][2]["n"] = 9

    with pytest.raises(ValueError, match="연속"):
        validate_longform_script(script)


def test_validate_longform_script_rejects_vague_units():
    from app.models import validate_longform_script

    script = _longform_script()
    script["scenes"][1]["narration"] = "아주 오래전 엄청 큰 규모의 흔적이 많이 남았습니다."

    with pytest.raises(ValueError, match="애매한 표현"):
        validate_longform_script(script)


def test_validate_longform_script_preserves_selected_style():
    from app.models import validate_longform_script

    result = validate_longform_script(_longform_script(style_id="clean_news"))

    assert result["style_id"] == "clean_news"


def test_validate_longform_script_defaults_to_clean_news_style():
    from app.models import validate_longform_script

    result = validate_longform_script(_longform_script())

    assert result["style_id"] == "clean_news"
