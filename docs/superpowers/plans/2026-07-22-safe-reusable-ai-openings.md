# 안전한 재사용형 AI 오프닝 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 검증된 실제 이미지를 Veo 첫 프레임으로 사용하는 안전한 AI 오프닝을 만들고, 결과물을 7일 정리와 무관한 영구 라이브러리에 보관·재사용한다.

**Architecture:** `ai_opening_library.py`가 영구 파일과 SQLite 색인을 관리하고, `vertex_video.py`가 Google Gen AI SDK 호출만 담당한다. `story_producer.py`는 기존 자산 조회 → 실제 이미지 확보 → 이미지 기반 생성 → 검증 → 스톡 폴백 순서만 조율하며 어떤 AI 실패도 업로드 실패로 전파하지 않는다.

**Tech Stack:** Python 3.12, google-genai Vertex AI SDK, SQLite, Pillow, ffmpeg/ffprobe, pytest

## Global Constraints

- 텍스트만으로 특정 실제 대상 영상을 생성하지 않는다.
- 기준 이미지는 실제 대상 일치·출처·라이선스가 확인돼야 한다.
- Veo 생성은 4초, 9:16, 무음이며 실제 사용 길이는 최대 3초다.
- 실제 대상 자산은 동일한 `subject_key`에만 재사용한다.
- 최근 14일 사용 자산은 가능한 다른 자산이 있으면 피한다.
- `data/media/ai_openings/`는 자동 삭제하지 않는다.
- 생성·검증 실패 자산도 `rejected`로 영구 보관하되 제작에 사용하지 않는다.
- AI 실패, 할당량·크레딧 소진, SDK 부재는 스톡 폴백으로 끝나야 한다.
- 기존 하루 4회 cron과 `WORK_RETENTION_DAYS=7`은 변경하지 않는다.
- `.env`, 인증 파일, 운영 데이터는 커밋하거나 배포로 덮어쓰지 않는다.

---

### Task 1: 영구 자산 라이브러리와 보존 경계

**Files:**
- Create: `app/services/ai_opening_library.py`
- Create: `tests/test_ai_opening_library.py`
- Modify: `tests/test_recovery.py`
- Read-only behavior: `scripts/run_daily.py:26-44`

**Interfaces:**
- Consumes: `data_dir: Path`, `subject_key: str`, 기준 이미지 메타데이터
- Produces: `AiOpeningAsset`, `find_reusable_asset()`, `create_asset_workspace()`, `register_asset()`, `mark_asset_used()`

- [ ] **Step 1: 영구 경계와 재사용 선택 실패 테스트 작성**

```python
def test_work_cleanup_never_deletes_permanent_ai_library(tmp_path):
    old_work = tmp_path / "work" / "20200101-1"
    permanent = tmp_path / "media" / "ai_openings" / "asset-1"
    old_work.mkdir(parents=True)
    permanent.mkdir(parents=True)
    (permanent / "master.mp4").write_bytes(b"keep")
    cleanup_old_work(tmp_path, keep_days=7)
    assert not old_work.exists()
    assert (permanent / "master.mp4").read_bytes() == b"keep"

def test_library_reuses_only_same_exact_subject(tmp_path):
    library = AiOpeningLibrary(tmp_path)
    asset_dir = tmp_path / "media" / "ai_openings" / "asset-1"
    asset_dir.mkdir(parents=True)
    for name in ("reference.jpg", "master.mp4", "opening.mp4"):
        (asset_dir / name).write_bytes(b"asset")
    library.register_asset(metadata={
        "asset_id": "asset-1", "subject_key": "richat-structure",
        "reuse_scope": "exact_subject", "status": "ready",
        "reference_path": str(asset_dir / "reference.jpg"),
        "master_path": str(asset_dir / "master.mp4"),
        "opening_path": str(asset_dir / "opening.mp4"),
    })
    assert library.find_reusable_asset("richat-structure") is not None
    assert library.find_reusable_asset("eye-of-sahara-lookalike") is None
```

- [ ] **Step 2: 테스트가 모듈 부재로 실패하는지 확인**

Run: `python -m pytest -q tests/test_ai_opening_library.py tests/test_recovery.py -p no:cacheprovider`

Expected: `ModuleNotFoundError: app.services.ai_opening_library`

