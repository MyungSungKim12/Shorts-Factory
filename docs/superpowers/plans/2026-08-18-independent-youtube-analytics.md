# 독립 YouTube Analytics 수집기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자동 영상 제작·합성·업로드 파이프라인을 변경하지 않고 영상 특징, 공개 통계, YouTube Analytics 성과와 유지율을 장기간 저장하는 독립 백엔드 수집기를 배포한다.

**Architecture:** `performance_store`가 SQLite 저장과 리포트 생성을 전담하고, `youtube_performance`가 읽기 전용 OAuth와 Google API 응답 정규화를 담당한다. `collect_performance.py`는 두 서비스를 조합하는 독립 진입점이며 기존 오케스트레이터에서는 import하거나 호출하지 않는다.

**Tech Stack:** Python 3.12, SQLite, google-api-python-client, google-auth-oauthlib, pytest, Linux cron

**Spec:** `docs/superpowers/specs/2026-08-18-independent-youtube-analytics-design.md`

## Global Constraints

- `app/agents/orchestrator.py`, `app/agents/uploader.py`, 제작·복구 스크립트의 실행 흐름을 변경하지 않는다.
- 업로드 토큰 `credentials/token.json`을 읽거나 덮어쓰지 않는다.
- 분석 토큰은 `credentials/analytics_token.json`만 사용하며 `credentials/`는 Git에서 계속 제외한다.
- YouTube Analytics 권한은 `youtube.readonly`, `yt-analytics.readonly`만 사용한다.
- 프론트엔드 저장소를 변경하지 않는다.
- 분석 실패는 독립 명령의 종료 코드와 전용 로그에만 반영한다.
- 성과 데이터는 작업 폴더 7일 정리 대상에 포함하지 않는다.

---

## 파일 구조

- `app/services/performance_store.py`: 분석 테이블, 특징 추출·저장, 성과·유지율 upsert, 리포트 생성
- `app/services/youtube_performance.py`: 분석 전용 인증, Data API·Analytics API 호출, 헤더 기반 응답 정규화
- `scripts/auth_youtube_analytics.py`: 로컬 대화형 OAuth 발급과 `channel==MINE` 시험 조회
- `scripts/collect_performance.py`: 독립 수집 실행, 부분 실패 처리, JSON 결과 출력
- `tests/test_performance_store.py`: DB 마이그레이션, 특징, 중복, 리포트 검증
- `tests/test_youtube_performance.py`: 인증 경계와 API 응답 정규화 검증
- `tests/test_collect_performance.py`: 수집 오케스트레이션과 부분 실패 검증
- `docs/OPERATIONS.md`: 인증·수동 실행·cron·복구 절차

### Task 1: 영구 성과 저장소

**Files:**
- Create: `app/services/performance_store.py`
- Create: `tests/test_performance_store.py`

**Interfaces:**
- Consumes: `data_dir: pathlib.Path`, 기존 `videos` 테이블, 선택적 `data/work/<run_id>/*.json`
- Produces: `init_performance_schema(data_dir)`, `capture_video_features(data_dir)`, `save_performance_snapshots(data_dir, rows)`, `retention_due_video_ids(data_dir, now, limit)`, `save_retention_points(data_dir, video_id, snapshot_date, points)`, `build_performance_report(data_dir, generated_at)`

- [ ] **Step 1: DB 마이그레이션과 중복 방지 실패 테스트 작성**

```python
def test_schema_upserts_one_snapshot_per_video_and_six_hour_bucket(tmp_path):
    create_uploaded_video(tmp_path, video_id="v1", run_id="20260818-1")
    store.init_performance_schema(tmp_path)
    row = performance_row("v1", "2026-08-18T06:10:00+00:00", views=100)
    store.save_performance_snapshots(tmp_path, [row])
    store.save_performance_snapshots(
        tmp_path,
        [performance_row("v1", "2026-08-18T09:20:00+00:00", views=120)],
    )
    assert snapshot_rows(tmp_path) == [("v1", "2026-08-18T06:00:00+00:00", 120)]
```

- [ ] **Step 2: 실패 확인**

Run: `venv\Scripts\python.exe -m pytest tests/test_performance_store.py -q`

Expected: FAIL because `app.services.performance_store` does not exist.

- [ ] **Step 3: 스키마와 6시간 버킷 upsert 최소 구현**

