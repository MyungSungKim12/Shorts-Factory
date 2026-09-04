# 이상한 지구기록 — 유튜브 스토리형 Shorts 자동화

유튜브 숏츠를 **기획 → 대본 → 영상 생성 → 업로드 → 분석 → 개선**까지 자동화하는 파이프라인.
Python 에이전트 모듈이 각 단계를 담당하며, 무료 스톡과 Google Cloud 서비스를 조합해 하루 4회 운영한다.

---

## 1. 전체 프로젝트 아이템 (자동 수익화 후보군)

| # | 아이템 | 수익 모델 | 난이도 | 자동화 적합도 | 비고 |
|---|--------|----------|--------|--------------|------|
| 1 | **유튜브 숏츠 자동 채널** ← 본 프로젝트 | 애드센스, 제휴링크 | 중 | ★★★★★ | 조회수 기반, 얼굴 노출 불필요 |
| 2 | 블로그/티스토리 자동 포스팅 | 애드센스, 쿠팡파트너스 | 하 | ★★★★★ | 텍스트만으로 가능, 숏츠와 소재 공유 |
| 3 | 인스타 릴스 재활용 업로드 | 리워드 프로그램, 제휴 | 하 | ★★★★☆ | 숏츠 결과물 재활용 (원소스 멀티유즈) |
| 4 | 전자책/노션 템플릿 판매 | 크몽, 스마트스토어 | 중 | ★★★☆☆ | 축적된 콘텐츠를 상품화 |
| 5 | GitHub 오픈소스 + 스폰서 | GitHub Sponsors, 광고 | 상 | ★★☆☆☆ | 장기전, 개발자 브랜딩 |

**전략: 1번(숏츠)을 메인으로 구축 → 같은 소재로 2번(블로그), 3번(릴스)에 재배포하는 원소스 멀티유즈(OSMU) 구조.**
파이프라인이 한 번 완성되면 소재만 바꿔 채널을 복제할 수 있다.

### 운영 콘텐츠 포맷: 검증 가능한 지구 미스터리
- 한 번의 소재 호출 안에서 후보 8개를 만들고 호기심·반전·위험·규모·영상 확보성·차별성을 비교한다.
- 목표 점수는 24/30이지만 기준 미달로 회차를 중단하지 않는다. 후보 중 최고점을 반드시 선택해 업로드한다.
- 11시·17시는 과학의 경계와 미해결 관측, 14시·21시는 숨겨진 세계와 금지된 구조로 회차를 구분한다.
- 하위 영역은 대기·기상, 지질·극한 자연, 지구 기록·탐사 이상, 변칙 물리·기술, 고대 구조·기술, 지하·금지 시설, 버려진 장소·제한 구역, 빙하·극지를 이틀 단위로 순환한다.
- 우주·천문과 바다·심해는 최근 과다 노출 영역으로 감점하며, 강한 실물 장면·위험·규모·반전이 검증될 때만 예외적으로 선택한다.
- 동물 중심 소재는 제외하며, 초자연적 결론 대신 확인된 사실과 아직 설명되지 않은 부분을 분리한다.
- 최신 변동 소재는 검색 검증이 필수이며, 불변 기록·수치만 보수 모드에서 모델 지식을 허용한다.

---

## 2. 전체 흐름 (파이프라인)

```
[스케줄러: 매일 정해진 시간]
        │
        ▼
① 리서처 모듈 ───────────── 후보 8개 내부 비교 후 가장 강한 소재 선정·사실 검증
        │
        ▼
② 대본 작가 모듈 ────────── 60~75초 스토리 대본 + 제목/설명/태그 생성
        │
        ▼
③ 영상 제작 모듈 ────────── Google 여성 TTS + 실제 이미지 기반 AI 오프닝 + 자막
        │                    → ffmpeg로 합성 (D:\ms\ffmpeg-8.1.2 활용)
        ▼
④ 업로더 모듈 ───────────── YouTube Data API v3로 자동 업로드
        │
        ▼
⑤ 분석 모듈 ──────────────── 공개 가능한 조회·반응 지표 수집(참고용)
```

---

## 3. 무료 툴 스택

| 역할 | 툴 | 무료 조건 |
|------|-----|----------|
| 오케스트레이션 | Python 스케줄러 + Linux cron | 서버 자동 실행 |
| 백엔드 | Python FastAPI | 오픈소스 |
| 프론트(대시보드) | React + Vite | 오픈소스 |
| TTS 음성 | Google Cloud Chirp 3 HD 여성 음성, 실패 시 gTTS | 무료 크레딧/무료 폴백 |
| 영상 합성 | ffmpeg (이미 보유: D:\ms\ffmpeg-8.1.2) | 오픈소스 |
| 이미지 소스 | Pexels/Pixabay API | 무료 API 키 |
| AI 오프닝 | Vertex AI Veo 이미지→영상, 실패 시 무료 스톡 | Google Cloud 크레딧 사용 |
| 업로드 | YouTube Data API v3 | 프로젝트 쿼터 범위에서 일 4회 운영 |
| 데이터 저장 | SQLite | 파일 기반, 설치 불필요 |
| 분석 | YouTube Analytics API | 무료 |

