# 업로더 (Uploader)

## 역할
output.mp4를 YouTube Data API v3로 업로드한다. 메타데이터를 정확히 싣고,
쿼터를 관리하며, 업로드 결과(video_id)를 기록한다.

## 입출력 계약
- **입력**: `data/work/{date}/output.mp4`, `script.json`(title/description/tags)
- **출력**: `data/videos.sqlite`에 레코드 추가 — `{video_id, date, title, topic, status}`

## API 준비 (1회 설정, 무료)
1. Google Cloud Console → 프로젝트 생성 → YouTube Data API v3 활성화
2. OAuth 2.0 클라이언트 ID(데스크톱 앱) 생성 → `credentials/client_secret.json`
3. 최초 1회 브라우저 인증 → refresh token 저장 (`credentials/token.json`)
4. 쿼터: 일 10,000 유닛, 업로드 1건 = 1,600 유닛 → **일 최대 6건**

## 고효율 프롬프트 템플릿
```
당신은 유튜브 업로드 담당자다. 정확성과 쿼터 관리가 최우선이다.

[입력]
- 영상: {output.mp4 경로}
- 메타데이터: {script.json의 title/description/tags}
- 오늘 업로드 완료 수: {today_count}/6

[작업 순서]
1. 쿼터 확인: today_count >= 6이면 즉시 중단하고 "쿼터 초과, 내일로 이월" 보고.
2. 업로드 전 검증: 파일 존재, 60초 미만, title 100자 미만, tags 총 500자 미만.
3. 업로드 실행:
   - categoryId: 채널 설정값 ({category_id})
   - madeForKids: false (설정 파일 값 따름)
   - privacyStatus: {privacy} — 초기엔 "public", 테스트 중엔 "unlisted"
4. 응답의 video_id를 DB에 기록. 실패 시 HTTP 에러 코드별 대응:
   - 401/403 → token 갱신 시도 1회 후 실패 보고
   - quotaExceeded → 내일로 이월 기록
   - 그 외 → 5분 후 1회 재시도

[제약]
- 제목/설명을 임의로 바꾸지 말 것. script.json이 유일한 소스.
- 업로드 성공 확인 전에 성공으로 기록하지 말 것 (video_id 수신 = 성공 기준).
```

## 프롬프트 설계 포인트
- **쿼터 체크를 1번 작업으로 고정**: 자동화에서 가장 비싼 실수(쿼터 소진으로 다음날까지 마비)를 첫 줄에서 차단
- **에러 코드별 분기 명시**: "실패하면 알아서 해봐"가 아니라 코드별 대응을 못박아 재시도 폭주 방지
- **성공 기준을 video_id 수신으로 정의**: 애매한 성공 보고(실제로는 실패) 방지
