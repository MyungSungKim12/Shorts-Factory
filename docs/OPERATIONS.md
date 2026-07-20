# Shorts Factory 운영 매뉴얼

자동 랭킹 숏츠 파이프라인의 규칙·현황·명령어 모음. 문제 생겼을 때 여기부터 본다.

> 모든 SSH 명령의 접속 키: `D:\ms\ssh-key-2026-07-10.key`, 서버: `ubuntu@168.107.15.146`
> 편의상 아래 예시는 `ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "..."` 형태.

---

## 1. 시스템 개요

| 항목 | 값 |
|------|-----|
| 서버 | Oracle Cloud, Ubuntu 24.04, IP `168.107.15.146` (E2.1.Micro 1GB + 스왑 4GB) |
| 프로젝트 경로(서버) | `/home/ubuntu/shorts-factory-be` |
| 프로젝트 경로(로컬) | `D:\ms\shorts-factory-be` (백), `D:\ms\shorts-factory-fe` (대시보드) |
| 대시보드 API | `http://168.107.15.146:8000` (systemd `shorts-dashboard`, 24시간 상시) |
| 대시보드 화면 | Vercel 배포 (프론트) |
| 자동 실행 | cron, 매일 3회 (11:00·17:00·21:00 KST) |

### 파이프라인 흐름
`리서처(소재+검증) → 작가(대본) → 프로듀서(TTS·영상·자막) → 업로더(YouTube) → 분석가(성과 갱신)`

---

## 2. 업로드 스케줄 (하루 3회)

| 회차 | 시간(KST) | 카테고리 |
|------|-----------|----------|
| 1 | 11:00 | 🐶 동물/펫 |
| 2 | 17:00 | ✈️ 여행/명소 |
| 3 | 21:00 | 🏛️ 역사 |

- 기술적 상한은 하루 6회 (YouTube API 쿼터 10,000 ÷ 업로드당 1,600).
- 운영 설정은 하루 3회이며 `.env`의 `DAILY_UPLOAD_LIMIT=3`으로 추가 실행도 차단한다.
- 회차 = `run_id`의 `-N` (예: `20260716-2` = 7/16 2회차).

---

## 3. 절대 규칙 (CLAUDE.md / AGENTS.md)

1. `.env`, `credentials/` **커밋 금지**
2. 업로드 **일 6건 한도** (코드가 `min(설정,6)`으로 강제)
3. **사실 검증**: `grounded_search`(검색) 또는 `verified_cache`(검증 캐시) 우선.
   불변 기록·수치 소재(산 높이·바다 깊이 등)에 한해 `model_memory`(모델 지식) 허용.
   최신 변동 소재(구독자·매출 순위 등)는 `model_memory` 금지.
4. 영상 길이: 목표 35~50초, 상한 180초(숏츠 최대). 15초 미만·180초 초과만 업로드 차단.
5. `verification_method`는 topic.json·로그에 항상 기록.

### LLM 폴백 순서
- 리서처(검색): Gemini 그라운딩 → (실패) 검증 캐시 → (없음) 보수 모드 model_memory
- 작가(대본): Groq gpt-oss-120b → (실패) Gemini
- 429: 분당 한도는 30/60/120초 백오프, 일일 한도는 즉시 다음 제공자

---

## 4. 상태·로그 확인 명령어

### 오늘 회차별 성공/실패 한눈에
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be/data/logs && for f in run-$(date +%Y%m%d)-*.json; do echo \"== $f ==\"; grep -E 'success|message|status' \$f; done"
```

### 실시간 로그 따라가기 (Ctrl+C로 빠져나옴)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "tail -f shorts-factory-be/data/cron.log"
```

### 최근 로그 40줄
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "tail -40 shorts-factory-be/data/cron.log"
```

### 실패한 회차만 찾기
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "grep -A2 '파이프라인 실패' shorts-factory-be/data/cron.log | tail -20"
```

### 특정 회차 상세 (예: 오늘 2회차)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cat shorts-factory-be/data/logs/run-20260716-2.json"
```

### 서버 상태 종합 (시각·서비스·cron·디스크)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "date; systemctl is-active shorts-dashboard; crontab -l; df -h / | tail -1"
```

### 대시보드 API 헬스체크 (로컬에서)
```powershell
Invoke-RestMethod "http://168.107.15.146:8000/api/health"
```

