# Phase 3–4 비판적 리뷰

리뷰 범위: `96e8ef6`(Phase 3: 미너비니 + 스키마 1.1.0 + 시점별 주입), `7bb7f2a`(Phase 3.5:
유니버스 RS + 패널 + 지표 사전 계산, Phase 4의 전제이므로 포함), `c86c87c`(Phase 4:
와인스타인 + Qullamaggie + 스키마 1.2.0). 2026-08-19 기준, 436 passed / ruff clean
상태에서 리뷰했다. Phase 0–2 리뷰(docs/review_phase0-2.md)의 지적이 반영됐는지 추적을 포함한다.

## 요약 — 심각도별 목록

| 심각도 | # | 한 줄 요약 |
|---|---|---|
| BLOCKER | B1 | 돌파 매매 시스템 3종 중 **어느 전략도 돌파 거래량을 진입 조건으로 요구하지 않는다**. 미너비니의 `max_base_depth_pct`·`breakout_volume_ratio`는 죽은 config다 |
| BLOCKER | B2 | examples의 와인스타인·Qullamaggie 게이트가 Phase 0 목업 그대로다 — Phase 4가 금지·테스트 잠금한 바로 그 설계(중복 조건 3중 계산)를 프론트 참조 문서가 여전히 보여준다 |
| EXPENSIVE-LATER | E1 | 미너비니 UNAVAILABLE 체크 4개가 `threshold: 0.0`을 날조해 내보낸다 — "계산 불가를 0.0으로 채우지 말 것" 위반 |
| EXPENSIVE-LATER | E2 | 와인스타인 평가가 매 호출 전체 히스토리로 SMA150을 재계산한다 — Phase 3.5의 O(n) 최적화가 이 전략에는 무효 |
| EXPENSIVE-LATER | E3 | (이월) `Comparator.BETWEEN`은 스키마가 두 번 오르는 동안 여전히 단일 threshold로 표현 불가 |
| MINOR | M1~M9 | 아래 각 절 참조 |

이전 리뷰(Phase 0–2)의 BLOCKER 4건(B1~B4)과 E1/E2/E3/E5/E6은 실제로 반영됐음을 코드로
확인했다 (verdict 기준 진입 + 3집단 집계, `*_by_date` 주입, `build_gate_result` 정책,
consensus validator 전면 잠금, SetupDetail 판별 유니온, `shortfall_pct`, 전체 판정 비교
감사, 지표 프레임). 이월된 것: E3(BETWEEN), E8(워치리스트/다종목 프론트 계약 — 패널은
백테스트 쪽에만 생겼다), M2(전략 에러의 리포트 표현 — `main.py` 파이프라인 구현 전에 결정 필요).

---

## Phase 3 — 미너비니 + regime/RS 시점별 주입

### [BLOCKER] B1 (미너비니 부분). 게이트·채점 어디에도 없는 방법론 핵심 조건 2개, config는 있다

`MinerviniConfig`는 임계값을 약속하는데 구현이 사용하지 않는다:

- **`max_base_depth_pct = 35.0` (config.py:164) — 참조 0곳.** `detect_base()`는
  `depth_pct`를 계산해 담기만 하고([minervini.py:169](strategies/minervini.py:169)) 어떤
  판정에도 쓰지 않는다. 깊이 50% 베이스도 유효한 베이스로 잡혀 PIVOT_READY까지 가고,
  점수·게이트 어디에서도 걸러지지 않는다. 미너비니 원전에서 베이스 깊이 상한은 실패
  확률을 거르는 핵심 기준이다 — '베이스 성숙도' 항목의 detail 문자열에 깊이가 표시만
  된다([minervini.py:488](strategies/minervini.py:488)).