```python
def snapshot_bucket(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    return utc.replace(hour=(utc.hour // 6) * 6, minute=0, second=0, microsecond=0).isoformat()

def save_performance_snapshots(data_dir: Path, rows: list[dict]) -> None:
    with _connect(data_dir) as db:
        init_performance_schema_on(db)
        db.executemany(
            """INSERT INTO video_performance_snapshots (
                 video_id, snapshot_bucket, snapshot_at, age_hours,
                 views, engaged_views, engaged_view_rate,
                 estimated_minutes_watched, average_view_duration_sec,
                 average_view_percentage, likes, comments, shares,
                 subscribers_gained, subscribers_lost,
                 analytics_end_date, source_status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id, snapshot_bucket) DO UPDATE SET
                 views=excluded.views, engaged_views=excluded.engaged_views,
                 source_status=excluded.source_status""",
            [_snapshot_values(row) for row in rows],
        )
```

- [ ] **Step 4: 특징 추출 실패 테스트 작성**

```python
def test_capture_features_survives_work_cleanup(tmp_path):
    create_uploaded_video(tmp_path, video_id="v1", run_id="20260818-1")
    write_work_jsons(
        tmp_path,
        "20260818-1",
        topic={"topic": "지하 도시", "category": "hidden_world", "verification_method": "grounded_search"},
        script={"title": "2만 명이 사라진 지하 도시", "hook": "도시가 하루아침에 비었습니다.", "scenes": [{"n": 1, "narration": "첫 장면", "duration_sec": 7}], "total_duration_sec": 60, "writer_mode": "llm"},
        produce={"actual_duration": 72.4, "intro": {"ai_generation": {"used": True}}},
    )
    store.capture_video_features(tmp_path)
    shutil.rmtree(tmp_path / "work" / "20260818-1")
    assert feature_row(tmp_path, "v1")["hook_text"] == "도시가 하루아침에 비었습니다."
    assert feature_row(tmp_path, "v1")["actual_duration_sec"] == 72.4
```

- [ ] **Step 5: 특징 보존 구현**

`videos` 행을 기준으로 `video_features`를 만들고, 작업 JSON이 있으면 `hook_text`, `script_chars`, `scene_count`, `planned_duration_sec`, `actual_duration_sec`, `writer_mode`, `verification_method`, `ai_opening_used`를 보강한다. 문자열·객체 형태의 `hook`과 `intro.ai_generation` 누락을 모두 안전하게 처리한다.

- [ ] **Step 6: 유지율과 리포트 실패 테스트 작성**

```python
def test_report_uses_mature_medians_and_warns_for_small_groups(tmp_path):
    seed_features_and_snapshots(tmp_path, views=[1000, 1200, 3300], ages=[72, 72, 72])
    report = store.build_performance_report(
        tmp_path, datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    )
    assert report["summary"]["median_views"] == 1200
    assert report["summary"]["mature_videos"] == 3
    assert report["warnings"] == ["표본 8개 미만: 성과 차이를 소재 규칙으로 확정하지 마세요."]
```

- [ ] **Step 7: 유지율 due 선택과 리포트 구현**

48시간 이상 지난 영상 중 오늘 유지율이 없는 영상을 오래된 순으로 최대 20개 반환한다. 리포트는 24시간 이상 지난 영상의 최신 스냅샷만 사용하고 평균이 아닌 중앙값, 표본 수, 부분 수집 경고를 포함한다.

- [ ] **Step 8: Task 1 검증**

Run: `venv\Scripts\python.exe -m pytest tests/test_performance_store.py -q`

Expected: all tests pass.

- [ ] **Step 9: Task 1 커밋**

```powershell
git add app/services/performance_store.py tests/test_performance_store.py
git commit -m "기능: 영상 성과 장기 저장소 추가"
```

### Task 2: YouTube 읽기 전용 수집 클라이언트

**Files:**
- Create: `app/services/youtube_performance.py`
- Create: `tests/test_youtube_performance.py`
- Create: `scripts/auth_youtube_analytics.py`

**Interfaces:**
- Consumes: `credentials/client_secret.json`, `credentials/analytics_token.json`, `YOUTUBE_API_KEY`, 영상 ID·조회 기간
- Produces: `ANALYTICS_SCOPES`, `load_analytics_credentials()`, `build_analytics_client()`, `fetch_public_statistics(video_ids)`, `fetch_owner_metrics(video_ids, start_date, end_date)`, `fetch_retention(video_id, start_date, end_date)`, `authorize_analytics()`

- [ ] **Step 1: 인증 분리 실패 테스트 작성**

