# Natural Free Shorts TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 무료 Supertonic 쇼츠 음성의 기계적인 억양과 장면 경계 음량·속도 변화를 줄인다.

**Architecture:** Supertonic이 목표 속도로 직접 생성하고 TTSResult의 실제 속도를 기준으로 필요한 파일만 후처리한다. 개별 음성 정규화는 제거하고 최종 합성 입력의 내레이션 전체를 한 번 정규화한다.

**Tech Stack:** Python, pytest, Supertonic 3, FFmpeg

**Spec:** `docs/superpowers/specs/2026-11-12-natural-free-shorts-tts-design.md`

## Global Constraints

- 무료·로컬 Supertonic 우선 운영을 유지한다.
- 소재·영상·자막·업로드 로직을 변경하지 않는다.
- 최종 90초 상한과 실측 오디오 싱크를 유지한다.
- `.env`와 `credentials/`는 커밋하지 않는다.

## Review Focus

- Supertonic 기본값이 환경변수 없이도 F4·1.2로 설정되는지 검증한다.
- 이미 1.2로 합성된 음성이 다시 1.2배 처리되지 않는지 검증한다.
- Google처럼 1.0 속도로 생성된 음성은 목표 1.2배 처리가 유지되는지 검증한다.
- BGM·전환음 유무와 관계없이 전체 내레이션 정규화 필터가 한 번 적용되는지 검증한다.
- 장면 길이 계산이 짧은 여유를 유지하며 음성을 자르지 않는지 검증한다.

---

### Task 1: Supertonic 기본 음색과 원본 합성 속도

**Files:**
- Modify: `app/services/tts.py`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_tts.py`

**Interfaces:**
- Produces: `TTSResult.speaking_rate`에 실제 모델 합성 속도를 기록한 Supertonic 결과

- [ ] F4·1.2·0.08 기본값을 요구하는 실패 테스트를 작성하고 실행한다.
- [ ] Supertonic 합성 기본값과 무음 설정을 구현한다.
- [ ] Supertonic·gTTS가 충돌하지 않는 설치 의존성을 고정한다.
- [ ] 대상 테스트를 통과시킨다.

### Task 2: 공급자별 중복 배속 방지와 장면 여유 축소

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Consumes: `TTSResult.speaking_rate`
- Produces: `_post_tempo(target_rate, result) -> float`, 공급자별 선택적 후처리

- [ ] 이미 목표 속도로 합성된 음성은 재처리하지 않고 느린 결과만 보정하는 실패 테스트를 작성한다.
- [ ] 공급자별 후처리 속도 계산과 선택 적용을 구현한다.
- [ ] 장면 끝 여유 0.06초 테스트와 구현을 추가한다.
- [ ] 대상 테스트를 통과시킨다.

### Task 3: 전체 내레이션 단일 음량 정규화

**Files:**
- Modify: `app/agents/story_producer.py`
- Test: `tests/test_story_producer.py`

**Interfaces:**
- Produces: `_finish_video()`의 전체 내레이션 `loudnorm` 처리

- [ ] 개별 준비 단계에서 정규화하지 않고 최종 필터에 한 번 포함되는 실패 테스트를 작성한다.
- [ ] 최종 내레이션 필터를 구현하고 BGM·전환음 경로를 유지한다.
- [ ] 대상 및 전체 테스트를 실행한다.