- **`breakout_volume_ratio = 1.5` (config.py:169) — 참조 0곳.** `decide()`는 BREAKOUT
  상태에서 `score_pct >= 60`과 `volume_reliable`만 보고 BUY를 낸다
  ([minervini.py:609-615](strategies/minervini.py:609)). 채점에 있는 거래량 항목은
  **건조(dryup)**뿐이라, 돌파일에 거래량이 실렸는지는 게이트·점수·decide 어디에서도
  확인하지 않는다. 정작 `examples/sample_buy.json:196`의 노트는 "돌파 거래량은 50일
  평균의 1.5배 이상을 확인할 것"이라고 말한다 — 시스템이 사용자에게 하라고 말하는
  확인을 시스템 자신은 하지 않는다.

"모든 임계값은 config" 원칙의 역방향 위반이다: config에 있는 임계값은 코드 어딘가가
소비한다는 약속인데, 스윕 대상 파라미터 2개가 무엇을 돌려도 결과가 같다.
Phase 4 두 전략과 묶어 아래 Phase 4 절의 B1(공통부)도 볼 것.

### [EXPENSIVE-LATER] E1. UNAVAILABLE 체크의 threshold=0.0 날조

`numeric()` 헬퍼에 넘기는 threshold 인자가 `... if have else 0.0` 패턴으로 계산된다:

- [minervini.py:247](strategies/minervini.py:247) `max(ind.sma150, ind.sma200) if have else 0.0`
- [minervini.py:273](strategies/minervini.py:273), [311](strategies/minervini.py:311),
  [337](strategies/minervini.py:337) 동일 패턴

이 0.0이 UNAVAILABLE 분기의 `build_gate_check(threshold=threshold)`로 그대로 흘러
([minervini.py:217-226](strategies/minervini.py:217)), JSON에 `"threshold": 0.0,
"comparator": "GT"`가 실린다. 프론트는 comparator+threshold로 문구를 조립하므로
"기준 > $0.00"을 그리게 된다. **"계산 불가를 `0.0`으로 채우지 말 것" 목록의 문구
그대로이며**, 같은 상황에서 threshold를 아예 싣지 않는 와인스타인
([weinstein.py:214-222](strategies/weinstein.py:214))과도 비일관이다. 기준값이 config
상수인 체크(기울기·52주·RS)는 UNAVAILABLE이어도 threshold가 의미 있으므로 그대로 두고,
기준값이 파생값(이동평균)인 체크만 None으로 비우면 된다.
`test_short_history_yields_unavailable_not_fail`(tests/test_strategies_minervini.py:155)이
status까지만 보고 threshold는 안 보는 테스트 갭이 이를 놓쳤다.

### regime/market.py, 주입 경로 — 대체로 문제 없음, 의미 함정 하나

시점 정합성 설계(전 함수 rolling/shift 기반 + `*_series()` 시점별 산출 + 별도 테스트 잠금)는
구조가 맞다. 발견한 문제:

- **[MINOR] M5. Stage 2x2의 라벨 함정** — [market.py:127-134](regime/market.py:127):
  주가가 **상승 중인** 150일선을 하루 하회하면 (below MA + slope > -flat) →
  `STAGE_1`('바닥 다지기')이 된다. Stage 2 진행 중의 눌림목이 '바닥'으로 분류되고,
  와인스타인 게이트는 이 값으로 즉시 탈락시킨다. 판정 방향은 보수적이라 손실은 없지만
  (a) 화면에 "바닥 다지기"로 표기되는 것은 오진이고 (b) 일봉 근사라 주봉 기반 원전보다
  훨씬 자주 깜빡인다. 최소한 enum 주석 또는 UI 문구에서 'MA 아래 + MA 상승'이 STAGE_1로
  떨어진다는 한계를 명시할 것.

### 미너비니 셋업 탐지 — 설계 관찰