```python
def test_analytics_credentials_never_use_upload_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "token.json").write_text("upload-secret")
    with pytest.raises(FileNotFoundError, match="analytics_token.json"):
        performance.load_analytics_credentials()
    assert performance.ANALYTICS_SCOPES == (
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    )
```

- [ ] **Step 2: 실패 확인**

Run: `venv\Scripts\python.exe -m pytest tests/test_youtube_performance.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: 인증과 클라이언트 생성 최소 구현**

```python
ANALYTICS_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
ANALYTICS_TOKEN = Path("credentials/analytics_token.json")

def load_analytics_credentials() -> Credentials:
    if not ANALYTICS_TOKEN.exists():
        raise FileNotFoundError("분석 인증이 필요합니다: credentials/analytics_token.json")
    credentials = Credentials.from_authorized_user_file(str(ANALYTICS_TOKEN), ANALYTICS_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        ANALYTICS_TOKEN.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("YouTube Analytics 읽기 토큰이 유효하지 않습니다")
    return credentials
```

- [ ] **Step 4: 헤더 기반 정규화 실패 테스트 작성**

```python
def test_owner_metrics_follow_column_headers_not_fixed_positions():
    response = {
        "columnHeaders": [
            {"name": "averageViewPercentage"}, {"name": "video"},
            {"name": "engagedViews"}, {"name": "views"},
        ],
        "rows": [[82.5, "v1", 800, 1000]],
    }
    assert performance.normalize_report(response) == [
        {"video": "v1", "averageViewPercentage": 82.5, "engagedViews": 800, "views": 1000}
    ]
```

- [ ] **Step 5: Data API와 Analytics API 호출 구현**

공개 통계는 50개, Analytics 영상 필터는 500개 단위로 나눈다. 소유자 지표 요청은 다음 계약을 사용한다.

```python
client.reports().query(
    ids="channel==MINE",
    startDate=start_date.isoformat(),
    endDate=end_date.isoformat(),
    dimensions="video",
    filters=f"video=={','.join(batch)}",
    metrics=(
        "engagedViews,views,estimatedMinutesWatched,averageViewDuration,"
        "averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost"
    ),
).execute()
```

유지율은 영상 하나씩 `dimensions=elapsedVideoTimeRatio`, `metrics=audienceWatchRatio,relativeRetentionPerformance`로 조회한다.

- [ ] **Step 6: 부분 응답·빈 응답 테스트 작성 및 구현**

`rows`가 없으면 빈 목록을 반환하고, 숫자가 누락된 필드는 `None`으로 정규화한다. `engaged_view_rate`는 `views > 0`일 때만 `engagedViews / views`로 계산한다.

- [ ] **Step 7: 대화형 인증 스크립트 구현**

`auth_youtube_analytics.py`는 `InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), ANALYTICS_SCOPES).run_local_server(port=0)`으로 토큰을 만들고 `reports.query(ids="channel==MINE", startDate=yesterday.isoformat(), endDate=yesterday.isoformat(), metrics="views")`를 실행한 후 분석 토큰 경로와 성공 여부만 출력한다. 토큰 내용은 출력하지 않는다.

- [ ] **Step 8: Task 2 검증**

Run: `venv\Scripts\python.exe -m pytest tests/test_youtube_performance.py -q`

Expected: all tests pass.

- [ ] **Step 9: Task 2 커밋**

```powershell
git add app/services/youtube_performance.py scripts/auth_youtube_analytics.py tests/test_youtube_performance.py
git commit -m "기능: 유튜브 분석 읽기 전용 수집 추가"
```

### Task 3: 독립 수집 명령과 성과 리포트

**Files:**
- Create: `scripts/collect_performance.py`
- Create: `tests/test_collect_performance.py`
- Modify: `app/services/performance_store.py`

**Interfaces:**
- Consumes: Task 1 저장 API, Task 2 수집 API
- Produces: `collect_performance(data_dir, now, public_fetcher, owner_fetcher, retention_fetcher) -> dict`, CLI 종료 코드, `data/reports/performance_latest.json`

- [ ] **Step 1: 완전 성공 흐름 실패 테스트 작성**

```python
def test_collector_persists_features_metrics_retention_and_report(tmp_path):
    create_uploaded_video(tmp_path, "v1", "20260815-1", uploaded_hours_ago=72)
    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: {"v1": {"views": 1100, "likes": 12, "comments": 1}},
        owner_fetcher=lambda ids, start, end: {"v1": owner_metrics(engaged_views=800)},
        retention_fetcher=lambda video_id, start, end: [{"elapsed_video_time_ratio": 0.5, "audience_watch_ratio": 0.72}],
    )
    assert result["status"] == "success"
    assert result["videos_saved"] == 1
    assert (tmp_path / "reports" / "performance_latest.json").exists()