### 업로드된 영상 목록 / 성과 리포트
```powershell
(Invoke-RestMethod "http://168.107.15.146:8000/api/videos").videos
Invoke-RestMethod "http://168.107.15.146:8000/api/report"
```

---

## 5. 자주 쓰는 운영 작업

### 특정 회차 수동 재실행 (예: 2회차)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && venv/bin/python -u scripts/run_daily.py 2"
```
> 이미 만든 산출물(topic/script/output)이 있으면 건너뛰고 남은 단계만 진행(이어하기).

### 특정 회차 처음부터 다시 (기록 삭제 후 재실행)
```bash
# 예: 오늘 2회차 완전 초기화
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && rm -rf data/work/20260716-2 data/logs/run-20260716-2.json && venv/bin/python -c \"import sqlite3; d=sqlite3.connect('data/videos.sqlite'); d.execute('DELETE FROM videos WHERE date=?',('20260716-2',)); d.commit()\""
```
> ⚠️ 이미 유튜브에 올라간 영상이 있으면 **스튜디오에서 수동 삭제** 필요 (중복 방지).

### 특정 시각 1회성 예약 실행 (예: 오늘 15시에 2회차)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "echo 'cd /home/ubuntu/shorts-factory-be && venv/bin/python -u scripts/run_daily.py 2 >> /home/ubuntu/shorts-factory-be/data/cron.log 2>&1' | at 15:00; atq"
```

### 대시보드 API 재시작 (코드 반영 후)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "sudo systemctl restart shorts-dashboard && systemctl is-active shorts-dashboard"
```

### 테스트 실행
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && venv/bin/python -m pytest -q tests/"
```

### 스케줄 변경 (cron 편집)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "crontab -l"   # 현재 확인
# 편집은 로컬에서 새 crontab 파일 만들어 scp 후 'crontab 파일명'으로 적용 (대화형 편집기 회피)
```

---

## 6. 백업 & 배포

### 배포 전 백업 (코드 + DB)
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "cd shorts-factory-be && TS=$(date +%Y%m%d_%H%M%S) && mkdir -p ~/backups && cp -r app ~/backups/app_\$TS && cp data/videos.sqlite ~/backups/videos_\$TS.sqlite && ls -t ~/backups | head"
```
> git은 소스만 백업. 서버의 `videos.sqlite`·`.env`·OAuth 토큰·cron은 위 명령으로 따로 백업.

### 파일 배포 (로컬 → 서버)
```powershell
scp -i "D:\ms\ssh-key-2026-07-10.key" "D:\ms\shorts-factory-be\app\agents\파일.py" ubuntu@168.107.15.146:~/shorts-factory-be/app/agents/
```

### git 커밋 (로컬)
```powershell
cd D:\ms\shorts-factory-be
git add . ; git commit -m "메시지" ; git push
```

---

## 7. 디스크 / 용량

- 작업 폴더는 **7일 보관 후 자동 삭제** (`run_daily.py`의 cleanup). 하루 4회 × 7일 ≈ 2~4GB 유지.
- 영상 원본은 유튜브에 있으므로 로컬 삭제해도 손실 없음.
```bash
ssh -i "D:\ms\ssh-key-2026-07-10.key" ubuntu@168.107.15.146 "df -h / | tail -1; du -sh shorts-factory-be/data; ls shorts-factory-be/data/work"
```

---

## 8. 주요 파일·설정

### .env 키 (서버 `/home/ubuntu/shorts-factory-be/.env`)
| 키 | 용도 |
|----|------|
| `GEMINI_API_KEY` / `GEMINI_MODEL` | 리서처 검색 그라운딩 |
| `GROQ_API_KEY` / `GROQ_MODEL` | 작가 주력 (gpt-oss-120b) |
| `YOUTUBE_API_KEY` | 분석가 조회수 수집 (공개 데이터, OAuth 아님) |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | 스톡 영상 (2단 폴백) |
| `FFMPEG_PATH` | `/usr/bin/ffmpeg` (서버) |
| `SUBTITLE_FONT` | `Jua` (주아체) |
| `TTS_SPEED` | 나레이션 배속 (기본 1.3) |
| `CHANNEL_NAME` | 오프닝 브랜딩 (비우면 생략, 현재 비어있음) |
| `UPLOAD_PRIVACY` | `public` |
| `DAILY_UPLOAD_LIMIT` | 6 (코드가 상한 강제) |
| `DASHBOARD_TOKEN` | 대시보드 수동실행 보호 토큰 |

