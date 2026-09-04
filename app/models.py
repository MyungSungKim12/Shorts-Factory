"""에이전트 입출력 계약 (Pydantic) — 불량 산출물이 파이프라인 하류로 가는 것을 차단.

agents/*.md의 "입출력 계약" 섹션에 대한 단일 코드 구현.
검증 실패 = ValueError 발생 = 파이프라인 중단 (불량 영상이 업로드되는 것보다 하루 쉬는 게 낫다).
"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_PLACEHOLDERS = {"...", "항목명", "N/A", "없음", "unknown", "TBD"}
_TITLE_INSTRUCTION_PATTERNS = (
    re.compile(r"^\s*\d+\s*자\s*(?:이하|이내)\s*제목\s*[:：]?"),
    re.compile(r"^\s*제목\s*[:：]"),
    re.compile(r"^\s*글자\s*수\s*\d+\s*자\s*(?:이하|이내)?"),
)


def validate_public_title(value: object) -> str:
    """모델의 JSON 작성 지시가 실제 공개 제목으로 새는 것을 차단한다."""
    title = " ".join(str(value or "").split())
    if any(pattern.search(title) for pattern in _TITLE_INSTRUCTION_PATTERNS):
        raise ValueError("제목 지시문이 실제 제목에 포함됨")
    return title


# 업로드가 허용되는 검증 방식.
# 규칙 완화(2026-07-16): 그라운딩 할당량 소진 시 채널 유지를 위해, 불변 기록 소재에 한해
# model_memory도 허용. (리서처의 보수 모드 프롬프트가 '불변 기록만' 쓰도록 강제)
UPLOADABLE_VERIFICATION = {"grounded_search", "verified_cache", "model_memory"}

_ABSTRACT_SPACE_TOPIC_KEYWORDS = (
    "암흑물질",
    "블랙홀",
    "우주론",
    "은하",
    "행성 대기",
    "금성 대기",
    "타이탄",
    "토성",
    "목성",
    "외계행성",
    "중력파",
    "오우무아무아",
    "우주 최고 에너지",
    "dark matter",
    "black hole",
    "cosmology",
    "galaxy",
)
_OVEREXPOSED_OCEAN_TOPIC_KEYWORDS = (
    "심해",
    "해저",
    "해양",
    "바다를 지배",
    "고대 해양",
    "해양 생물",
    "열수구",
    "수중",
    "ocean",
    "deep sea",
    "underwater",
)
_VAGUE_LONGFORM_UNITS = (
    "아주 오래전",
    "엄청",
    "굉장히",
    "상당히",
    "수많은",
    "많이",
    "큰 규모",
    "거대한 규모",
    "오랜 시간",
    "오래전",
)

STORY_TITLE_TARGET_MIN = 22
STORY_TITLE_TARGET_MAX = 34
STORY_TITLE_MAX = 42


def story_topic_rejection_reason(data: dict) -> str | None:
    """Return the automatic-production rejection reason for weak Shorts topics."""
    if str(data.get("format") or "story") != "story":
        return None
    fields = [
        data.get("topic"),
        data.get("hook_angle"),
        data.get("target_keyword"),
        data.get("core_question"),
        data.get("selection_reason"),
    ]
    for fact in data.get("facts") or []:
        if isinstance(fact, dict):
            fields.extend([fact.get("claim"), fact.get("value")])
    haystack = " ".join(str(value or "") for value in fields).lower()
    if any(keyword.lower() in haystack for keyword in _ABSTRACT_SPACE_TOPIC_KEYWORDS):
        return "추상 우주·천문 소재는 자동 제작에서 제외됨"
    if any(keyword.lower() in haystack for keyword in _OVEREXPOSED_OCEAN_TOPIC_KEYWORDS):
        return "과다 노출 해양·심해 소재는 자동 제작에서 제외됨"
    return None


def is_rejected_story_topic(data: dict) -> bool:
    """자동 제작에서 조회수 저하가 반복된 소재를 차단한다."""
    return story_topic_rejection_reason(data) is not None


class RankItem(BaseModel):
    rank: int = Field(ge=1, le=10)
    name: str
    fact: str
    source: str
    source_url: str = ""   # 출처 URL (내용 검증은 미뤄도 저장은 저렴)
    visual_keyword: str = ""

    @field_validator("name", "fact", "source")
    @classmethod
    def _not_placeholder(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or v in _PLACEHOLDERS:
            raise ValueError("빈 값 또는 자리표시자 — 실제 내용 필수")
        return v


class TopicContract(BaseModel):
    """리서처 산출물 (topic.json) 계약."""
    topic: str = Field(min_length=5)
    ranking_size: int = Field(ge=3, le=10)
    hook_angle: str = ""
    target_keyword: str = ""
    items: list[RankItem]
    evidence: list = []
    verification_note: str = ""
    # 검증 방식: grounded_search | verified_cache | model_memory
    verification_method: str = "model_memory"
    verified_at: str = ""

    @model_validator(mode="after")
    def _ranks_complete(self):
        ranks = sorted(i.rank for i in self.items)
        expected = list(range(1, self.ranking_size + 1))
        if ranks != expected:
            raise ValueError(
                f"순위 불완전: {ranks} — 1~{self.ranking_size}가 중복·누락 없이 있어야 함"
            )
        return self

    def is_uploadable(self) -> bool:
        """규칙상 업로드 허용 여부. model_memory는 불변 기록·수치 소재에만 사용한다."""
        return self.verification_method in UPLOADABLE_VERIFICATION


class Scene(BaseModel):
    n: int
    rank: int | None = None
    narration: str = Field(min_length=2)
    visual: str = ""
    duration_sec: float = Field(gt=0, le=15)

    @field_validator("rank", mode="before")
    @classmethod
    def _zero_is_none(cls, v):
        # 모델이 비순위 씬(훅/긴장/CTA)에 0을 넣는 경우가 흔함 — 0은 "순위 없음"으로 정규화
        if v in (0, "0", "", None):
            return None
        return v


class ScriptContract(BaseModel):
    """작가 산출물 (script.json) 계약."""
    title: str = Field(min_length=5, max_length=100)
    description: str = ""
    tags: list[str] = []
    hook: str = ""
    scenes: list[Scene] = Field(min_length=3)
    cta: str = ""
    # 숏츠 상한은 180초 — 목표는 40~60초지만 초과해도 오류로 막지 않고 통과시킴
    total_duration_sec: float = Field(gt=0, le=180)

    @field_validator("title", mode="before")
    @classmethod
    def _public_title_only(cls, value: object) -> str:
        return validate_public_title(value)

    @model_validator(mode="after")
    def _structure_rules(self):
        # 순위 씬은 반드시 역순(N → 1) — 랭킹 포맷의 핵심 계약
        ranked = [s.rank for s in self.scenes if s.rank is not None]
        if ranked and ranked != sorted(ranked, reverse=True):
            raise ValueError(f"순위 씬이 역순(N→1)이 아님: {ranked}")

        # 실제 영상 길이는 씬 duration 합계로 결정됨. total_duration_sec는 표시용이라
        # 불일치해도 실패시키지 않고 합계로 자동 보정 (모델의 사소한 계산 오차로 회차를 날리지 않음).
        total = sum(s.duration_sec for s in self.scenes)
        if not 5 <= total <= 180:
            raise ValueError(f"씬 duration 합계 {total:.0f}초 — 숏츠 범위(5~180초) 벗어남")
        self.total_duration_sec = round(total, 1)
        return self


class StoryFact(BaseModel):
    claim: str
    value: str
    source: str
    source_url: str

    @field_validator("claim", "value", "source")
    @classmethod
    def _not_placeholder(cls, v: str) -> str:
        value = (v or "").strip()
        if not value or value in _PLACEHOLDERS:
            raise ValueError("빈 값 또는 자리표시자 — 실제 내용 필수")
        return value

    @field_validator("source_url")
    @classmethod
    def _source_url_required(cls, v: str) -> str:
        value = (v or "").strip()
        if not value.startswith(("https://", "http://")):
            raise ValueError("사실마다 HTTP(S) 출처 URL이 필요함")
        return value


class StoryVisualPlan(BaseModel):
    beat: str = Field(min_length=2)
    keywords: list[str] = Field(min_length=2, max_length=5)

    @field_validator("keywords")
    @classmethod
    def _keywords_are_concrete(cls, values: list[str]) -> list[str]:
        cleaned = [(value or "").strip() for value in values]
        if any(not value or value in _PLACEHOLDERS for value in cleaned):
            raise ValueError("visual keyword는 실제 검색어여야 함")
        return cleaned


class VisualIdentity(BaseModel):
    exact_queries: list[str] = Field(min_length=1, max_length=3)
    safe_fallbacks: list[str] = Field(min_length=1, max_length=5)
    required_exact: bool = True

    @field_validator("exact_queries", "safe_fallbacks")
    @classmethod
    def _queries_are_nonblank(cls, values: list[str]) -> list[str]:
        cleaned = [(value or "").strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("visual identity query must not be blank")
        return cleaned


class StoryTopicContract(BaseModel):
    """단일 소재 스토리 리서처 산출물 계약."""
    format: Literal["story"] = "story"
    topic: str = Field(min_length=5)
    category: Literal[
        "place_nature",
        "science_mystery",
        "hidden_world",
        "history_mystery",
    ]
    hook_angle: str = Field(min_length=5)
    target_keyword: str = Field(min_length=2)
    core_question: str = Field(min_length=5)
    interest_score: int = Field(default=0, ge=0, le=30)
    selection_reason: str = ""
    facts: list[StoryFact] = Field(min_length=1)
    visual_plan: list[StoryVisualPlan] = Field(min_length=1)
    visual_identity: VisualIdentity | None = None
    verification_method: str
    verified_at: str = Field(min_length=5)

    def is_uploadable(self) -> bool:
        return self.verification_method in UPLOADABLE_VERIFICATION

    @model_validator(mode="after")
    def _reject_abstract_space_topics(self):
        reason = story_topic_rejection_reason(self.model_dump())
        if reason:
            raise ValueError(reason)
        return self


class ManualStoryTopicContract(StoryTopicContract):
    """수동 요청용 스토리 계약 — 실제 채널 밖 분류명을 그대로 보존한다."""

    category: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9_]+$")


class StoryScene(BaseModel):
    n: int = Field(ge=1)
    role: Literal["hook", "context", "problem", "mechanism", "payoff", "close"]
    narration: str = Field(min_length=2)
    visuals: list[str] = Field(min_length=2, max_length=3)
    duration_sec: float = Field(ge=2, le=15)
    emphasis: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("narration")
    @classmethod
    def _narration_within_spoken_budget(cls, value: str) -> str:
        normalized = " ".join((value or "").split())
        if len(normalized) > 80:
            raise ValueError("씬 narration은 공백 포함 80자 이하여야 함")
        if not re.search(r"[.!?]$", normalized):
            raise ValueError("씬 narration은 종결 문장부호로 끝나야 함")
        return normalized

    @field_validator("visuals")
    @classmethod
    def _visuals_are_searchable(cls, values: list[str]) -> list[str]:
        cleaned = [(value or "").strip() for value in values]
        if any(not value or value in _PLACEHOLDERS for value in cleaned):
            raise ValueError("씬마다 실제 visual 검색어가 필요함")
        return cleaned


class StoryScriptContract(BaseModel):
    """기존 산출물 호환 범위 안에서 최대 75초인 스토리 본문 계약."""
    format: Literal["story"] = "story"
    title: str = Field(min_length=5, max_length=100)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    hook: str = Field(min_length=5)
    scenes: list[StoryScene] = Field(min_length=7, max_length=10)
    cta: str = ""
    total_duration_sec: float = Field(ge=53, le=75)

    @field_validator("title", mode="before")
    @classmethod
    def _public_title_only(cls, value: object) -> str:
        title = validate_public_title(value)
        if len(title) > STORY_TITLE_MAX:
            raise ValueError(
                f"제목 길이 {len(title)}자 — Shorts 제목은 {STORY_TITLE_MAX}자 이하여야 함"
            )
        return title

    @model_validator(mode="after")
    def _story_structure(self):
        numbers = [scene.n for scene in self.scenes]
        expected = list(range(1, len(self.scenes) + 1))
        if numbers != expected:
            raise ValueError(f"씬 번호가 연속적이지 않음: {numbers}")
        if self.scenes[0].role != "hook":
            raise ValueError("첫 씬 role은 hook이어야 함")
        if self.scenes[-1].role != "close":
            raise ValueError("마지막 씬 role은 close여야 함")
        narration_chars = sum(len(scene.narration) for scene in self.scenes)
        if narration_chars > 440:
            raise ValueError(
                f"본문 narration 합계 {narration_chars}자 — 440자 상한 초과"
            )
        total = round(sum(scene.duration_sec for scene in self.scenes), 1)
        if not 53 <= total <= 75:
            raise ValueError(f"씬 duration 합계 {total:.1f}초 — story 본문 목표(53~75초) 벗어남")
        self.total_duration_sec = total
        return self


class LongformScene(BaseModel):
    n: int = Field(ge=1)
    role: Literal[
        "hook",
        "context",
        "evidence",
        "mechanism",
        "counterpoint",
        "payoff",
        "close",
    ]
    chapter_title: str = Field(min_length=2, max_length=60)
    narration: str = Field(min_length=10)
    visuals: list[str] = Field(min_length=1, max_length=4)
    duration_sec: float = Field(ge=15, le=90)

    @field_validator("narration")
    @classmethod
    def _narration_is_spoken_text(cls, value: str) -> str:
        normalized = " ".join((value or "").split())
        if any(term in normalized for term in _VAGUE_LONGFORM_UNITS):
            raise ValueError("롱폼 narration에 애매한 표현이 포함됨")
        if not re.search(r"[.!?]$", normalized):
            raise ValueError("롱폼 narration은 종결 문장부호로 끝나야 함")
        return normalized

    @field_validator("visuals")
    @classmethod
    def _visuals_are_searchable(cls, values: list[str]) -> list[str]:
        cleaned = [(value or "").strip() for value in values]
        if any(not value or value in _PLACEHOLDERS for value in cleaned):
            raise ValueError("롱폼 씬마다 실제 visual 검색어가 필요함")
        return cleaned


class LongformScriptContract(BaseModel):
    """수동 검토용 6~10분 롱폼 대본 계약."""

    format: Literal["longform"] = "longform"
    title: str = Field(min_length=5, max_length=100)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    hook: str = Field(min_length=5)
    style_id: Literal["documentary", "cinematic", "clean_news"] = "clean_news"
    scenes: list[LongformScene] = Field(min_length=6, max_length=16)
    visual_identity: VisualIdentity | None = None
    cta: str = ""
    total_duration_sec: float = Field(default=0, ge=360, le=600)

    @field_validator("title", mode="before")
    @classmethod
    def _public_title_only(cls, value: object) -> str:
        return validate_public_title(value)

    @model_validator(mode="after")
    def _longform_structure(self):
        numbers = [scene.n for scene in self.scenes]
        expected = list(range(1, len(self.scenes) + 1))
        if numbers != expected:
            raise ValueError(f"롱폼 씬 번호가 연속적이지 않음: {numbers}")
        if self.scenes[0].role != "hook":
            raise ValueError("롱폼 첫 씬 role은 hook이어야 함")
        if self.scenes[-1].role != "close":
            raise ValueError("롱폼 마지막 씬 role은 close여야 함")
        roles = {scene.role for scene in self.scenes}
        required = {"hook", "context", "evidence", "mechanism", "payoff", "close"}
        if not required <= roles:
            missing = ", ".join(sorted(required - roles))
            raise ValueError(f"롱폼 필수 챕터 누락: {missing}")
        total = round(sum(scene.duration_sec for scene in self.scenes), 1)
        if not 360 <= total <= 600:
            raise ValueError(f"롱폼 duration 합계 {total:.1f}초 — 6~10분 범위 벗어남")
        self.total_duration_sec = total
        return self


def validate_topic(data: dict, content_format: str | None = None) -> dict:
    """topic.json 검증 — 실패 시 ValueError."""
    selected = content_format or data.get("format") or "ranking"
    model = StoryTopicContract if selected == "story" else TopicContract
    result = model.model_validate(data).model_dump()
    if selected == "story":
        from app.services.visual_relevance import ensure_visual_identity
        return ensure_visual_identity(result)
    return result


def validate_manual_story_topic(data: dict) -> dict:
    """Validate a manual story without weakening automatic category constraints."""
    result = ManualStoryTopicContract.model_validate(data).model_dump()
    from app.services.visual_relevance import ensure_visual_identity
    return ensure_visual_identity(result)


def validate_script(data: dict, content_format: str | None = None) -> dict:
    """script.json 검증 — 실패 시 ValueError."""
    selected = content_format or data.get("format") or "ranking"
    model = StoryScriptContract if selected == "story" else ScriptContract
    return model.model_validate(data).model_dump()


def validate_longform_script(data: dict) -> dict:
    """longform/script.json 검증 — 실패 시 ValueError."""
    return LongformScriptContract.model_validate(data).model_dump()
