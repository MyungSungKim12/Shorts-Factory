# 독립 YouTube Analytics 수집기 설계

## 목표

기존 영상 제작·합성·업로드·복구 파이프라인을 변경하지 않고, 업로드된 영상의 실제 시청 성과와 제작 특징을 장기간 축적한다. 축적 데이터는 이후 소재 선정 기준을 실제 성과에 맞춰 보정하는 근거로 사용하지만, 이번 작업에서는 소재 선정 로직에 자동 반영하지 않는다.

## 범위

### 포함

- YouTube Analytics 읽기 전용 OAuth 인증
- 기존 업로드 인증과 분석 인증의 물리적 분리
- 공개 통계와 채널 소유자 Analytics 지표 수집
- 영상별 제작 특징 영구 보존
- 영상별·수집 시점별 성과 스냅샷 영구 보존
- 영상 구간별 시청 유지율 저장
- 기존 업로드 영상 역수집
- 최신 분석 JSON 리포트 생성
- 서버 독립 cron 등록과 운영 문서

### 제외

- 프론트엔드 변경
- 자동 제작·합성·업로드·복구 코드 변경
- 분석 결과를 소재 선정 점수에 자동 반영
- BigQuery 등 외부 분석 저장소
- 수익·광고 지표 수집
- YouTube Studio에만 있고 Analytics API로 제공되지 않는 Shorts 피드 노출 수 직접 수집

## 핵심 원칙

1. 분석 장애는 영상 제작과 업로드를 막지 않는다.
2. 업로드용 `credentials/token.json`은 읽거나 덮어쓰지 않는다.
3. 분석용 토큰은 `credentials/analytics_token.json`에 별도로 저장하고 커밋하지 않는다.
4. 분석 수집기는 오케스트레이터에서 호출하지 않고 독립 명령과 cron으로만 실행한다.
5. 원본 작업 영상은 기존 7일 정리 정책을 유지하되, 작은 JSON 특징과 통계 행은 삭제하지 않는다.
6. 같은 수집 구간을 다시 실행해도 중복 행이 생기지 않아야 한다.

## 인증

분석 인증에는 다음 두 읽기 전용 범위를 사용한다.

- `https://www.googleapis.com/auth/youtube.readonly`
- `https://www.googleapis.com/auth/yt-analytics.readonly`

로컬에서 `credentials/client_secret.json`으로 한 번 인증하고 생성된 `analytics_token.json`을 서버의 동일 경로에 전송한다. 인증 스크립트는 분석 API에 `channel==MINE` 시험 요청을 보내 토큰과 채널 소유권을 확인한다. 업로드 토큰의 범위는 변경하지 않는다.

## 구성 요소

### `app/services/performance_store.py`

SQLite 테이블 생성, 특징 스냅샷 저장, 성과 스냅샷 저장, 유지율 저장과 리포트 조회를 담당한다. 외부 API를 알지 못하며 전달받은 정규화 데이터만 저장한다.

### `app/services/youtube_performance.py`

YouTube Data API와 YouTube Analytics API 응답을 정규화한다. 공개 통계 수집과 소유자 지표 수집을 분리하며, 일부 지표가 제공되지 않아도 가능한 지표를 저장한다.

### `scripts/auth_youtube_analytics.py`

분석 전용 OAuth 토큰을 발급하고 시험 조회한다. 대화형 로컬 실행만 지원하며 서버 cron에서는 실행하지 않는다.

### `scripts/collect_performance.py`

독립 수집 진입점이다. DB에 기록된 업로드 영상을 기준으로 특징을 먼저 영구 저장하고, 공개 통계와 Analytics 통계를 수집한 뒤 리포트를 갱신한다. 실패 시 비정상 종료 코드와 안전한 로그를 남기되 다른 프로세스의 상태를 변경하지 않는다.

## 데이터 모델

### `video_features`

영상당 한 행을 유지한다.

- `video_id` 기본키
- `run_id`
- `uploaded_at`
- `title`
- `topic`
- `category`
- `hook_text`
- `script_chars`
- `scene_count`
- `planned_duration_sec`
- `actual_duration_sec`
- `writer_mode`
- `verification_method`
- `ai_opening_used`
- `feature_source`
- `captured_at`

최근 작업 폴더가 존재하면 `topic.json`, `script.json`, `produce_log.json`에서 채운다. 이미 정리된 과거 영상은 `videos` 테이블과 공개 영상 메타데이터로 채우며 알 수 없는 값은 `NULL`로 둔다. 이후 더 풍부한 값이 발견되면 `NULL`만 보강하고 기존 확정값은 임의로 덮어쓰지 않는다.

### `video_performance_snapshots`

영상과 수집 시점별 누적 성과를 저장한다.