### credentials/ (서버, 커밋 금지)
- `client_secret.json` — YouTube OAuth 클라이언트
- `token.json` — 발급된 업로드 토큰 (프로덕션 게시라 만료 없음)

---

## 9. 트러블슈팅

| 증상 | 원인 / 조치 |
|------|-------------|
| 회차 실패 `Gemini 일일 한도 초과` | 정상 — 보수 모드(model_memory)로 폴백됨. 영상은 나옴 |
| 회차 실패 `캐시에 쓸 소재 없음` (구버전) | 규칙 완화로 해소됨. 지금은 보수 모드로 진행 |
| `허용되지 않은 검증 방식` | verification_method 값 확인. UPLOADABLE_VERIFICATION(models.py)에 있어야 함 |
| 자막/순위 텍스트 깨짐 | 항목명이 너무 긺 → producer의 short_name이 괄호 제거·절단 처리 |
| 영상에 사람 등 엉뚱한 장면 | 스톡 검색 실패 → 카테고리 안전어 폴백. produce_log의 `fallback_scenes` 확인 |
| 업로드 안 됨 `허용 범위 위반` | 영상 15초 미만 또는 180초 초과. TTS 길이 과다 → 대본 축약 |
| 대시보드 접속 안 됨 | `systemctl restart shorts-dashboard`, 오라클 콘솔 8000 포트 개방 확인 |
| 서버 코드 오류 롤백 | `~/backups/app_타임스탬프`에서 복원 |

---

## 10. 오류 해결 이력 (구축 중 겪은 문제와 조치)

구축 과정에서 실제로 부딪힌 오류들. 같은 문제 재발 시 참고.

### LLM / 검증 관련
| 오류 | 원인 | 해결 |
|------|------|------|
| 로컬 모델(Mistral/Orca2)이 엉뚱한 순위 생성 (예: "가장 강한 사람"에 오바마) | 7B 로컬 모델이 프롬프트 이해·사실성 부족 | Ollama 포기 → Gemini 무료 API로 전환 |
| Gemini `404 model not found` | 예비 모델명이 구식(gemini-2.5-flash 등 신규 제공 종료) | 키로 실제 사용 가능 모델 조회 후 목록 갱신, 404는 재시도 없이 다음 모델로 |
| Gemini `429 Too Many Requests` 반복 | 무료 그라운딩(검색) 할당량이 별도로 작아 금방 소진 | 분당 한도는 30/60/120초 백오프, 일일 한도는 즉시 제공자 전환. Groq 폴백 추가 |
| Gemini `503 high demand` | 무료 티어 서버 혼잡(우리 문제 아님) | 재시도 + 예비 모델 자동 전환 로직 |
| JSON `Extra data: line N` (리서처/작가 실패) | 그라운딩 응답이 JSON 뒤에 인용·설명을 덧붙임 | `json_extract.py`: 중괄호 균형 맞춰 첫 완전한 객체만 추출 (코드펜스·머리말·후행텍스트 제거) |
| `max_tokens must be ≤ 8192` (Groq 400) | Groq 상한 초과 요청(16000) | Groq 호출 시 8192로 캡 |
| Groq compound `413 Request Entity Too Large` | 무료 티어에서 compound 검색 모델 사용 불가 | 검색 폴백에서 compound 제거, Gemini 그라운딩만 사용 |
| **검증 캐시 데드락** (모든 회차 중단) | 캐시를 채우려면 그라운딩 성공 필요 → 할당량 소진으로 캐시가 영영 안 채워짐 | 규칙 완화: 불변 기록 소재에 한해 model_memory 허용 (CLAUDE.md/AGENTS.md 수정) |
| `허용되지 않은 검증 방식` (규칙 완화 후에도 업로드 차단) | 업로더에 하드코딩된 옛 허용목록이 model_memory 누락 | 업로더도 `models.UPLOADABLE_VERIFICATION` 참조하도록 통일 |

