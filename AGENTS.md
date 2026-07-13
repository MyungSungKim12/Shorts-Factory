# Shorts Factory 백엔드 — Codex 작업 지침

## 프로젝트 개요
유튜브 숏츠 자동 생성·업로드·분석 파이프라인. 전체 설명은 README.md 참고.

## 에이전트 운영 규칙
- 각 에이전트의 역할·프롬프트는 `agents/*.md`에 정의되어 있다. 작업 전 해당 md를 반드시 읽고 그 프롬프트 템플릿을 따를 것.
- 에이전트 간 데이터는 `data/work/{date}/` 아래 JSON 파일로만 주고받는다 (topic.json → script.json → output.mp4).
- 산출물 스키마는 각 md의 "입출력 계약" 섹션이 유일한 기준이다.

## 절대 규칙
- `.env`, `credentials/` 커밋 금지
- 업로드는 일 6건 한도 (YouTube API 쿼터)
- 대본·제목의 사실 검증 없이 업로드 금지
