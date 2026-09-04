# 롱폼 프로듀서 (Longform Producer)

## 역할

6~10분 분량의 수동 검토용 롱폼 영상을 만든다. 쇼츠 자동 공장과 분리되어 동작하며, 업로드는 검토 완료 후 별도 명령으로 진행한다.

## 입출력 계약

- 입력: `data/longform/{run_id}/script.json`
- 사전 검수 출력: `data/longform/{run_id}/media_board.json` + `media_contact_sheet.png`
- 최종 출력: `data/longform/{run_id}/output.mp4` + `produce_log.json`
- 실제 계약 구현: `app.models.validate_longform_script`
- 미디어 사전 검수 구현: `app.services.longform_media_preflight.prepare_longform_media_board`
- 실제 제작 구현: `app.agents.longform_producer.run_longform_producer`

## 제작 원칙

1. 쇼츠와 달리 검색·추천 유입을 고려해 제목, 썸네일 브리프, 오프닝 질문, 챕터 제목을 함께 기록한다.
2. 최종 영상은 먼저 파일로 만들고, 사람이 확인한 뒤 별도 업로드한다.
3. 대본은 `hook → context → evidence → mechanism → counterpoint → payoff → close` 구조를 포함한다.
4. `아주 오래전`, `엄청`, `수많은`, `큰 규모`처럼 기준이 흐린 표현은 사용하지 않는다.
5. 숫자·단위·연대는 출처가 있을 때만 쓰고, 불확실하면 “정확한 연대는 불확실하다”처럼 명확히 말한다.
6. 기존 AI 자산은 같은 실제 대상이면 먼저 재사용한다. 새로 생성된 AI 자산도 영구 라이브러리에 남겨 쇼츠에서 재사용 가능하게 한다.
7. 스타일은 제작 전에 PNG 미리보기로 확인하고 `documentary`, `cinematic`, `clean_news` 중 하나를 선택한다.
8. 롱폼은 대본 중심이 아니라 **미디어 우선(asset-first)** 으로 제작한다. 핵심 장면에 실제 대상과 맞는 A등급/Wikimedia·NASA·공식 자료 또는 C등급/검증 기준 이미지 기반 AI 자산이 없으면, 소재 각도를 수정하거나 검수 실패로 남긴다.
9. 6분 롱폼은 최소 20개 화면 장면으로 구성하고, 그중 최소 15개는 영상 소스 또는 AI 영상 소스를 우선 사용한다.
10. 정지 이미지는 지도·문서·근거 사진처럼 필요한 경우에만 사용하고, 흔들리는 줌·패닝으로 억지 영상화하지 않는다.
11. 무료 스톡은 분위기·전환용으로만 사용하고, 특정 실제 장소·구조·기록의 증거 화면처럼 제시하지 않는다.
12. 권장 실행 순서:
    - `python scripts/generate_longform.py --run-id {run_id} --prepare-media`
    - `python scripts/generate_longform.py --run-id {run_id} --materialize-media`
    - `python scripts/generate_longform.py --run-id {run_id} --preview-30s`
    - 확인 후 `python scripts/generate_longform.py --run-id {run_id}`

## 스타일 기본값

- 추천/기본값: `clean_news`
- 이유: 롱폼은 검색·추천 화면에서 신뢰감이 먼저 필요하므로, 뉴스 해설형 레이아웃으로 클릭 후 이탈을 줄인다.