### 검증(Pydantic) 게이트 관련
| 오류 | 원인 | 해결 |
|------|------|------|
| `순위 씬이 역순이 아님: [0,5,4,3,2,0,1,0]` | 작가가 hook·긴장·CTA 씬에 `rank:0`을 넣음 → 검증기가 0을 순위로 오인 | `rank 0 → None` 자동 정규화 + 작가 프롬프트에 "비순위 씬은 null" 명시 |
| `씬 duration 합계 ≠ total_duration_sec` | 모델이 총 길이를 씬 합계와 다르게 적음 | 실패 대신 합계값으로 자동 보정 |

### 영상 제작(TTS/ffmpeg) 관련
| 오류 | 원인 | 해결 |
|------|------|------|
| edge-tts `403 WSServerHandshake` | MS 음성 서버 연결 차단 | edge-tts → pyttsx3 → 최종 **gTTS(한국어)**로 정착 |
| pyttsx3 무한 대기 | 시스템에 한국어 음성 없음 | gTTS로 교체 |
| gTTS `Language not supported: auto` | `lang='auto'` 미지원 | `lang='ko'` 고정 |
| ffmpeg `WinError 2 파일 없음` | FFMPEG_PATH가 소스 폴더(실행파일 없음) 가리킴 | ffmpeg Windows 빌드 다운로드 후 `-essentials_build` 경로로 수정 |
| ffmpeg `returned non-zero` (입력=출력 동일) | 다운로드 원본과 인코딩 출력 파일명이 같음 | 출력은 `enc_` 접두어로 분리 |
| ffmpeg `No such file or directory` (출력) | 자막 처리로 cwd를 tmp로 바꾸며 상대경로 출력 실패 | 출력 파일은 절대경로로 지정 |
| 자막이 영상에 안 나옴 (서버만) | 리눅스에서 ffprobe 경로 유도 실패(.exe 치환) → 자막 길이 0초 | `_ffprobe_path`로 OS별 처리 + 측정 실패 시 대본 길이로 대체 |
| 자막이 "일위/이위"로 표시 | 모델이 순위를 한글로 씀 | 표기 규칙(숫자) + 자막 단계에서 한글순위→숫자 자동 변환 |
| 보이스가 "km²"를 "케이엠"으로 읽음 | TTS가 위첨자 기호 못 읽음 | TTS 직전 단위기호→한글 발음 치환 (자막은 원본 유지) |
| 좌측 순위 텍스트 깨짐 (역사 회차) | 항목명 길어 "…"(주아체 글리프 없음) 붙음 | 괄호 부연 제거 + "…"→".." + 절단 |
| 영상에 사람 등 엉뚱한 장면 | 서술형 검색어 0건 → 첫 단어("afghan") 재검색이 사람/풍경 끌어옴 | 첫 단어 재검색 제거, 카테고리 안전어 폴백 도입 |
| 영상 60초 초과 오류 | 검증 상한이 60초로 좁았음 | 상한 180초(숏츠 최대)로 완화, 넘쳐도 업로드 |

### 인프라 / 배포 관련
| 오류 | 원인 | 해결 |
|------|------|------|
| YouTube `403 API not enabled` | OAuth 클라이언트 프로젝트에 YouTube Data API 미활성 | 해당 프로젝트에서 API 활성화 |
| OAuth `403 access_denied` | 앱이 테스트 모드, 테스트 사용자 미등록 | OAuth 동의화면에 본인 이메일 테스트 사용자 추가 |
| 토큰 7일 만료 우려 | 테스트 모드 앱의 refresh token 제약 | OAuth 앱 "게시"(프로덕션)로 전환 |
| 분석가 `403 insufficient scopes` | 업로드 토큰엔 통계 조회 권한 없음 | 공개 데이터는 OAuth 대신 **YouTube API 키**로 조회 (재인증 회피) |
| GitHub push 거부 (`secret detected`) | `.env.example`에 실제 API 키가 들어감 | 자리표시자로 교체 후 `--amend`, 이력 초기화 |
| 서버 로그가 실행 후에만 보임 | 파이썬 출력 버퍼링 | cron 명령에 `python -u`(unbuffered) 추가 |

## 11. 다음 개선 후보 (미구현)

- 무료 웹검색(DuckDuckGo) 붙여 model_memory 비중 줄이고 실제 검증 강화
- YouTube Analytics(시청지속·24/72h 성과) — 재인증 필요
- 카테고리/시간대 분리 실험 (요일별 순환)
- 실패 시 알림(텔레그램 등)