- **[MINOR] M7. '베이스'는 사실상 최근 65봉 구조다** — `base_lookback_days=65`
  (config.py:161) 창 안에서만 앵커를 찾으므로 수개월짜리 베이스는 표현할 수 없고,
  `len(df) >= 65`면 `detect_base()`가 항상 무언가를 반환한다(앵커 탐색이 실패하는 경로가
  구조적으로 없다). 따라서 NO_SETUP은 상장 65일 미만에서만 나오고, 강한 단조 상승
  구간에도 '길이 ~26일짜리 베이스'가 항상 잡힌다. 진단 결과가 틀렸다기보다 **어휘가
  과장된다** — "베이스 형성 중"이 "최근 눌림 구조 있음" 정도의 의미로 희석된다. 한계를
  모듈 docstring에 명시하거나, 최소 되돌림 깊이 같은 베이스 성립 조건을 추가할지 결정이
  필요하다 (B1의 max_base_depth_pct 미사용과 같은 뿌리다).

---

## Phase 3.5 — 유니버스 RS + 패널 (Phase 4의 전제라 포함)

### [MINOR] M2. 구성종목과 비구성종목의 백분위 규약이 다르다

- 구성종목: `scores.rank(axis=1, pct=True) * 100` ([universe.py:128](data/universe.py:128))
  — 자기 자신 포함 순위이므로 값 범위가 (100/n, 100]. 최하위가 0이 아니라 100/n이다.
- 비구성종목: `(row < score).sum() / len(row) * 100` ([universe.py:160](data/universe.py:160))
  — strictly-less 비율이므로 [0, 100]. 같은 순위라도 구성종목 쪽이 항상 ~100/n p 높다.

n=116이면 0.9p, KOSPI(50)면 2p 차이다. RS 70/80 같은 게이트 경계에 걸린 종목은 유니버스
소속 여부에 따라 판정이 뒤집힐 수 있다. 한쪽 규약(예: `(rank-1)/(n-1)` 또는 strictly-less)
으로 통일할 것.

### [MINOR] M3. evaluate_panel이 '시끄럽게 죽는다' 철학을 조용히 우회한다

[harness.py:688-689](backtest/harness.py:688) — 티커별 `InsufficientBacktestDataError`를
`continue`로 삼킨다. `InsufficientBacktestDataError`의 docstring 자체가 "조용히 빈 결과를
돌려주면 시그널 0건이 정상처럼 보인다. 시끄럽게 죽는다"인데, 패널 레벨에서는 같은 상황이
조용히 지나간다. `PanelResult.tickers`가 성공 종목 수만 담으므로 29종목 중 20종목이
스킵돼도 결과 객체만 봐서는 '원래 9종목 패널'과 구분되지 않는다. 스킵된 티커 목록을
`PanelResult`에 남길 것 (사유 문자열까지는 필요 없다).

### [MINOR] M4. rs_line_new_high는 어디에도 연결되지 않았다

`rs_line_new_high_series()`([universe.py:164](data/universe.py:164))는 구현과 docstring이
있지만 **호출부가 없다**. `IndicatorSnapshot.rs_line_new_high`(core/types.py:523)와
`build_context(rs_line_new_high=...)` 파라미터도 프로덕션 경로 어디에서도 채워지지 않아
항상 None이고, 전략 3종 중 사용처도 없다. 죽은 계약 필드 + 죽은 함수 조합이다. CANSLIM
(RS 라인 신고가가 실제로 쓰이는 방법론)에서 쓸 계획이면 그 Phase까지 함수에 명시적으로
'미연결' 표시를 하고, 아니면 "미확정 필드를 계약에 넣지 않는다" 원칙에 따라 정리 대상이다.

---

## Phase 4 — 와인스타인 + Qullamaggie + 프랙탈 스윙

### [BLOCKER] B1 (공통부). 돌파 거래량이 세 전략 어디에서도 진입의 필요조건이 아니다

세 전략 모두 '돌파를 산다'는 방법론이고, 세 방법론의 원전 모두 돌파 거래량 확인을
필수 조건으로 둔다. 현재 구현:

| 전략 | 돌파 거래량의 위치 | 결과 |
|---|---|---|
| minervini | **없음** (config만 존재, 죽은 값) | 저거래량 돌파에 BUY 가능 |
| weinstein | 채점 25점 항목 ([weinstein.py:337](strategies/weinstein.py:337)) | 거래량 0점이어도 나머지 75점으로 60% 기준 통과 → BUY 가능 |
| qullamaggie | **없음** (건조도만 채점) | 저거래량 돌파에 BUY 가능 |

와인스타인 `decide()`는 미완성 봉일 때 "와인스타인의 돌파는 거래량이 조건이다"라는
문구까지 출력하지만([weinstein.py:506](strategies/weinstein.py:506)), 완성 봉에서는 그
조건을 강제하지 않는다 — 거래량 비율 0.5배(평균의 절반)로 돌파해도 신선도·이격도·RS가
만점이면 81%로 BUY다. 문구와 로직이 어긋난다.

거래량 확인을 게이트에 넣을지(추세 조건이 아니므로 애매), BREAKOUT 상태에서의 decide
필요조건으로 넣을지는 설계 결정이지만, **지금처럼 '어디에도 없음'은 세 방법론 모두에서
잘못**이고, 셋을 나란히 두는 이 프로젝트에서 세 전략이 같은 방향으로 같은 결함을 공유하면
판정 비교 자체가 그 결함을 숨긴다. risk/render/main 파이프라인이 이 판정을 실사용자에게
보여주기 전에 고칠 것. (백테스트 재실행 필요 — 진입률 0.5%/8.7%/0.3%는 이 조건이 없는
상태의 수치다.)

### [BLOCKER] B2. examples가 Phase 4 설계와 정면으로 모순된다

Phase 3에서 examples를 스키마 1.1.0으로 마이그레이션할 때 **미너비니 판정만** 실제 구현에
맞춰졌고, 와인스타인·Qullamaggie 판정은 Phase 0의 상상 목업이 스키마만 통과하도록 남았다.
Phase 4에서 두 전략이 실제로 구현되면서 목업과 구현이 정면 충돌한다:

- **와인스타인 예시 게이트가 금지된 설계다**: `price_above_ma150` + `ma150_trending_up` +
  `stage2_confirmed`(examples/sample_buy.json:220, 231, 242) — Stage 2가 함의하는 사실을
  세 번 세는 구성. 정확히 이 구성을 CLAUDE.md가 금지하고
  `test_gate_does_not_recount_what_stage_2_already_implies`
  (tests/test_strategies_weinstein.py:114)가 잠근다. 실제 게이트는 5조건
  (stage_is_2 / price_above_ma10w / market_not_risk_off / rs_percentile / dollar_volume)
  으로 id·개수·내용이 전부 다르다. 프론트가 예시로 개발하면 `pass_count/total` 진행률
  의미부터 어긋난다.
- **Qullamaggie 예시의 체크 id 5개 중 4개가 구현에 없다**: `min_adr_pct`/`min_dollar_volume`
  (구현은 `adr_pct`/`dollar_volume`), `consolidation_length`, `breakout_volume_ratio`
  (examples/sample_incomplete_bar.json:214-258) — 뒤 둘은 구현 게이트에 존재하지 않는 조건.
  구현 게이트에 있는 `rs_percentile`/`price_above_ema21`은 예시에 없다. 심지어 이 예시의
  핵심 서사("당일 봉 미완성 → 돌파 거래량 UNAVAILABLE → 4/5 탈락")는 **현재 구현에서
  발생할 수 없다** — 구현된 5조건 중 당일 봉 거래량에 의존하는 조건이 없다.
- **`tests/test_examples.py:199`가 죽은 id를 잠근다**: `unavailable_checks == ["breakout_volume_ratio"]`
  — 테스트가 낡은 목업을 회귀 보호하고 있어, 목업을 구현에 맞추려는 사람이 '테스트를
  고쳐야 하는' 역방향 마찰을 만난다.
