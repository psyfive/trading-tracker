# 예시 리포트

프론트엔드 개발용 `DiagnosisReport` JSON.

**손으로 채운 목업이 아니라 실제 전략 출력이다.** `scripts/make_examples.py`가 고정 픽스처
(`tests/fixtures/`)로 컨텍스트를 만들고 구현된 전략 3종을 그대로 돌려 생성한다.
따라서 값은 실측이지만, 3년치 고정 픽스처 위의 과거 시점이므로 **투자 판단의 근거가 아니다.**

```bash
python scripts/make_examples.py          # 재생성
python scripts/make_examples.py --check  # 구현 출력과 일치하는지만 확인
```

전략을 고치면 이 파일들도 함께 바뀌어야 한다. `tests/test_examples.py`가 같은 (티커, as_of)를
다시 평가해 파일과 대조하므로, 재생성을 잊으면 테스트가 실패하며 재생성하라고 알려 준다.

Phase 0~3의 손 목업은 두 Phase 연속으로 구현을 따라가지 못했고(존재하지 않는 게이트 체크 id,
구현이 금지한 게이트 구성, 발생 불가능한 시나리오), 스키마 검증은 그것을 잡지 못했다.
지금 구조에서는 그 드리프트가 구조적으로 불가능하다.

| 파일 | 보여주는 상태 |
|---|---|
| `sample_buy.json` | 세 전략이 모두 BUY (`UNANIMOUS_BUY`). 만점 척도·베이스 구조가 전략마다 다르다 |
| `sample_gate_reject.json` | 한 조건만 모자란 탈락(`score: null`)과 통과가 같은 종목에 공존 — 자가 다르다 |
| `sample_incomplete_bar.json` | `sample_buy.json`과 **같은 봉**, 다만 장중이라 봉이 미완성 — 전원 WATCH |

앞의 두 파일은 픽스처를 훑어 시나리오에 맞는 시점을 **찾아서** 만든 것이다. 조건에 맞는
시점이 없으면 스크립트는 파일을 쓰지 않고 실패한다 (없는 상태를 지어내지 않는다).

## UI가 반드시 구분해서 그려야 하는 것

- `status: "UNAVAILABLE"` 은 `"FAIL"` 과 다른 색으로. 데이터 부족이지 조건 미달이 아니다.
  이때 `threshold`가 없을 수도 있다 — 기준값 자체가 파생값(이동평균)이라 아직 없는 경우다.
  `null`을 0으로 대체해 "기준 > 0.00"을 그리지 말 것.
- `score: null` 은 0점이 아니라 **채점하지 않았다**는 뜻이다. 막대 0%로 그리면 안 된다.
- `max_score`는 전략마다, 그리고 같은 전략이라도 시점마다 다르다. 채점할 수 없는 항목을
  0점 처리하지 않고 만점에서 빼기 때문이다. 항상 `earned / max_score` 비율로 그릴 것이며,
  서로 다른 척도이므로 전략 간 점수를 나란히 비교하거나 평균내지 말 것.
- `sample_incomplete_bar.json`은 `sample_buy.json`과 같은 봉인데 판정이 전부 WATCH다.
  거래량 채점 항목이 만점에서 빠졌고, 돌파 거래량을 확인할 수 없어 BUY를 보류했다.
  차이가 나는 이유는 각 판정의 `notes`에 문장으로 들어 있다.
- `setup_metrics.detail`은 `kind`로 분기한다. 모르는 `kind`는 무시하면 된다.
  같은 차트에서 세 전략의 `base_length_days`가 서로 다르다는 점을 확인할 것 —
  피벗·베이스는 지표가 아니라 **방법론별 해석**이다.
- `risk_plans`는 **전략명을 키로 하는 맵**이다. 세 전략의 피벗이 다르면 진입가·손절가도
  다르므로 플랜도 전략마다 따로 나온다. 진입 의사가 있는 판정(BUY/WATCH)에만 실리므로
  키가 `strategy_verdicts`보다 적을 수 있고, 하나도 없으면 빈 객체다.
  예시는 계좌 10만을 가정해 주수까지 채웠다 — 계좌를 모르면 `shares`/`position_value`/
  `risk_amount`가 전부 `null`이고, 그때 0으로 그리면 안 된다.
- `warnings`의 `RS_UNIVERSE_MISSING`(생존편향)은 RS 백분위를 실은 모든 리포트에 붙는다.
  숫자 옆에 함께 표시할 것.
