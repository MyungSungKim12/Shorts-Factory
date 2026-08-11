# Keyword Candidates and Script Density Implementation Plan

> **For agentic workers:** Execute inline. The user prohibited repeated stage-by-stage test runs; add focused contract tests and run verification once before deployment.

**Goal:** 단어 입력을 제작 가능한 후보로 확장하고 모든 스토리 영상의 정보 전달량을 안정화한다.

**Architecture:** 후보 생성·검증은 `manual_topic` 서비스에 두고, 선택 상태 전환은 SQLite 트랜잭션으로 처리한다. 프론트는 저장된 후보 요약만 표시한다. 대본 품질은 프롬프트와 Pydantic 계약을 함께 강화한다.

**Tech Stack:** FastAPI, SQLite, Pydantic, React, pytest, Node test, Vite

## 구현 작업

- [ ] 단어 입력 감지, 후보 생성·시각 자료 사전검사, 최대 1회 보충 생성
- [ ] 후보 선택 API와 원자적 상태 전환
- [ ] 후보 카드 UI와 선택 동작
- [ ] 스토리 프롬프트·계약·검증 템플릿 정보량 강화
- [ ] 핵심 계약 테스트 작성
- [ ] 배포 직전 전체 백엔드·프론트 테스트와 빌드
- [ ] 백엔드·프론트 커밋·푸시·배포 및 운영 API 확인