- **스키마 1.2.0의 신규 타입 2종(WeinsteinSetup/QullamaggieSetup)의 예시가 없다** —
  `"kind"` grep 결과 세 파일 통틀어 minervini뿐. detail 판별 유니온을 프론트가 어떻게
  분기해야 하는지 보여주는 참조 샘플이 정작 새 타입에는 없다.
- 부수: 예시의 `strategy_version`은 전부 "0.1.0", 구현 3종은 전부 "1.0.0".

"계약을 바꾸면 examples가 먼저 깨진다"는 장치는 **형태**만 검증한다. 지금 상태는 '검증은
통과하지만 내용이 거짓'이며, 가장 싼 해법은 목업을 손으로 고치는 게 아니라 **구현 3종의
실제 출력으로 examples를 재생성하는 스크립트**를 만들어 드리프트 자체를 불가능하게 하는
것이다 (render/json_out.to_json이 이미 있다). render/프론트 Phase 전이 마지노선이다.

### [EXPENSIVE-LATER] E2. 와인스타인 평가는 여전히 O(전체 히스토리)다

`stage2_age_days()`가 매 호출 `sma(close, 150)` + `slope_pct`를 **전체 윈도우**에 대해
재계산한다([weinstein.py:90-92](strategies/weinstein.py:90)). evaluate 한 번에 두 번
불린다(build_score [352](strategies/weinstein.py:352), build_setup_metrics
[440](strategies/weinstein.py:440)). 백테스트 replay는 시점마다 evaluate를 부르므로
와인스타인 경로만 O(n²)가 재도입된다 — Phase 3.5가 "6ms → 0.18ms/봉"으로 만든 최적화가
이 전략에는 적용되지 않는다 (미너비니 detect_base는 65봉, Qullamaggie는 65+130봉으로
바운드되어 있어 괜찮다). `IndicatorFrame`에 sma150/slope **시계열**이 이미 있으므로
(indicators/snapshot.py:114-121) 마지막 값만 뽑는 현재 스냅샷 경로 외에 시계열 접근
경로를 열거나, age 계산을 `max_stage2_age_days`+slope_lookback 만큼의 tail로 바운드하면
된다(신선도 채점은 150일 이후를 구분하지 않으므로 정보 손실 없음). `sweep()` 구현 전이
싸다. 같은 맥락에서 evaluate 한 번에 `detect_range` 3회
([331](strategies/weinstein.py:331), [416](strategies/weinstein.py:416),
[438](strategies/weinstein.py:438)) / `detect_base` 3회 / `detect_consolidation` 3회
호출되는 중복도 있다 — 바운드돼 있어 급하지 않지만, 전략이 무상태여야 하는 제약 안에서
evaluate 단위로 탐지 결과를 전달할 구조(예: StrategyBase가 탐지 결과를 인자로 넘기는
템플릿 확장)를 고민할 가치가 있다.

### [MINOR] M1. Stage 판정과 신선도 계산의 config 이원화

게이트의 `stage_is_2`는 주입된 Stage(= `RegimeConfig.stage_ma_period=150`,
config.py:112 기반)를 쓰고, 같은 전략의 `stage2_age_days`는
`WeinsteinConfig.ma_period_daily=150`(config.py:195)을 쓴다. 지금은 둘 다 150이라
일치하지만 어느 한쪽만 replace()로 바꾸면 '화면의 Stage'와 '신선도가 세는 Stage 2'가
조용히 다른 선을 보게 된다. weinstein.py:26-31 docstring이 일치를 전제로 설명하지만
강제 장치(공유 필드 또는 assert)가 없다. 스윕 대상 파라미터이므로 드리프트 확률이 낮지 않다.

### [MINOR] M6. _decay 중복 정의 + 리터럴 하나

- `_decay()`가 [weinstein.py:157](strategies/weinstein.py:157)과
  [qullamaggie.py:136](strategies/qullamaggie.py:136)에 동일 코드로 두 번 있다. 전략끼리
  import 금지 규칙 때문에 복사된 것인데, 채점 정규화 헬퍼는 `strategies/base.py`로 올리면
  규칙 위반 없이 공유된다 (`_scaled`도 미너비니 전용으로 남아 있어 세 전략의 정규화
  어휘가 2종으로 갈라져 있다).
