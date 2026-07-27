# Strange Earth Mystery Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 이상한 지구기록 브랜딩과 네 가지 검증형 미스터리 회차를 운영에 반영한다.

**Architecture:** `SLOT_CATEGORIES`가 회차별 카테고리와 프롬프트를 결정하고, 캐시 선택 시 같은 카테고리만 허용한다. 최종 영상 길이는 공통 `shorts_max_duration()`으로 제작 타이밍과 품질 게이트가 동일하게 판정한다.

**Tech Stack:** Python 3.12, Pydantic, SQLite, pytest, Linux cron

## Tasks

- [x] 77초 허용·180초 초과 차단 테스트와 구현
- [x] 네 가지 카테고리·동물 카테고리 거부 테스트
- [x] 동물 캐시 제외 필터
- [x] 채널 설정·리서처 프롬프트·문서 갱신
- [x] 서버 백업 및 배포
- [x] 서버 전체 테스트
- [x] 오늘 slot 4 사전 제작 즉시 재실행