```

- [ ] **Step 2: 실패 확인**

Run: `venv\Scripts\python.exe -m pytest tests/test_collect_performance.py -q`

Expected: FAIL because `scripts.collect_performance` does not exist.

- [ ] **Step 3: 수집 조정 최소 구현**

```python
def collect_performance(data_dir: Path, *, now: datetime, public_fetcher, owner_fetcher, retention_fetcher) -> dict:
    store.init_performance_schema(data_dir)
    store.capture_video_features(data_dir, captured_at=now)
    videos = store.list_uploaded_videos(data_dir)
    public = public_fetcher([video["video_id"] for video in videos])
    owner = owner_fetcher([video["video_id"] for video in videos], earliest_date(videos), analytics_end_date(now))
    rows = merge_metrics(videos, public, owner, now)
    store.save_performance_snapshots(data_dir, rows)
    # due 영상의 유지율을 최대 20개 수집
    report = store.build_performance_report(data_dir, now)
    write_json_atomically(data_dir / "reports" / "performance_latest.json", report)
    return report["collection"]
```

- [ ] **Step 4: 부분 실패 격리 테스트 작성**

```python
def test_public_stats_are_saved_when_analytics_is_unavailable(tmp_path):
    create_uploaded_video(tmp_path, "v1", "20260815-1", uploaded_hours_ago=72)
    result = collector.collect_performance(
        tmp_path,
        now=NOW,
        public_fetcher=lambda ids: {"v1": {"views": 900, "likes": 4, "comments": 0}},
        owner_fetcher=lambda *args: (_ for _ in ()).throw(RuntimeError("quota")),
        retention_fetcher=lambda *args: [],
    )
    assert result["status"] == "partial"
    assert latest_snapshot(tmp_path, "v1")["views"] == 900
    assert latest_snapshot(tmp_path, "v1")["source_status"] == "public_only"
```

- [ ] **Step 5: 소스별 실패 처리와 원자적 리포트 쓰기 구현**

공개 통계와 소유자 통계를 각각 `try` 블록으로 분리한다. 하나만 성공하면 `partial`, 둘 다 실패하면 `failed`를 반환한다. JSON은 같은 디렉터리의 임시 파일에 쓴 뒤 `Path.replace()`로 교체한다.

- [ ] **Step 6: 파이프라인 비침범 정적 테스트 작성**

```python
def test_existing_pipeline_does_not_import_independent_collector():
    source = Path("app/agents/orchestrator.py").read_text(encoding="utf-8")
    assert "collect_performance" not in source
    assert "youtube_performance" not in source
```

- [ ] **Step 7: CLI 구현**

CLI는 `DATA_DIR`을 읽고 성공은 0, 부분 성공도 다음 cron에서 보완 가능하므로 0, 두 데이터 소스가 모두 실패하면 1로 종료한다. 출력은 상태·저장 영상 수·리포트 경로만 포함하고 자격증명 값을 출력하지 않는다.

- [ ] **Step 8: Task 3 검증**

Run: `venv\Scripts\python.exe -m pytest tests/test_collect_performance.py tests/test_performance_store.py tests/test_youtube_performance.py -q`

Expected: all tests pass.

- [ ] **Step 9: Task 3 커밋**

```powershell
git add scripts/collect_performance.py app/services/performance_store.py tests/test_collect_performance.py
git commit -m "기능: 독립 영상 성과 수집 명령 추가"
```

### Task 4: 운영 문서와 전체 회귀 검증

**Files:**
- Modify: `agents/05_analyst.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/auth_youtube_analytics.py`, `scripts/collect_performance.py`
- Produces: 로컬 인증, 서버 토큰 전송, 최초 역수집, 독립 cron, 원복 절차

- [ ] **Step 1: 분석가 계약 문서 수정**

기존 공개 통계 분석가는 파이프라인 참고용으로 유지한다고 기록하고, 독립 수집기는 자동 소재 결정을 하지 않으며 `performance_latest.json`을 생성한다고 명시한다.

- [ ] **Step 2: 운영 명령 문서화**

다음 실제 명령과 기대 결과를 `docs/OPERATIONS.md`에 추가한다.

```powershell
venv\Scripts\python.exe scripts\auth_youtube_analytics.py
scp -i "D:\ms\ssh-key-2026-07-10.key" credentials\analytics_token.json ubuntu@168.107.15.146:~/shorts-factory-be/credentials/analytics_token.json
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && venv/bin/python -u scripts/collect_performance.py"
```

서버 cron은 기존 9개 행을 그대로 보존한 뒤 다음 독립 행만 추가한다.

```cron
20 2,8,14,20 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/collect_performance.py >> data/performance.log 2>&1
```

원복은 이 한 행만 제거하며 기존 제작 cron과 DB를 복원하지 않는다고 명시한다.

- [ ] **Step 3: 전체 회귀 테스트 실행**

Run: `venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