- [qullamaggie.py:395](strategies/qullamaggie.py:395) `_decay(dryup, cfg.ideal_volume_dryup_ratio, 1.0)`
  — worst 경계 `1.0`이 리터럴이다. '컨솔 평균과 같으면 0점'이라는 판정 기준이므로
  파라미터이지 항등원이 아니다. config로 올릴 것 (미너비니의 `_scaled` 내부 1.0은 정규화
  항등원이라 예외).

### [MINOR] M8. 표기 어긋남

[qullamaggie.py:282, 293](strategies/qullamaggie.py:282) — EMA21을 라벨에서 "20일선"으로
부른다. 계약 필드는 `ema21`이고 reason에는 "EMA21"이 병기되지만, 라벨만 보는 화면에서는
20일 EMA로 오독된다. "주가 > EMA21"로 통일하거나 config에 기간을 두는 편이 맞다
(현재 ema21 사용은 IndicatorConfig.ema_periods=(10,21)에 하드 결합 — E7류 결합이지만
이미 알려진 패턴이므로 별도 항목으로 세지 않는다).

### [MINOR] M9. CI look-ahead 감사의 표본이 얇다

`tests/test_strategies_lookahead.py`는 AAPL 1종목 × 6시점 × 전략 3종이다
(AUDIT_CONFIG, tests/test_strategies_lookahead.py:34-42). 존재 자체가 중요한 안전망이고
비용 문제도 이해되지만, 미래 참조가 특정 셋업 국면(예: 돌파 직후)에서만 발생하는 유형이면
6시점 표본을 비켜간다. KOSPI 픽스처 1종목을 추가해 시장·통화 스케일이 다른 경로도 태우면
비용 대비 커버리지가 좋아진다.

### 문제 없음으로 확인한 것

- **프랙탈 스윙 지표** (indicators/core.py:201-232): '마지막 봉은 스윙 고점이 될 수 없다'
  성질이 rolling(center=True, min_periods=window)로 구조적으로 보장되고, 손계산 테스트와
  Phase 3의 함정(피벗=베이스 최고가) 회귀 테스트가 있다. 세 전략이 각자 k로 공유하는
  구성도 '계산식이 하나면 지표' 원칙에 부합한다.
- **게이트 축 분화 설계**: 세 전략의 게이트가 실제로 서로 다른 질문을 하고, 와인스타인
  테스트가 함의 중복 금지를 잠근다. 검증 스크립트의 주입 구성(verify_phase4.py:126-138)도
  시장별 regime/stage/RS를 올바르게 물린다.
- **스키마 1.1.0→1.2.0 진화 방식**: SetupDetail 판별 유니온에 변형을 덧붙이는 additive
  경로, shortfall_pct의 단일 계산 지점, 미구현 전략(CANSLIM) 타입을 미리 만들지 않은 것
  모두 이전 리뷰의 의도대로다.
- **하네스 통합**: 지표 프레임의 시점 동치성이 테스트와 감사 양쪽에서 교차 확인되고,
  패널 풀링이 요약 평균이 아니라 원본 Outcome 병합인 것도 맞다.

---

## 종합

Phase 3–4의 골격 — 게이트 축 분화, 스키마 진화, 시점별 주입, 프랙탈 스윙 공유 — 은
이전 리뷰의 처방을 충실히 따랐고 구조적으로 건전하다. 남은 문제는 두 종류다:

1. **방법론 충실도**: 돌파 거래량 부재(B1)와 베이스 깊이 미검증은 '진단 시스템이 원전
   방법론을 대변한다'는 전제를 깬다. 지금 수치(진입률·초과수익)로 전략을 논하기 전에
   고쳐야 재측정 비용이 한 번으로 끝난다.
