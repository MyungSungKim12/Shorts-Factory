# Story Speed and Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chirp 3 HD 음성을 1.2배로 재생하면서 검증된 정보가 더 많은 65~80초 스토리와 자연스러운 문장 호흡을 만든다.

**Architecture:** 작가 계약이 9~10개 씬과 560~680자 본문, 문장부호 호흡을 보장한다. 프로듀서는 모든 TTS 조각을 `TTS_SPEED` 값으로 리타이밍한 뒤 실측 길이로 영상과 자막을 구성하며 기존 자동 감속은 사용하지 않는다.

**Tech Stack:** Python 3.12, Pydantic, Google Cloud TTS, FFmpeg, pytest

## Global Constraints

- Chirp 3 HD Kore 여성 음색을 유지한다.
- 제목·본문·CTA에 피치 보존 1.2배속을 동일 적용한다.
- 본문은 검증된 `topic.json` 사실만 사용한다.
- 최종 영상은 보통 65~80초를 목표로 하고 90초를 넘기지 않는다.
- 별도 샘플 영상을 생성하지 않는다.

---

### Task 1: 대본 분량과 문장 호흡 계약

**Files:**
- Modify: `app/models.py`
- Modify: `tests/test_story_contracts.py`

**Interfaces:**
- Consumes: `StoryScene.narration`, `StoryScriptContract.scenes`
- Produces: 종결 문장부호, 무구두점 구간, 씬 수 및 본문 글자 수 검증

- [x] 종결 문장부호가 없거나 무구두점 구간이 긴 narration, 560자 미만·680자 초과 본문이 실패하는 테스트를 작성한다.
- [x] 대상 테스트가 현재 계약에서 예상한 이유로 실패하는지 확인한다.
- [x] 씬당 최대 80자, 9~10개 씬, 본문 560~680자, 계획 본문 72~84초 계약을 구현한다.
- [x] 대상 테스트를 통과시킨다.

### Task 2: 작가 프롬프트와 검증 사실 템플릿

**Files:**
- Modify: `app/agents/writer.py`
- Modify: `agents/02_script-writer.md`
- Modify: `tests/test_story_prompts.py`

**Interfaces:**
- Consumes: 검증된 `StoryTopicContract`
- Produces: 새 계약을 만족하는 LLM 프롬프트와 `build_verified_story_script(topic)`

- [x] 프롬프트와 폴백 결과의 씬 수·글자 수·구두점 행동 테스트를 작성한다.
- [x] 대상 테스트 실패를 확인한다.
- [x] 정보 확장 규칙과 낭독 구두점 규칙을 프롬프트에 추가한다.
- [x] 검증된 claim/value만 조합하는 9개 씬 폴백을 새 계약에 맞춘다.
- [x] 대상 테스트를 통과시킨다.

### Task 3: 전체 음성 1.2배속 처리

**Files:**
- Modify: `app/agents/story_producer.py`
- Modify: `agents/03_video-producer.md`
- Modify: `.env.example`
- Modify: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `story_playback_tempo() -> float`
- Consumes: `TTS_SPEED`, 제목·본문·CTA WAV

- [x] 기본값·허용값·잘못된 값의 속도 결정 및 전체 음성 리타이밍 테스트를 작성한다.
- [x] 기존 자동 감속 동작 때문에 테스트가 실패하는지 확인한다.
- [x] `TTS_SPEED`를 0.8~1.5 범위로 읽고 기본 1.2를 반환하는 함수를 구현한다.
- [x] 제목·각 씬·CTA를 항상 결정된 속도로 리타이밍하고 이후 길이를 재측정한다.
- [x] 대상 테스트를 통과시킨다.

### Task 4: 전체 검증·커밋·서버 배포

**Files:**
- Modify: 위 파일 및 본 계획 문서

- [x] `python -m pytest -q`와 `git diff --check`를 통과시킨다.
- [ ] 변경 파일만 한국어 메시지로 커밋하고 `main`에 푸시한다.
- [ ] 서버 소스를 백업하고 동일 커밋 아카이브를 배포한다.
- [ ] 서버 `.env`의 `TTS_SPEED=1.2`를 설정하되 다른 값을 변경하지 않는다.
- [ ] 서버 전체 테스트, 대시보드, API 상태와 다음 회차 미제작 또는 새 로직 적용 상태를 확인한다.
