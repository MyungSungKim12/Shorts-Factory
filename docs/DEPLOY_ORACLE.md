# Oracle Cloud 무료 서버 배포 가이드 — 완전 자동화

컴퓨터를 꺼놔도 매일 정해진 시간에 영상이 자동 생성·업로드되도록
Oracle Cloud Always Free 서버에 파이프라인을 올리는 절차.

## 사전 준비 (로컬에서 확인할 것)

- [ ] 로컬에서 전체 파이프라인 성공 (생성→업로드)
- [ ] OAuth 앱 **프로덕션 게시** 완료 (테스트 모드면 토큰 7일 만료 → 자동화 불가)
- [ ] `credentials/token.json` 존재 (로컬에서 인증 완료된 토큰 — 서버에서는 브라우저를 못 열므로 이 파일을 그대로 복사해 감)

## 1. Oracle Cloud VM 생성

1. https://cloud.oracle.com 가입 (해외결제 가능한 카드 필요 — 확인용이며 과금 없음)
2. 콘솔 → **Compute** → **Instances** → **Create Instance**
3. 설정:
   - **Image**: Ubuntu 24.04 (또는 22.04)
   - **Shape**: `VM.Standard.A1.Flex` — **4 OCPU / 24GB RAM까지 영구 무료** (ARM)
     - A1이 품절이면 `VM.Standard.E2.1.Micro` (1GB, 느리지만 가능)
   - **SSH 키**: "Generate a key pair" → 개인키 다운로드 (분실 시 접속 불가!)
4. 생성 후 **공용 IP** 메모

## 2. 서버 접속 및 기본 환경

```bash
# 로컬 PowerShell에서 (다운받은 개인키 사용)
ssh -i C:\경로\ssh-key.key ubuntu@서버IP

# --- 이하 서버 안에서 ---
sudo apt update && sudo apt install -y python3-venv python3-pip ffmpeg fonts-nanum
sudo timedatectl set-timezone Asia/Seoul   # 예약 시간을 한국 기준으로
```

## 3. 프로젝트 업로드

```powershell
# 로컬 PowerShell에서 — 프로젝트 폴더 복사 (venv, data 제외하면 가벼움)
scp -i C:\경로\ssh-key.key -r D:\ms\shorts-factory-be ubuntu@서버IP:~/
```

주의: `.env`와 `credentials/`(client_secret.json, token.json)가 **반드시 포함**돼야 한다.
git으로 올리는 경우 이 둘은 gitignore라 따로 scp로 보낼 것.

## 4. 서버용 설정 (.env 수정)

```bash
# 서버에서
cd ~/shorts-factory-be
nano .env
```

리눅스에 맞게 두 줄 변경:
```
FFMPEG_PATH=/usr/bin/ffmpeg
SUBTITLE_FONT=NanumGothic
```

## 5. 의존성 설치 및 1회 수동 테스트

```bash
cd ~/shorts-factory-be
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 수동 실행 테스트 (성공하면 업로드까지 됨)
venv/bin/python scripts/run_daily.py
```

## 6. 매일 자동 실행 (cron)

```bash
crontab -e   # 처음이면 에디터 선택: 1 (nano)
```

맨 아래 한 줄 추가 — 매일 18:00(한국시간) 실행:
```
0 18 * * * cd /home/ubuntu/shorts-factory-be && venv/bin/python scripts/run_daily.py >> data/cron.log 2>&1
```

## 7. 운영 확인

```bash
# 실행 로그 확인
tail -50 ~/shorts-factory-be/data/cron.log

# 오늘 파이프라인 결과
cat ~/shorts-factory-be/data/logs/run-$(date +%Y%m%d).json
```

## 트러블슈팅

| 증상 | 원인/해결 |
|------|----------|
| 자막이 □□□로 깨짐 | fonts-nanum 미설치 또는 SUBTITLE_FONT 미변경 |
| invalid_grant 에러 | token.json 만료 — OAuth 앱이 테스트 모드인지 확인, 로컬에서 재인증 후 token.json 다시 복사 |
| A1 인스턴스 생성 실패 (Out of capacity) | 리전 혼잡 — 몇 시간 뒤 재시도 또는 E2.1.Micro로 시작 |
| ffmpeg 인코딩 매우 느림 | E2.1.Micro(1GB)면 정상 — A1.Flex로 이전 권장 |

## 비용

전부 Always Free 범위: VM(A1 4코어/24GB), 스토리지 200GB, 트래픽 월 10TB.
카드 등록은 본인 확인용이며 Free Tier 리소스만 쓰면 과금이 발생하지 않는다.