2. **참조 문서의 진실성**: examples 드리프트(B2)는 코드가 아니라 프로세스 문제다 —
   손으로 만드는 목업은 구현 속도를 따라가지 못한다는 것이 두 Phase에 걸쳐 증명됐으므로,
   실제 출력으로 재생성하는 자동화가 답이다.

---

## 조치 내역 (2026-08-19)

리뷰 항목을 코드에 반영했다. 테스트 482 passed / 3 skipped, ruff clean.
전략 3종의 `strategy_version`은 전부 1.1.0으로, `SCHEMA_VERSION`은 1.3.0으로 올렸다.

| 항목 | 조치 | 근거 위치 |
|---|---|---|
| B1 | 돌파 거래량을 세 전략 `decide()`의 **이진 필요조건**으로. 계산은 `breakout_volume_ratio()` 하나로 공유하고 수치를 계약(`detail.breakout_volume_ratio`)에 실었다. 미너비니는 `max_base_depth_pct`도 BUY 필요조건으로 소비한다 | `strategies/base.py`, 전략 3종 `_breakout_volume_note()` |
| B2 | examples를 **실제 전략 출력으로 생성**한다. 조립은 `core/report.py`(컨센서스 파생의 단일 지점), 생성은 `scripts/make_examples.py`. 테스트가 같은 (티커, as_of)를 재평가해 파일과 대조하므로 드리프트가 구조적으로 불가능하다 | `scripts/make_examples.py`, `tests/test_examples.py` |
| E1 | UNAVAILABLE 체크에서 **파생 threshold는 None**. 기준값이 config 상수인 체크는 그대로 싣는다 | `strategies/minervini.py` `numeric()` |
| E2 | `stage2_age_days`를 상수 창(`ma_period + slope_lookback + max_stage2_age_days`)으로 바운드하고 상한에서 잘랐다. 탐지 중복 호출은 `@memoize_per_context`로 제거 — 캐시 키가 **ctx 신원**이라 감사의 두 패스는 캐시를 공유하지 않는다 | `strategies/weinstein.py`, `strategies/base.py` |
| E3 | 이월. `Comparator.BETWEEN`은 여전히 단일 threshold로 표현 불가 | — |
| M1 | `WeinsteinConfig`의 Stage 파라미터를 `RegimeConfig`와 일치시키고(`slope_min_pct` -> `stage_flat_slope_pct`) `AppConfig.__post_init__`이 드리프트를 즉시 죽인다. 부수 효과로 신선도가 세는 조건이 Stage 2 판정과 **같은 식**이 됐다 (이전에는 0.0 vs 0.5로 어긋나 있었다) | `config.py` |
| M2 | 백분위 규약을 strictly-less로 통일 (구성종목 경로의 `rank(pct=True)` 제거) | `data/universe.py` |
| M3 | `PanelResult.skipped_tickers` 추가 + verify 스크립트가 표시 | `backtest/harness.py` |
| M4 | `rs_line_new_high`를 '미연결·연결 시점 지정'으로 계약과 함수 양쪽에 명시 | `core/types.py`, `data/universe.py` |
| M5 | STAGE_1이 '상승 중 MA를 하회한 눌림목'까지 삼킨다는 사실을 enum과 판정 함수에 명시 | `core/types.py`, `regime/market.py` |
| M6 | `_decay`/`_scaled`를 `strategies/base.py`로 승격(`decay_score`/`scaled_score`), Qullamaggie 건조도 worst를 `max_volume_dryup_ratio`로 | `strategies/base.py`, `config.py` |
| M7 | 베이스 탐지 창 65봉의 한계를 모듈 docstring에 명시 | `strategies/minervini.py` |
| M8 | EMA21 라벨에서 '20일선' 표기 제거 | `strategies/qullamaggie.py` |
| M9 | look-ahead 감사에 KOSPI 픽스처 추가 (2종목 × 3전략) | `tests/test_strategies_lookahead.py` |

