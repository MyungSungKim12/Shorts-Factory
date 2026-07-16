# Shorts Factory 백엔드 — Claude 작업 지침

## 프로젝트 개요
유튜브 숏츠 자동 생성·업로드·분석 파이프라인. 전체 설명은 README.md 참고.

## 에이전트 운영 규칙
- 각 에이전트의 역할·프롬프트는 `agents/*.md`에 정의되어 있다. 작업 전 해당 md를 반드시 읽고 그 프롬프트 템플릿을 따를 것.
- 에이전트 간 데이터는 `data/work/{date}/` 아래 JSON 파일로만 주고받는다 (topic.json → script.json → output.mp4).
- 산출물 스키마는 각 md의 "입출력 계약" 섹션이 유일한 기준이다.

## 절대 규칙
- `.env`, `credentials/` 커밋 금지
- 업로드는 일 6건 한도 (YouTube API 쿼터)
- 사실 검증: 검색 그라운딩(grounded_search) 또는 검증 캐시(verified_cache)를 최우선으로 한다.
  단, 소재가 **시간이 지나도 변하지 않는 기록·수치**(산 높이·바다 깊이·스코빌 지수 등)인 경우에 한해
  강한 모델의 지식(model_memory)으로 생성·업로드할 수 있다. (그라운딩 무료 할당량 소진 시 채널 유지 목적)
  최신 순위 변동이 있는 소재(구독자·매출 순위 등)는 model_memory로 업로드 금지.
- verification_method는 항상 topic.json·로그에 기록한다.
