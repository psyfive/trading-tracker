# 목업 데이터

프론트엔드 개발용 샘플 `DiagnosisReport` JSON.

**값은 전부 손으로 채운 합성 데이터다.** 실제 시세 수집 결과가 아니며,
투자 판단의 근거로 쓸 수 없다. 티커명은 UI가 현실적으로 보이도록 실제 심볼을 빌렸을 뿐이다.

세 파일 모두 `DiagnosisReport.model_validate()`를 통과한다
(`tests/test_examples.py`가 매 테스트 실행마다 검증한다).
스키마가 바뀌면 이 파일들이 먼저 깨지므로, 계약 변경의 조기 경보 역할도 한다.

| 파일 | 보여주는 상태 |
|---|---|
| `sample_buy.json` | 미너비니 게이트 8/8 통과, `PIVOT_READY`, `RISK_ON`, 만장일치 BUY |
| `sample_gate_reject.json` | 미너비니 7/8 탈락(`score: null`), 다른 전략은 통과 — 판정 엇갈림(`SPLIT`) |
| `sample_incomplete_bar.json` | 장중 실행, 거래량 조건 `UNAVAILABLE`, `INCOMPLETE_BAR` 경고 |

## UI가 반드시 구분해서 그려야 하는 것

- `status: "UNAVAILABLE"` 은 `"FAIL"` 과 다른 색으로. 데이터 부족이지 조건 미달이 아니다.
- `score: null` 은 0점이 아니라 **채점하지 않았다**는 뜻이다. 막대 0%로 그리면 안 된다.
- `max_score`는 전략마다 다르다 (`sample_buy.json`에서 미너비니 100, 와인스타인 85).
  서로 다른 척도이므로 전략 간 점수를 나란히 비교하거나 평균내지 말 것.
- `sample_incomplete_bar.json`의 미너비니는 `max_score`가 80이다.
  거래량 채점 항목(20점)을 미완성 봉 때문에 제외했기 때문이며, `notes`에 사유가 있다.