- [ ] **Step 3: 라이브러리 최소 구현**

```python
@dataclass(frozen=True)
class AiOpeningAsset:
    asset_id: str
    subject_key: str
    opening_path: Path
    master_path: Path
    reference_path: Path
    status: str
    last_used_at: str | None

class AiOpeningLibrary:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "media" / "ai_openings"
        self.db_path = Path(data_dir) / "videos.sqlite"
```

나머지 공개 메서드의 정확한 시그니처는 `find_reusable_asset(subject_key: str, *, cooldown_days: int = 14, now: datetime | None = None) -> AiOpeningAsset | None`, `create_asset_workspace(subject_key: str) -> tuple[str, Path]`, `register_asset(*, metadata: dict) -> AiOpeningAsset`, `mark_asset_used(asset_id: str, run_id: str) -> None`다. SQLite에는 `ai_opening_assets`와 `ai_opening_usage`를 `CREATE TABLE IF NOT EXISTS`로 만들고, 상태가 `ready`이며 파일이 실제 존재하는 동일 `subject_key`만 반환한다. 다른 자산이 없으면 14일 이내 사용 자산도 재사용해 호출·업로드 실패를 피한다.

- [ ] **Step 4: 라이브러리와 기존 복구 테스트 통과 확인**

Run: `python -m pytest -q tests/test_ai_opening_library.py tests/test_recovery.py -p no:cacheprovider`

Expected: PASS

---

### Task 2: Vertex AI 이미지 기반 영상 생성 어댑터

**Files:**
- Create: `app/services/vertex_video.py`
- Create: `tests/test_vertex_video.py`
- Modify: `requirements.txt`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `reference_image: Path`, `output: Path`, `subject: str`, 환경설정
- Produces: `VeoGenerationResult`, `generate_opening_video()`

- [ ] **Step 1: SDK 호출 계약과 비활성화 실패 테스트 작성**

```python
def test_disabled_generation_never_constructs_client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEO_OPENING_ENABLED", "false")
    with pytest.raises(VeoUnavailable, match="disabled"):
        generate_opening_video(tmp_path / "ref.jpg", tmp_path / "out.mp4", "Richat")

def test_image_generation_uses_no_audio_and_vertical_four_seconds(tmp_path, fake_client):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"image")
    result = generate_opening_video(
        reference, tmp_path / "output.mp4", "Richat Structure",
        client=fake_client, sleep_fn=lambda seconds: None,
    )
    config = fake_client.models.calls[0]["config"]
    assert config.duration_seconds == 4
    assert config.aspect_ratio == "9:16"
    assert config.generate_audio is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest -q tests/test_vertex_video.py -p no:cacheprovider`

Expected: FAIL because `app.services.vertex_video` does not exist.

- [ ] **Step 3: Google Gen AI SDK 어댑터 구현**

```python
@dataclass(frozen=True)
class VeoGenerationResult:
    output: Path
    model: str
    duration_sec: int
    estimated_cost_usd: float

def generate_opening_video(reference_image, output, subject, *, client=None, sleep_fn=time.sleep):
    if os.getenv("VEO_OPENING_ENABLED", "false").lower() != "true":
        raise VeoUnavailable("Veo opening is disabled")
    client = client or genai.Client(vertexai=True, project=project, location=location)
    operation = client.models.generate_videos(
        model=model,
        prompt=motion_prompt(subject),
        image=types.Image.from_file(location=str(reference_image)),
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=4,
            aspect_ratio="9:16",
            resolution="720p",
            generate_audio=False,
            person_generation="dont_allow",
            negative_prompt="morphing, new objects, text, logo, people, geographic changes",
        ),
    )
    # 제한시간까지 operation을 폴링하고 Video.save(output)를 사용한다.
```

`google-genai>=1.0,<2`를 추가한다. SDK import, 인증, API, 할당량, 안전 필터, 시간 초과는 모두 `VeoUnavailable` 또는 `VeoGenerationFailed`로 정규화한다.

- [ ] **Step 4: 환경 예제에 비밀값 없는 설정 추가**

```dotenv
GOOGLE_CLOUD_PROJECT=shorts-factory-502004
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_VERTEXAI=true
VEO_OPENING_ENABLED=false
VEO_MODEL=veo-3.1-fast-generate-001
VEO_OPENING_MAX_SEC=3.0
VEO_TIMEOUT_SEC=900
VEO_POLL_SEC=15
```

