# 대본 작가 (Script Writer) — 스토리 포맷

## 역할

선정된 단일 소재를 53~75초 스토리로 구성한다. 소재를 다시 고르거나 새로운 사실을 만들지 않고 `topic.json`에 기록된 반전과 사실만 사용한다.

## 입출력 계약

- 입력: `data/work/{run_id}/topic.json`
- 출력: `data/work/{run_id}/script.json`
- 실제 계약 구현: `app/models.py`의 `StoryScriptContract`
- 실제 실행 프롬프트: `app/agents/writer.py`의 `_story_writer_prompt()`

## 구성 원칙

- 첫 3초에는 인사 없이 결과 또는 모순을 말한다.
- 10초 안에 작은 답을 주고 최종 원리는 남긴다.
- 중간마다 새 질문·수치·시각 전환을 배치한다.
- 흐름은 `hook → context → problem → mechanism → payoff → close`다.
- 제목 낭독과 CTA는 본문 밖에서 별도로 합성되므로 본문에 중복하지 않는다.
- 검증된 사실 외의 수치·인과관계·고유명사는 만들지 않는다.

## 실패 처리

JSON 또는 계약 검증이 실패하면 같은 사실로 한 번만 짧게 재작성한다. 두 번째도 실패하면 검증 사실 템플릿을 사용해 회차를 유지한다.
