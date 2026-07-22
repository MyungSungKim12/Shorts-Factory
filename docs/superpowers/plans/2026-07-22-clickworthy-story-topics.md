# 클릭을 부르는 스토리 소재 선정 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추가 API 호출 없이 재미 점수가 높은 스토리 소재만 다음 회차부터 선택한다.

**Architecture:** 기존 `_story_researcher_prompt()` 안에서 후보 생성과 점수화를 함께 수행하고 최종 JSON 하나만 반환한다. 회차 카테고리를 실제 프롬프트에 주입하며, 호출 횟수와 운영 cron은 그대로 유지한다.

**Tech Stack:** Python 3.12, Gemini/Groq 단일 호출 폴백, Pydantic, Linux cron

## Global Constraints

- 추가 AI 호출을 만들지 않는다.
- 소재 점수는 24/30 이상을 목표로 하며 미달이어도 최고점 후보로 회차를 진행한다.
- 영상 합성·TTS·업로드·품질 게이트는 변경하지 않는다.
- `.env`, `credentials/`, 운영 데이터는 배포와 커밋에서 제외한다.
- 사용자 요청에 따라 새 테스트와 샘플 영상, 전체 회귀 테스트는 만들지 않는다.

---

### Task 1: 재미 중심 소재 프롬프트

**Files:**
- Modify: `app/agents/researcher.py`
- Modify: `app/models.py`

**Interfaces:**
- Consumes: `context["category"]`, 최근 소재 목록, 기존 한 번의 `call_agent()` 호출
- Produces: `StoryTopicContract`의 `interest_score: int`, `selection_reason: str`

- [x] `SLOT_CATEGORIES`를 스토리형 네 카테고리로 바꾼다.
- [x] `_story_researcher_prompt()`에 후보 8개와 여섯 점수 항목을 추가한다.
- [x] 총점 24점 이상을 우선하되 재호출·회차 중단 없이 최고점 후보를 선택한다.
- [x] 관련 기존 테스트만 실행한다.

### Task 2: 역할 문서 레거시 제거

**Files:**
- Modify: `agents/01_trend-researcher.md`
- Modify: `agents/02_script-writer.md`
- Modify: `agents/05_analyst.md`
- Modify: `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: 현재 9개 cron과 실제 Python 프롬프트 구조
- Produces: 현재 역할과 일치하는 문서

- [x] 랭킹 전용 역할 문구를 스토리 역할로 교체한다.
- [x] 분석 결과가 자동 프롬프트 입력이라는 잘못된 설명을 제거한다.
- [x] 기존 호출 횟수와 운영 cron은 변경하지 않는다.
- [x] Python 컴파일과 diff 검사를 실행한다.

### Task 3: 서버 반영

**Files:**
- Server: `/home/ubuntu/shorts-factory-be`

**Interfaces:**
- Consumes: 변경된 소스·문서와 기존 4회 업로드 cron
- Produces: 다음 회차부터 새 소재 기준을 쓰는 운영 서버

- [x] 서버 소스·설정·데이터·cron을 백업한다.
- [x] 변경된 추적 파일만 배포한다.
- [x] 관련 테스트·컴파일·헬스체크를 최소 실행한다.
- [x] 기존 06:30 워머와 11·14·17·21시 업로드를 모두 유지한다.
- [x] 변경분을 한글 커밋으로 만들고 `main`에 푸시한다.