- [ ] **Step 4: 컴파일 검증**

Run: `venv\Scripts\python.exe -m compileall -q app scripts`

Expected: exit code 0.

- [ ] **Step 5: 변경 범위 검증**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; no `.env`, `credentials/`, `data/`, frontend files, or production media staged.

- [ ] **Step 6: Task 4 커밋**

```powershell
git add agents/05_analyst.md docs/OPERATIONS.md README.md
git commit -m "문서: 유튜브 성과 수집 운영 절차 추가"
```

### Task 5: 서버 배포와 최초 수집

**Files:**
- Deploy only: `app/services/performance_store.py`, `app/services/youtube_performance.py`, `scripts/auth_youtube_analytics.py`, `scripts/collect_performance.py`, 문서와 테스트
- Secret transfer: `credentials/analytics_token.json` (Git 제외)

**Interfaces:**
- Consumes: 검증된 로컬 커밋, 분석 전용 토큰, 현재 서버 crontab
- Produces: 서버 분석 DB 테이블, `data/reports/performance_latest.json`, 독립 performance cron

- [ ] **Step 1: 서버 상태와 백업 대상 확인**

```powershell
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && git status --short && systemctl is-active shorts-dashboard && crontab -l && df -h / | tail -1"
```

서버가 clean인지 확인하고, 서버 소스·`videos.sqlite`·crontab을 `/home/ubuntu/backups/20260818-analytics/`에 보존한다. 같은 디렉터리가 이미 있으면 덮어쓰지 않고 배포를 중단한다.

- [ ] **Step 2: 로컬 OAuth 승인**

Run: `venv\Scripts\python.exe scripts\auth_youtube_analytics.py`

Expected: 브라우저에서 채널 소유 계정 승인 후 `credentials/analytics_token.json` 생성과 시험 조회 성공.

- [ ] **Step 3: 검증된 커밋 배포와 비밀 파일 전송**

기존 배포 방식으로 백엔드 커밋을 서버에 반영한다. `analytics_token.json`은 SCP로 별도 전송하고 서버에서 `chmod 600 credentials/analytics_token.json`을 적용한다.

- [ ] **Step 4: 최초 역수집 실행**

```powershell
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && venv/bin/python -u scripts/collect_performance.py"
```

Expected: `success` 또는 최신 Analytics 지연에 따른 `partial`; 기존 업로드 영상 수만큼 특징 행 생성; 토큰 값 출력 없음.

- [ ] **Step 5: 서버 산출물 확인**

```powershell
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && python3 -c \"import json,sqlite3; d=json.load(open('data/reports/performance_latest.json',encoding='utf-8')); c=sqlite3.connect('data/videos.sqlite'); print(d['collection']); print(c.execute('select count(*) from video_features').fetchone()[0]); print(c.execute('select count(*) from video_performance_snapshots').fetchone()[0])\""
```

Expected: collection 결과와 0보다 큰 특징·스냅샷 행 수.

- [ ] **Step 6: 독립 cron 등록**

현재 `crontab -l`을 파일로 저장하고 기존 행을 그대로 유지한 채 performance 행 하나만 추가한다. 중복 등록 여부를 확인한 후 적용한다.

- [ ] **Step 7: 기존 자동화 무변경 확인**

`crontab -l`에서 기존 9개 제작 행과 새 분석 행을 구분해 확인하고, `systemctl is-active shorts-dashboard`, `/api/health`, 최근 `data/cron.log`를 확인한다. 분석 로그는 `data/performance.log`로 분리되어야 한다.

- [ ] **Step 8: 최종 Git 상태 확인**

Run: `git status --short` and `git log -6 --oneline`

Expected: worktree clean, 구현·문서 커밋 존재, 비밀 파일 미추적.