- [ ] **Step 5: Vertex 어댑터 테스트 통과 확인**

Run: `python -m pytest -q tests/test_vertex_video.py -p no:cacheprovider`

Expected: PASS without a real Vertex API call.

---

### Task 3: 생성 영상 검증과 영구 등록

**Files:**
- Modify: `app/services/ai_opening_library.py`
- Modify: `app/services/media_probe.py`
- Modify: `tests/test_ai_opening_library.py`

**Interfaces:**
- Consumes: 기준 이미지, Veo 원본, ffmpeg 경로
- Produces: `validate_ai_opening() -> dict`, `build_opening_derivative()`

- [ ] **Step 1: 첫 프레임 불일치와 정상 영상 테스트 작성**

```python
def test_reference_frame_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_opening_library, "frame_distance", lambda *_: 40)
    report = validate_ai_opening(reference, master, ffmpeg="ffmpeg")
    assert report["passed"] is False
    assert "reference_frame_mismatch" in report["failures"]

def test_rejected_asset_remains_on_disk_but_is_not_reusable(tmp_path):
    asset = library.register_asset(metadata={
        "asset_id": "rejected-1", "subject_key": "richat-structure",
        "reuse_scope": "exact_subject", "status": "rejected",
        "reference_path": str(reference), "master_path": str(master),
        "opening_path": str(opening),
    })
    assert asset.master_path.exists()
    assert library.find_reusable_asset(asset.subject_key) is None
```

- [ ] **Step 2: 검증 테스트 실패 확인**

Run: `python -m pytest -q tests/test_ai_opening_library.py -p no:cacheprovider`

Expected: FAIL because validation functions are missing.

- [ ] **Step 3: ffprobe와 지각 해시 기반 검증 구현**

`ffmpeg`로 첫 프레임 PNG를 추출하고 Pillow로 16x16 grayscale average hash를 계산한다. 영상은 재생 가능 MP4, 세로 비율, 3.5~8.5초, 첫 프레임 해밍 거리 기본 32/256 이하를 요구한다. 파생본은 원본을 건드리지 않고 최대 `VEO_OPENING_MAX_SEC`만 잘라 H.264/AAC 없는 무음 MP4로 만든다.

- [ ] **Step 4: 검증·영구 보존 테스트 통과 확인**

Run: `python -m pytest -q tests/test_ai_opening_library.py tests/test_media_probe.py -p no:cacheprovider`

Expected: PASS

---

### Task 4: 스토리 프로듀서 통합과 무조건 폴백

**Files:**
- Modify: `app/agents/story_producer.py`
- Modify: `app/services/quality_gate.py`
- Modify: `tests/test_story_producer.py`
- Modify: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: `AiOpeningLibrary`, `generate_opening_video()`, 실제 Wikimedia 기준 이미지
- Produces: 오프닝 전략 `ai_library | vertex_veo_image | stock_after_veo_failure | exact_stock`

- [ ] **Step 1: 재사용 우선·신규 생성·폴백 테스트 작성**

기존 테스트 픽스처에 `FakeLibrary`와 `fake_generator`를 추가한다. 동일 대상의 `ready` 자산을 반환하는 경우 결과 전략은 `ai_library`이고 생성기 호출 횟수는 0이어야 한다. 라이브러리가 비어 있고 정확한 기준 이미지가 있으면 `vertex_veo_image`와 영구 `opening.mp4`를 반환해야 한다. 생성기가 `VeoGenerationFailed("quota")`를 던지면 예외 없이 `stock_after_veo_failure`와 존재하는 `required_media`를 반환해야 한다.

- [ ] **Step 2: 기존 테스트와 새 테스트의 예상 실패 확인**

Run: `python -m pytest -q tests/test_story_producer.py tests/test_quality_gate.py -p no:cacheprovider`

Expected: FAIL only for new AI strategy assertions.

- [ ] **Step 3: `_select_opening_source()` 통합 구현**

함수는 `identity`, `topic`, `work_dir`, `temp_dir`, `used_ids`, 선택적 라이브러리·생성기를 받는다. `subject_key`는 첫 `exact_queries` 값을 소문자·공백 정규화한 안정 문자열로 만든다. 영구 자산 조회가 먼저이며, 신규 생성은 정확한 Wikimedia 기준 이미지 확보 후에만 수행한다. 생성 실패는 구조화된 `ai_generation` 로그로 변환하고 정확한 스톡 이미지 또는 기존 일반 스톡을 사용한다.