### 남은 문제 — 조치하지 않았고, 결정이 필요하다

**BUY의 대부분은 여전히 돌파 전(PIVOT_READY)이다.** 표본 6종목에서 세어 보면
minervini 4:1, weinstein 21:3, qullamaggie 7:4로 PIVOT_READY가 BREAKOUT보다 많다.
B1의 조치는 '돌파했는데 거래량이 없는' 경우만 거른다 — 피벗 3% 이내에서 미리 사는
진입에는 확인할 돌파 자체가 없기 때문이다.

세 방법론의 원전은 모두 **돌파를 확인하고 산다**. 그 해석을 그대로 따르면 PIVOT_READY는
BUY가 아니라 WATCH여야 하고, 그것은 진입 정의 자체를 바꾸는 결정이라 리뷰 범위 밖으로
두었다. 바꾸기로 하면 세 전략의 `decide()`에서 PIVOT_READY 분기를 WATCH로 내리고
백테스트 수치를 전부 다시 재야 한다.

### 재측정 결과 (`us_large`, 표본 3종목 / 풀링 29종목)

| 전략 | 게이트 통과율 | 진입률 (전) | 진입률 (후) | 20봉 초과수익 (후) |
|---|---|---|---|---|
| minervini | 5.4% | 0.5% | **0.2%** | +1.61%p (n=166) |
| weinstein | 16.1% | 8.7% | **5.0%** | -0.76%p (n=826) |
| qullamaggie | 1.0% | 0.3% | **0.3%** | -1.05%p (n=111) |

초과수익은 전부 2SE 구간 안이다. 생존편향·기간편향도 그대로이므로 이 수치로
전략 우열을 말할 수 없다는 결론은 바뀌지 않는다.

---

## Phase 5에서 정리된 이월 항목 (2026-08-20)

| 항목 | 처리 |
|---|---|
| M2 (전략 에러의 리포트 표현) | **결정·구현.** 전략 하나가 예외를 내면 그 전략만 판정 목록에서 빠지고 `STRATEGY_ERROR` 경고가 실린다 (컨센서스 분모도 줄어든다). 빠진 전략을 `REJECTED_BY_GATE`로 채우면 '그 방법론이 거절했다'는 거짓이 되므로 채우지 않는다 |
| `risk_plan` 단수 필드 | **전략별로 분리.** `risk_plans: dict[전략명, RiskPlan]`(스키마 1.4.0). 하나로 두면 '어느 방법론의 진입가를 쓸 것인가'라는 선택을 조립 코드가 하게 된다. 플랜이 붙을 수 있는 판정(BUY/WATCH)은 validator가 강제한다 |
| E3 (BETWEEN용 threshold_high) | 이월. 아직 `Comparator.BETWEEN`을 쓰는 전략이 없다 |
| E8 (워치리스트/다종목 프론트 계약) | 이월. 단일 티커 진단 경로가 먼저 필요했고, 이제 그 경로가 생겼으므로 다음 Phase의 대상이다 |

### Phase 5에서 드러난 것

**RS 유니버스 수집이 파이프라인의 선행 조건이었다.** `data/universe.py`에는 종가를
계산하는 함수만 있고 **수집 경로가 없었다** — 픽스처 생성 스크립트만이 그 행렬을
만들었다. 세 전략 모두 게이트에 RS 조건이 있고 UNAVAILABLE이 AND 게이트를 막으므로,
그대로 CLI를 붙였다면 어떤 종목을 진단해도 전부 `REJECTED_BY_GATE`가 나오고 그 화면은
'이 종목이 나쁘다'와 구분되지 않았을 것이다. `load_universe_closes()`(구성종목 수집 +
하루 단위 캐시 + 누락 종목 보고)를 추가하고, 실패 시 CRITICAL 경고로 '종목의 문제가
아니다'를 남기게 했다. `scripts/verify_phase5.py`가 이 시나리오를 재현해 확인한다.