AI 오프닝은 검증된 실제 이미지를 첫 프레임으로 사용하고 작은 카메라 움직임만 생성한다. 결과물은 `data/media/ai_openings/`에 영구 보관해 같은 실제 대상에서 재사용하며, API 실패나 크레딧 소진 시 업로드를 중단하지 않고 무료 스톡으로 폴백한다.

`AI_CREDIT_MODE=auto`에서는 Vertex Gemini 소재·대본, Gemini 2.5 Flash TTS 여성 음성, Veo 후보 2개 비교를 사용한다. 예상 잔액이 `CLOUD_CREDIT_FLOOR_KRW`(운영값 80,000원) 이하가 되면 신규 유료 호출을 자동 중단한다. 무료 모드에서도 이미 검증·저장된 동일 대상 AI 영상은 계속 재사용한다.

---

## 4. 에이전트 구성 (agents/ 디렉토리)

| 파일 | 에이전트 | 담당 |
|------|---------|------|
| `agents/00_orchestrator.md` | 오케스트레이터 | 전체 파이프라인 지휘, 에러 복구 |
| `agents/01_trend-researcher.md` | 트렌드 리서처 | 소재 발굴, 경쟁 분석 |
| `agents/02_script-writer.md` | 대본 작가 | 숏츠 대본, 제목/태그(SEO) |
| `agents/03_video-producer.md` | 영상 프로듀서 | TTS·자막·합성 스크립트 실행 |
| `agents/04_uploader.md` | 업로더 | 업로드, 메타데이터, 예약 발행 |
| `agents/06_longform-producer.md` | 롱폼 프로듀서 | 수동 검토용 6~10분 롱폼 파일 제작 |
| `agents/05_analyst.md` | 분석가 | 성과 수집, 개선 리포트 |

각 md는 **역할 정의 + 입출력 계약 + 실제 런타임 위치**를 설명한다. 실제 실행은 `app/agents/`의 Python 모듈이 담당한다.

---

## 5. 실행 방법

```bash
# 백엔드
cd D:\ms\shorts-factory-be
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 프론트엔드
cd D:\ms\shorts-factory-fe
npm install
npm run dev
```

### 회차별 수동 소재 예약

대시보드는 오늘과 내일의 4개 회차를 표시한다. 각 회차의 소재 입력 마감(사전 제작)과 업로드 시각은 모두 KST 기준이다.

| 회차 | 소재 입력 마감 | 업로드 |
|------|----------------|--------|
| 1 | 09:00 | 11:00 |
| 2 | 12:00 | 14:00 |
| 3 | 15:00 | 17:00 |
| 4 | 19:00 | 21:00 |

1. 대시보드의 **관리자 토큰** 칸에 서버 `.env`의 `DASHBOARD_TOKEN` 값을 입력한다. 값은 브라우저의 `sessionStorage`에만 보관되므로 탭 세션이 끝나면 다시 입력해야 한다.
2. 마감 전 회차에 소재와 선택 조건을 입력하고 **소재 확인**을 누른다. 검증 결과가 `reservable`이면 **이 소재 예약**을 눌러 `reserved` 상태를 확인한다.
3. 마감 시각에 사전 제작이 시작되며, 완성본은 `review_ready`에서 업로드되지 않고 승인 또는 반려를 기다린다.
4. 업로드 시각 전에 승인하면 해당 시각의 cron이 업로드한다. 업로드 시각이 지난 뒤 `held` 또는 `review_ready` 영상을 승인하면 즉시 업로드 작업을 시작한다.
5. 반려하면 산출물은 `data/rejected/{run_id}-attempt-{N}`에 보관되고 기본 7일 뒤 정리된다. 마감 전에는 다른 소재를 입력해 다시 확인할 수 있고, 같은 소재 재시도와 새 소재 재검사는 시도 번호를 올려 별도 이력을 남긴다.

제작이 시작되기 전 자동 운영으로 되돌리려면 **수동 예약 취소**를 사용한다. 취소가 완료되면 수동 예약 게이트가 제거되고 카드가 `mode=auto`, `state=auto`로 돌아가며, 해당 회차는 기존 자동 제작·업로드 경로를 사용한다.

실패 상태에서는 작업자가 해제된 뒤 **같은 소재 재시도**, **새 소재로 재검사**, 또는 **건너뛰기**를 선택한다. 재시도는 기존 산출물을 덮어쓰지 않고 새 attempt로 진행하며, 승인되지 않은 수동 영상은 자동 영상으로 대체하거나 YouTube에 업로드하지 않는다.

