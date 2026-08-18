# 분석가 (Analyst)

## 역할

업로드 이력과 공개 조회수를 모아 회차·카테고리별 결과를 요약한다. 현재 분석 결과는 운영자 참고용이며 다음 리서처·작가 프롬프트에 자동 주입되지 않는다.

## 입출력 계약

- 입력: `data/videos.sqlite`, YouTube 공개 통계
- 출력: `data/reports/latest.json`
- 실제 구현: `app/agents/analyst.py`

## 현재 수집 범위

- 조회수
- 회차와 카테고리별 평균 조회수
- 상위 카테고리와 상대 배수

기존 파이프라인 분석가에는 평균 시청 지속률, Engaged views, 구독 전환이 포함되지 않는다. 해당 지표는 아래 독립 성과 수집기에서만 읽기 전용 OAuth로 수집하며, API가 제공하지 않는 지표는 추정하지 않는다.

## 주의사항

- 카테고리는 업로드 당시 `topic.json`에서 DB에 저장된 값을 사용한다.
- 카테고리가 저장되지 않은 과거 영상은 현재 슬롯 설정으로 소급 분류하지 않고 `과거 미분류`로 분리한다.
- 표본이 적을 때 결과를 장기 성과처럼 단정하지 않는다.
- 자동 학습 루프는 구현되어 있지 않으므로 보고서 생성만으로 다음 소재가 바뀌지 않는다.
# 독립 성과 수집기

기존 `app/agents/analyst.py`는 업로드 파이프라인 안에서 공개 조회수·좋아요·댓글과 카테고리 요약을 갱신하는 참고 분석가로 유지한다. 실패해도 업로드 성공 여부에는 영향을 주지 않는다.

장기 학습 데이터는 `scripts/collect_performance.py`가 별도로 수집한다. 이 명령은 업로드 오케스트레이터에서 호출하지 않으며 다음 산출물만 만든다.

- `videos.sqlite`의 `video_features`
- `videos.sqlite`의 `video_performance_snapshots`
- `videos.sqlite`의 `video_retention_points`
- `data/reports/performance_latest.json`

분석 결과는 현재 관찰용이다. 다음 소재를 자동으로 결정하거나 researcher·writer 프롬프트를 변경하지 않는다. 표본이 8개 미만인 그룹은 소재 성과 차이로 확정하지 않는다.
