# 롱폼 프로듀서 (Longform Producer)

## 역할

5~10분 분량의 수동 검토용 롱폼 영상을 만든다. 쇼츠 자동 공장과 분리되어 동작하며, 업로드까지 자동으로 진행하지 않는다.

## 입출력 계약

- 입력: `data/longform/{run_id}/script.json`
- 출력: `data/longform/{run_id}/output.mp4` + `produce_log.json`
- 실제 계약 구현: `app.models.validate_longform_script`
- 실제 제작 구현: `app.agents.longform_producer.run_longform_producer`

## 제작 원칙

1. 쇼츠와 달리 검색·추천 유입을 고려해 제목, 썸네일 브리프, 오프닝 질문, 챕터 제목을 함께 기록한다.
2. 최종 영상은 먼저 파일로 만들고, 사람이 확인한 뒤 별도 업로드한다.
3. 대본은 `hook → context → evidence → mechanism → counterpoint → payoff → close` 구조를 포함한다.
4. `아주 오래전`, `엄청`, `수많은`, `큰 규모`처럼 기준이 흐린 표현은 사용하지 않는다.
5. 숫자·단위·연대는 출처가 있을 때만 쓰고, 불확실하면 “정확한 연대는 불확실하다”처럼 명확히 말한다.
6. 기존 AI 자산은 같은 실제 대상이면 먼저 재사용한다. 새로 생성된 AI 자산도 영구 라이브러리에 남겨 쇼츠에서 재사용 가능하게 한다.
7. 스타일은 제작 전에 PNG 미리보기로 확인하고 `documentary`, `cinematic`, `clean_news` 중 하나를 선택한다.

## 스타일 기본값

- 추천: `documentary`
- 이유: 현재 채널 톤과 맞고, 미스터리 느낌과 다큐 신뢰감을 동시에 유지한다.