---

## 6. 수익화 로드맵

| 단계 | 기간(예상) | 목표 | 수익 |
|------|-----------|------|------|
| 1. 구축 | 1~2주 | 파이프라인 완성, 일 1개 자동 업로드 | 0원 |
| 2. 축적 | 1~3개월 | 구독자 1,000명 + 시청 1,000만 뷰(숏츠 조건) | 0원 |
| 3. 수익 개시 | 3개월~ | 애드센스 승인, 숏츠 수익 배분 | 소액 시작 |
| 4. 확장 | 6개월~ | 제휴링크(쿠팡파트너스) + 채널 복제 + 블로그 재배포 | 복수 수익원 |

**현실 체크**: 숏츠 애드센스는 RPM이 낮아(1,000뷰당 수십 원 수준) 초기엔 소액이다.
따라서 4단계의 제휴링크·OSMU 확장이 실질 수익의 핵심이며, 파이프라인 자동화로
운영 비용(시간)을 0에 수렴시키는 것이 이 프로젝트의 본질적 가치다.

---

## 7. 주의사항

- YouTube는 **재사용 콘텐츠/대량 생산 스팸**에 수익화 제한을 건다. → 대본·편집에 고유 가치(정보, 스토리)를 넣는 것이 에이전트 프롬프트의 핵심 목표.
- API 키·OAuth 토큰은 `.env`로 관리하고 절대 커밋하지 않는다.
- 업로드 쿼터: YouTube API 기본 일 10,000 → 업로드 1건당 1,600 소모 → **일 최대 6건**.

---

## 8. 스토리형 Shorts 설정

운영 포맷은 단일 소재를 목표 60~75초 동안 설명하는 스토리형이다. 실제 TTS 발화가 계획보다 길어지면 음성을 자르지 않고 최종 180초까지 허용한다. 로컬 샘플 명령은 리서처 → 작가 → 프로듀서까지만 실행하며 업로더, `data/work/`, SQLite, cron을 변경하지 않는다.

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project shorts-factory-502004
$env:CONTENT_FORMAT='story'
$env:TTS_PROVIDER='google'
python scripts\generate_sample.py --sample-id story-v1
```

결과는 `data/samples/story-v1/` 아래의 `topic.json`, `script.json`, `produce_log.json`, `validation.json`, `output.mp4`에 저장된다. Google 음성 호출에 실패하면 제작 로그에 실제 공급자를 기록하고 gTTS로 폴백한다.

## 롱폼 제작

쇼츠 자동 공장은 그대로 유지하고, 롱폼은 별도 수동 검토 라인으로 만든다.

1. 먼저 스타일 미리보기를 만든다.

```powershell
python scripts\generate_longform.py --run-id longform-demo --preview-styles
```

결과는 `data/longform/style-previews/{run_id}/` 아래 `documentary.png`, `cinematic.png`, `clean_news.png`로 저장된다.

2. 마음에 드는 스타일을 고른 뒤 `data/longform/{run_id}/script.json`에 `style_id`를 넣고 렌더링한다. 기본값은 `clean_news`다.

```powershell
python scripts\generate_longform.py --run-id longform-demo
```

결과는 `data/longform/{run_id}/output.mp4`와 `produce_log.json`에 저장된다. 롱폼은 쇼츠 자동 공장과 분리되어 있으며, 사람이 확인한 뒤 아래 명령으로 별도 업로드한다.

```powershell
python scripts\upload_longform.py --run-id longform-demo
```

업로드 결과는 `data/longform/{run_id}/upload_log.json`에 저장되며, 같은 `run_id`는 중복 업로드하지 않는다.

롱폼에서 사용한 AI 자산은 `data/media/ai_openings/` 영구 라이브러리에 남아 같은 실제 대상의 쇼츠 제작 시 재사용할 수 있다.

운영 환경은 다음 값을 사용한다.

```dotenv
CONTENT_FORMAT=story
TTS_PROVIDER=google
```

Google TTS 호출이 실패하면 제작 로그에 실제 공급자를 기록하고 gTTS로 자동 폴백한다.
# 독립 성과 분석

자동 제작·업로드와 분리된 성과 수집 명령을 제공한다.

```powershell
venv\Scripts\python.exe scripts\auth_youtube_analytics.py
venv\Scripts\python.exe scripts\collect_performance.py
```

분석 인증은 `credentials/analytics_token.json`을 사용하며 업로드 인증인 `credentials/token.json`을 변경하지 않는다. 수집 결과는 `data/reports/performance_latest.json`과 `data/videos.sqlite`의 영구 분석 테이블에 저장된다. 분석 API가 실패해도 자동 제작·업로드 프로세스에는 영향을 주지 않는다.