- [ ] **Step 4: 인트로 합성과 품질 게이트 갱신**

AI 편집본이 있으면 제목 인트로의 최대 3초에만 사용하고 나머지는 실제 스톡으로 채운다. 품질 게이트는 `vertex_veo_image`와 `ai_library` 모두 기준 이미지의 정확 출처, 자산 상태 `ready`, 대상 키 일치를 요구한다. AI 영상 자체를 `exact_source`로 간주하지 않는다.

- [ ] **Step 5: 프로듀서 관련 테스트 통과 확인**

Run: `python -m pytest -q tests/test_story_producer.py tests/test_quality_gate.py tests/test_ai_opening_library.py tests/test_vertex_video.py -p no:cacheprovider`

Expected: PASS

---

### Task 5: 운영 문서, 실제 샘플, 서버 배포

**Files:**
- Modify: `agents/03_video-producer.md`
- Modify: `README.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/superpowers/plans/2026-07-22-safe-reusable-ai-openings.md`
- Server: `/home/ubuntu/shorts-factory-be`

**Interfaces:**
- Consumes: 완성된 로컬 구현과 서버 ADC
- Produces: 영구 AI 자산 1건, 서버 활성 설정, 다음 회차부터 안전한 오프닝

- [ ] **Step 1: 문서에 실제 우선순위와 영구 보존 명시**

`AI 라이브러리 → 검증된 실제 이미지 기반 Veo → 무료 스톡`, `data/work` 7일 삭제, `data/media/ai_openings` 무기한 보존, 용량 확인 명령을 기록한다. 텍스트 기반 실제 대상 생성 설명은 제거한다.

- [ ] **Step 2: 전체 변경 범위 검증**

Run:

```powershell
python -m pytest -q tests/test_ai_opening_library.py tests/test_vertex_video.py tests/test_story_producer.py tests/test_quality_gate.py tests/test_recovery.py -p no:cacheprovider
python -m compileall -q app scripts
git diff --check
```

Expected: all selected tests PASS, compile and diff check exit 0.

- [ ] **Step 3: 서버 백업과 안전 배포**

서버의 `app`, `scripts`, `tests`, `agents`, `requirements.txt`, `.env`, `credentials`, `data`, crontab을 타임스탬프 백업한다. 추적된 변경 파일만 배포하고 `.env`, `credentials`, `data`는 덮어쓰지 않는다. 새 의존성을 서버 venv에 설치한다.

- [ ] **Step 4: 서버 환경 활성화와 샘플 한 건 생성**

서버 `.env`에 기존 값을 보존하면서 `VEO_OPENING_ENABLED=true`, 프로젝트, 위치, 모델, 시간 제한을 추가한다. 다음 미래 회차와 겹치지 않는 샘플 ID로 리서처·작가·프로듀서까지만 실행하고 업로드하지 않는다. `produce_log.json`에서 기준 이미지 출처, `vertex_veo_image` 또는 명확한 폴백, 영구 자산 경로를 확인한다.

- [ ] **Step 5: 영구 보존과 서비스 상태 확인**

샘플 작업폴더와 별개로 `data/media/ai_openings/<asset_id>/master.mp4`, `opening.mp4`, `reference.*`, `metadata.json`이 존재하는지 확인한다. 대시보드를 재시작하고 `/api/health`, cron 9개, 7일 정리 경계가 그대로인지 확인한다.

- [ ] **Step 6: 한글 커밋과 `main` 푸시**

```bash
git add .env.example README.md requirements.txt agents/03_video-producer.md app/agents/story_producer.py app/services/ai_opening_library.py app/services/media_probe.py app/services/quality_gate.py app/services/vertex_video.py docs/OPERATIONS.md tests/test_ai_opening_library.py tests/test_quality_gate.py tests/test_recovery.py tests/test_story_producer.py tests/test_vertex_video.py docs/superpowers/plans/2026-07-22-safe-reusable-ai-openings.md
git commit -m "기능: 안전한 AI 오프닝 자산 재사용"
git push origin main
```