- `video_id`
- `snapshot_at`
- `age_hours`
- `views`
- `engaged_views`
- `engaged_view_rate`
- `estimated_minutes_watched`
- `average_view_duration_sec`
- `average_view_percentage`
- `likes`
- `comments`
- `shares`
- `subscribers_gained`
- `subscribers_lost`
- `analytics_end_date`
- `source_status`

`video_id`와 UTC 기준 수집 날짜·시간 버킷을 고유키로 사용한다. 공개 통계만 성공하거나 Analytics 데이터가 아직 확정되지 않은 경우에도 행을 저장하고 `source_status`에 부분 성공을 기록한다.

### `video_retention_points`

- `video_id`
- `snapshot_date`
- `elapsed_video_time_ratio`
- `audience_watch_ratio`
- `relative_retention_performance`

유지율은 호출량을 줄이기 위해 업로드 후 48시간 이상 지난 영상만 수집하고, 동일 영상은 하루 한 번만 갱신한다.

## 수집 방식

1. `videos.sqlite`의 `status='uploaded'` 영상을 읽는다.
2. 새 영상 또는 특징이 불완전한 영상의 제작 특징을 보존한다.
3. 최대 50개씩 YouTube Data API 공개 통계를 조회한다.
4. 최대 500개 영상 필터를 사용해 YouTube Analytics 누적 지표를 조회한다.
5. Analytics API의 최신 날짜 지연을 허용하고 응답이 없는 영상은 오류로 간주하지 않는다.
6. 48시간 이상 지난 영상의 유지율을 제한적으로 갱신한다.
7. 연령이 다른 영상의 단순 누적 조회수 비교를 피하도록 24시간 이상 지난 영상 중심의 리포트를 만든다.

서버에서는 독립 cron으로 6시간마다 실행한다. 공개 통계는 실행마다 저장하며 Analytics 데이터는 API가 제공하는 최신 확정일까지 갱신한다. 수집 중복은 DB 고유키와 upsert로 제거한다.

## 리포트

`data/reports/performance_latest.json`에 다음을 기록한다.

- 수집 성공·부분 성공·실패 개수
- 영상별 최신 성과
- 카테고리·제목 구조·길이·제작 모드별 성과
- 표본 수와 중앙값
- 24시간 미만 영상 제외 여부
- 데이터가 충분하지 않을 때의 명시적 경고

초기 리포트는 관찰 결과만 제공한다. 특정 소재를 자동 추천하거나 researcher 프롬프트를 수정하지 않는다.

## 실패 처리

- 분석 토큰이 없으면 명확한 인증 안내와 함께 수집 명령만 실패한다.
- 공개 API 실패와 Analytics API 실패는 서로 독립적으로 처리한다.
- 특정 영상 응답이 없으면 나머지 영상을 계속 처리한다.
- SQLite 쓰기는 짧은 트랜잭션과 upsert를 사용한다.
- 오류 메시지에 OAuth 토큰, API 키, 인증 파일 내용은 포함하지 않는다.
- cron 실패는 기존 텔레그램 업로드 알림과 결합하지 않고 별도 로그에만 남긴다.

## 테스트

- 분석 토큰 경로와 OAuth 범위가 업로드 인증과 분리되는지 검증
- 신규 DB와 구버전 DB 모두에서 테이블 생성 검증
- 동일 스냅샷 재수집 시 중복 방지 검증
- 최근 작업 폴더 특징 저장과 과거 영상 부분 역수집 검증
- Analytics 응답 헤더 순서에 독립적인 정규화 검증
- Analytics 데이터 지연·빈 응답·부분 API 실패 검증
- 유지율 저장과 일일 중복 방지 검증
- 리포트가 표본 수·중앙값·경고를 정확히 생성하는지 검증
- 기존 오케스트레이터·업로더 테스트가 변경 없이 통과하는지 회귀 검증

## 배포

1. 로컬 자동 테스트를 통과시킨다.
2. 분석 전용 OAuth 인증을 수행한다.
3. 백엔드 소스를 서버에 배포한다.
4. 분석 토큰을 서버 `credentials/analytics_token.json`에 전송하고 권한을 제한한다.
5. 서버에서 최초 역수집을 한 번 실행한다.
6. 결과 DB와 `performance_latest.json`을 확인한다.
7. 6시간 간격 독립 cron을 등록한다.
8. 기존 제작·업로드 cron과 서비스가 그대로인지 확인한다.

## 성공 기준

- 기존 자동 영상이 변경 전과 동일한 예약으로 제작·업로드된다.
- 분석 인증 실패 상태에서도 제작·업로드가 정상 작동한다.
- 신규 영상의 특징이 작업 폴더 정리 후에도 DB에 남는다.
- 기존 영상과 신규 영상의 Analytics 성과가 장기 스냅샷으로 축적된다.
- 재실행으로 중복 데이터가 생기지 않는다.
- 프론트엔드 저장소에는 변경이 없다.
