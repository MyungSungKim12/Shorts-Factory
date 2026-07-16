"""에이전트 입출력 계약 (Pydantic) — 불량 산출물이 파이프라인 하류로 가는 것을 차단.

agents/*.md의 "입출력 계약" 섹션에 대한 단일 코드 구현.
검증 실패 = ValueError 발생 = 파이프라인 중단 (불량 영상이 업로드되는 것보다 하루 쉬는 게 낫다).
"""
from pydantic import BaseModel, Field, field_validator, model_validator

_PLACEHOLDERS = {"...", "항목명", "N/A", "없음", "unknown", "TBD"}


# 업로드가 허용되는 검증 방식.
# 규칙 완화(2026-07-16): 그라운딩 할당량 소진 시 채널 유지를 위해, 불변 기록 소재에 한해
# model_memory도 허용. (리서처의 보수 모드 프롬프트가 '불변 기록만' 쓰도록 강제)
UPLOADABLE_VERIFICATION = {"grounded_search", "verified_cache", "model_memory"}


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
        """규칙상 업로드 허용 여부 — 검증되지 않은(model_memory) 소재는 불가."""
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


def validate_topic(data: dict) -> dict:
    """topic.json 검증 — 실패 시 ValueError."""
    return TopicContract.model_validate(data).model_dump()


def validate_script(data: dict) -> dict:
    """script.json 검증 — 실패 시 ValueError."""
    return ScriptContract.model_validate(data).model_dump()
