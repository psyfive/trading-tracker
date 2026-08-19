# Phase 0–2 비판적 리뷰 (Phase 3 착수 전)

리뷰 범위: 커밋 `7ecf92b`(Phase 0) ~ `c9c1be3`(Phase 2), 2026-08-19 기준.
테스트는 전부 통과한 상태에서 리뷰했다 (244 passed, 10 skipped — 네트워크 테스트 스킵).

## 요약 — 심각도별 목록

| 심각도 | # | 한 줄 요약 |
|---|---|---|
| BLOCKER | B1 | 하네스가 verdict를 무시하고 게이트 통과 = 진입으로 취급한다. WATCH/AVOID도 매수로 집계된다 |
| BLOCKER | B2 | 백테스트 컨텍스트의 stage=UNDEFINED / regime=상수 / rs_percentile=None — Phase 3 전략 3종이 게이트를 통과할 수 없다 |
| BLOCKER | B3 | 'UNAVAILABLE이 게이트를 막는가'가 미정의다. REJECTED_BY_GATE가 '조건 미달'과 '데이터 없음'을 구분하지 못한다 |
| BLOCKER | B4 | consensus의 중복 필드 대부분이 validator로 잠겨 있지 않다 — gate_progress는 빈 리스트면 검증을 통과하고, progress_ratio·verdict_counts·buy_strategies·agreement는 아예 검증 대상이 아니다 |
| EXPENSIVE-LATER | E1 | SetupMetrics/SetupState가 미너비니 VCP 어휘로 고정된 닫힌 스키마다. CANSLIM·Qullamaggie·와인스타인의 셋업 수치가 들어갈 자리가 없다 |
| EXPENSIVE-LATER | E2 | 게이트 탈락 시 detect_setup을 호출하지 않고 NO_SETUP으로 채운다 — '판정 안 함'을 '셋업 없음'으로 위장하는 자기 규칙 위반 |
| EXPENSIVE-LATER | E3 | '게이트 근접도'가 통과 개수뿐이다. 실패 조건의 미달 폭이 계약에 없어서, 제대로 정렬하려면 프론트가 임계값 비교를 재구현해야 한다 (원칙 4 위반 유도) |
| EXPENSIVE-LATER | E4 | Comparator.BETWEEN을 그릴 수 없다 — threshold가 단일 float이다 |
| EXPENSIVE-LATER | E5 | look-ahead 감사가 (verdict, gate.passed, score) 3개 필드만, 12개 시점만 비교한다 |
| EXPENSIVE-LATER | E6 | replay가 매 봉 전체 윈도우 지표를 재계산한다 — O(n²). Phase 3 파라미터 스윕에서 병목 확정 |
| EXPENSIVE-LATER | E7 | 전략 config와 IndicatorConfig가 조용히 결합되어 있다 (ma_period_daily=150 ↔ sma150 필드명) |
| EXPENSIVE-LATER | E8 | 워치리스트(다종목) 계약이 없다. 리포트 단건 ~10KB를 종목 수만큼 나르는 것 외의 경로가 없다 |
| MINOR | M1~M8 | 아래 각 절 참조 |

---

## 관점 1 — 프론트엔드 계약으로서의 `DiagnosisReport`

샘플 JSON 3개로 4계층 UI를 그리는 매핑을 실제로 수행한 결과:

| UI 요소 | JSON 필드 | 판정 |
|---|---|---|
| L0 시장국면 배너 | `regime` | 그릴 수 있음 (색만) |
| L1 종목 Stage | `stage` | 그릴 수 있음 |
| L2 게이트 체크리스트 "7/8" | `consensus.gate_progress[].pass_count/total` 또는 `strategy_verdicts[].gate` | 그릴 수 있음 |
| L2 실패 조건 + 수치 | `GateCheck.actual/threshold/comparator/unit/reason` | 대부분 가능. BETWEEN·BOOL은 결함 (E4, M6) |
| L3 셋업 상태머신 | `strategy_verdicts[].setup_state` | 게이트 통과 종목만 가능 (E2) |
| 워치리스트 근접도 정렬 | `consensus.gate_progress[].progress_ratio` | 개수 기준으로만 가능 (E3) |

중첩 순회 질문에 대한 답: **정렬 자체는 중첩 순회가 필요 없다.** `gate_progress`가 평평하게
올라와 있고 (`core/types.py:479`), 리포트당 전략 수만큼의 1단 루프로 끝난다. 문제는 정렬 키의
품질(E3)이지 접근 경로가 아니다.

### [BLOCKER] B4. consensus 중복 필드의 드리프트가 사실상 잠겨 있지 않다

`GateProgress`의 docstring은 "validator가 원본 GateResult와의 일치를 강제하므로 값이 어긋날
수 없다"고 주장한다 (`core/types.py:264`). 실제 validator는 그렇지 않다:

- `core/types.py:542` — `if self.consensus.gate_progress:` 로 시작한다. **gate_progress가 빈
  리스트면 검증 전체가 건너뛰어진다.** Phase 3에서 리포트 조립 코드가 이 필드를 채우는 것을
  잊으면 리포트는 유효하게 생성되고, 프론트 정렬만 조용히 죽는다.
- `core/types.py:543-557` — 비교 튜플이 `(passed, pass_count, total, unavailable_count)`뿐이다.
  **`progress_ratio`는 검증 대상이 아니다.** `pass_count/total`과 무관한 값을 넣어도 통과한다.
  정렬 키로 쓰라고 만든 바로 그 필드가 잠겨 있지 않다.
- `verdict_counts`, `buy_strategies`, `gate_passed_strategies`, `agreement`는 **어떤 validator도
  건드리지 않는다** (`core/types.py:536-562`에 해당 검사 없음). `verdict_counts={BUY: 3}`인데
  실제 BUY가 0개여도 유효한 리포트다. `tests/test_types_contract.py:421`은 total_strategies만
  잠근다.

지금 validator 몇 줄이면 끝난다. Phase 3에서 `diagnose()`가 이 조립을 구현한 뒤에 어긋나면
프론트 버그로 위장되어 발견 비용이 몇 배가 된다.

### [EXPENSIVE-LATER] E3. '근접도'가 개수라서, 진짜 랭킹은 프론트 재구현을 유도한다

`sample_gate_reject.json:348` — 미너비니 7/8 탈락, `progress_ratio: 0.875`. 어떤 종목이든
1개 실패는 전부 0.875다. JNJ는 RS 74 vs 기준 80으로 6p 차이지만, RS 30인 종목도 같은
0.875를 받는다. "게이트 근접도 순" 정렬이 이 둘을 구분하려면 프론트가
`GateCheck.actual/threshold/comparator`로 **미달 폭을 직접 계산**해야 하는데, 그 순간
comparator 방향 해석·스케일 정규화라는 판정 로직이 렌더러에 들어간다 — CLAUDE.md 원칙 4가
말하는 바로 그 누수다. 실패 체크에 정규화된 마진(예: `margin_pct`)을 계약에 추가하든,
GateProgress에 근접도 스칼라를 백엔드가 계산해 넣든, **계산 주체를 지금 정해야 한다.**
계약 변경이므로 나중일수록 SCHEMA_VERSION 비용이 붙는다.

### [EXPENSIVE-LATER] E4. Comparator.BETWEEN은 현재 계약으로 표현 불가

`core/types.py:133`에 `BETWEEN`이 선언되어 있지만 `GateCheck.threshold`는 단일
`float | None`이다 (`core/types.py:216`). 하한·상한 두 값이 필요한 비교를 담을 자리가 없다.
프론트가 "X ~ Y 사이" 문구를 조립할 수 없고, 사용하는 순간 reason 문자열 파싱으로 도망가게
된다. 쓰지 않을 거면 enum에서 빼고, 쓸 거면 `threshold_high` 등을 지금 추가할 것
(둘 다 SCHEMA_VERSION 변경이지만 지금이 가장 싸다).

### [EXPENSIVE-LATER] E8. 다종목 화면의 계약이 없다

`DiagnosisReport`는 단건 계약뿐이다. `sample_buy.json`은 377줄(~10KB)이고 대부분이 L2/L3
상세(checks의 한글 reason, components, risk_plan)다. 워치리스트 100종목이면 ~1MB를 나르고,
그중 정렬·요약에 필요한 것은 `ticker/price/regime/stage/gate_progress/agreement` 정도다.
리스트용 요약 계약(또는 리포트의 부분 집합 규약)이 없으면 프론트는 전체 리포트를 받아
버리는 구조가 되고, 나중에 요약 타입을 추가하면 두 계약의 일관성 문제가 새로 생긴다.

### [MINOR] M1. L0 배너에 '왜'가 없다

`regime`은 enum 하나다. CAUTION의 근거(분산일 수, 지수 vs 200일선)는 계약 어디에도 없다.
`RegimeConfig`(`config.py:95`)는 판정 입력을 갖고 있으므로 판정 근거를 리포트에 실을 자리
(예: RegimeDetail)가 없다는 것은 배너에 툴팁 하나 못 단다는 뜻이다. UI 요구가 배너
색상뿐이면 문제없다.

### [MINOR] M2. 전략 평가 실패가 리포트에 표현되지 않는다

`WarningCode.STRATEGY_ERROR`(`core/types.py:154`)는 있지만, validator가
`total_strategies == len(strategy_verdicts)`를 강제하므로 (`core/types.py:540`) 죽은 전략은
목록에서 그냥 빠진다. 프론트는 "4개 전략 중 canslim이 에러였다"를 warning.message 문자열
파싱 없이 알 수 없다. L2에 전략별 열을 고정 배치하는 UI라면 자리가 빈다.

### [MINOR] M3. L3 상태머신의 순서가 계약 밖이다

`SetupState`(`core/types.py:72`)는 값만 있고 진행 순서(NO_SETUP → … → EXTENDED)와
분기(FAILED_BREAKOUT)가 주석에만 존재한다. 프론트는 순서를 하드코딩해야 한다. 표현
문제라 판정 누수는 아니지만, 순서가 바뀌면 계약 변경 없이 UI가 어긋난다.

---

## 관점 2 — Phase 2 하네스 신뢰성

구조적 차단 자체는 실질적이다: `StockContext`에 미래 봉이 물리적으로 없고
(`backtest/harness.py:231`), `_assert_point_in_time`이 매 호출을 재확인하며
(`backtest/harness.py:188-202`), 하네스 자신의 슬라이싱 버그를 잡는 테스트도 있다
(`tests/test_harness.py:136`). 진입가 규칙(시그널 다음 봉 시가)도
`tests/test_harness.py:156`이 독립적으로 검증한다. 그 위에서 발견한 문제:

### [BLOCKER] B1. 하네스는 verdict를 버린다 — '게이트 통과 = 진입'

`backtest/harness.py:456`:

```python
(taken if signal.gate_passed else rejected).append(outcome)
```

집계가 `gate_passed`로만 갈린다. `replay`의 진입가 기록도 마찬가지다
(`backtest/harness.py:401-402` — `if gate_passed`). 더미 3종은 게이트 통과 = 무조건 BUY라서
이 결함이 보이지 않지만, Phase 3의 진짜 전략은 게이트를 통과하고도 WATCH/AVOID/HOLD를 낸다 —
`examples/sample_incomplete_bar.json:186`의 미너비니가 정확히 그 사례다 (8/8 통과, verdict
WATCH). 현재 하네스로 재면 "WATCH라서 안 산 날"이 전부 매수로 집계되어, **측정 대상이
'전략 성과'가 아니라 '게이트 성과'가 된다.** `Signal`에 verdict는 이미 있으므로
(`backtest/harness.py:85`) 집계 축을 verdict 기준(최소한 BUY만 taken)으로 바꾸거나,
게이트 성과와 verdict 성과를 별도 그룹으로 분리해야 한다. 더미 예측(verify_phase2)은
게이트=verdict라서 어느 쪽이든 그대로 성립한다.

### [BLOCKER] B2. 백테스트 컨텍스트가 Phase 3 전략의 게이트 입력을 못 준다

`_evaluate_at`이 만드는 컨텍스트는:

- `stage=Stage.UNDEFINED` 고정 (`backtest/harness.py:242`)
- `regime`은 호출 인자 하나로 전 기간 상수 (`backtest/harness.py:380`, 기본 CAUTION)
- `rs_percentile=None` (`core/context.py:76` 기본값, 유니버스는 Phase 3.5)

와인스타인 게이트는 Stage 2 확인이 핵심이고 (`examples/sample_buy.json:229` stage2_confirmed),
미너비니·CANSLIM 게이트는 RS 백분위가 필수다 (`config.py:133,157`). 이 값들이 백테스트에서
영원히 UNDEFINED/None이면 해당 GateCheck는 UNAVAILABLE이 되고, AND 게이트에서는 **전략이
단 한 번도 게이트를 통과하지 못한다.** 즉 Phase 3에서 전략을 구현해도 하네스로 잴 수 없다.
과거 시점별 stage/regime을 look-ahead 없이 재현해 주입하는 경로(지수 데이터의 시점별
슬라이스 포함)가 하네스에 필요하고, RS는 Phase 3.5 전까지 해당 체크를 어떻게 취급할지
B3의 정책 결정과 묶인다. 이것을 Phase 3 중간에 발견하면 하네스와 전략을 오가며 고치게 된다.

### [BLOCKER] B3. UNAVAILABLE의 게이트 정책이 미정의다

CLAUDE.md는 표시 레벨("FAIL과 구분해 보여라")만 정하고, **통과 판정에서 UNAVAILABLE을
어떻게 취급하는지는 어디에도 정의가 없다.** 현재 유일한 구현은 더미의 헬퍼로,
`strategies/dummy.py:38`이 `passed = pass_count == len(checks)` — UNAVAILABLE을 사실상
탈락으로 취급한다. `GateResult.required_count`(`core/types.py:235`)가 있지만 UNAVAILABLE이
분모에 드는지 빠지는지 규약이 없다. 결과적으로:

- `examples/sample_incomplete_bar.json:270` — Qullamaggie 4 PASS + 1 UNAVAILABLE이
  `REJECTED_BY_GATE`다. notes에 "조건 미달로 탈락한 것이 아니다"라고 쓰지만 **verdict enum은
  그 구분을 표현하지 못한다.** 프론트·백테스트 집계 양쪽에서 '데이터 없어 보류'가
  '조건 미달 탈락'과 같은 값으로 합쳐진다 — "UNAVAILABLE을 FAIL로 둔갑시키지 말 것"이라는
  자기 규칙이 verdict 레벨에서 깨진다.
- B2와 결합하면: 백테스트에서 RS UNAVAILABLE → 전량 REJECTED_BY_GATE → 시그널 0건이
  '정상 결과'처럼 보인다.

전략 4종을 구현하기 전에 규약(예: UNAVAILABLE은 보류 verdict를 만든다, 또는 required_count
분모에서 제외한다)을 base 레이어에 박아야 한다. 전략별로 제각기 처리하면 되돌리기 비싸다.

### [EXPENSIVE-LATER] E5. 감사의 그물눈이 성기다

`audit_lookahead`의 비교 대상은 `(verdict, gate.passed, score)` 3개뿐이다
(`backtest/harness.py:283-287`). `setup_state`, `setup_metrics`, `components`, `notes`로만
새는 미래 참조는 적발되지 않는다. 지금은 성과 집계가 그 3개만 쓰므로 측정치는 오염되지
않지만, Phase 3에서 setup_state가 프론트 L3와 리포트에 실리는 순간 '감사는 clean인데 화면은
미래를 반영'하는 조합이 가능해진다. 또한:

- 검사 시점이 `lookahead_audit_samples: 12`개다 (`config.py:190`). 조건부로만 미래를 쓰는
  전략(예: 특정 국면에서만)은 12개 표본을 비켜갈 수 있다. 감사는 성실성 검사이지 증명이
  아니라는 한계를 결과 객체에 명시하든, 시점 수를 평가봉 대비 비율로 올리든 해야 한다.
- 감사는 전략이 무상태라는 가정 위에 있다. `evaluate_results`는 replay를 먼저 돌리고 같은
  인스턴스로 감사를 돌리므로 (`backtest/harness.py:425-428`), 호출 간 상태를 쌓는 전략은
  A/B 평가가 같은 오염 상태를 공유해 감사가 무력화된다. RandomStrategy의 날짜 고정 시드가
  이 문제를 우회한 것인데 (`strategies/dummy.py:99-101` docstring), 그 요구사항이 더미의
  주석에만 있고 Strategy Protocol/StrategyBase 문서에는 없다.

### [EXPENSIVE-LATER] E6. replay는 O(n²)이고 Phase 3 스윕에서 병목이 된다

`_evaluate_at` → `build_context` → `build_indicator_snapshot`이 매 평가 시점마다 **윈도우
전체에 대해 지표 15종을 처음부터 재계산한다** (`backtest/harness.py:236-243`,
`indicators/snapshot.py:49`). 750봉 픽스처 하나에 550회 × O(n) 계산이고, 감사가 시점당 2회
평가를 또 얹는다. `sweep`(`backtest/harness.py:474`)이 붙는 순간 설정 변형 수만큼
곱해진다. 전체 시계열 지표를 한 번 계산해 두고 시점별로 잘라 쓰는 구조(지표 사전 계산 +
스냅샷만 시점별 추출)로 바꾸는 것은 지금은 하네스 내부 리팩터링이지만, 전략 4종이 각자
`ctx.ohlcv` 기반 셋업 탐지(베이스/피벗 스캔)를 얹은 뒤에는 손대는 범위가 커진다.

### [MINOR] M4. 예측 1(AlwaysBuy ≡ 벤치마크)은 동어반복이다

벤치마크는 시그널과 **같은 `forward_outcome` 호출 결과에서** 만들어진다
(`backtest/harness.py:446-456`). AlwaysBuy는 전 봉 게이트 통과이므로 taken과 benchmark가
동일 리스트가 되고, `scripts/verify_phase2.py:206`의 `abs(excess) < 1e-9`는 구성상 항상
참이다. 이 예측은 진입 정렬을 검증하지 않는다 — 그것을 실제로 잡는 것은 별도의 단위 테스트
(`tests/test_harness.py:156`, `:182`)다. verify_phase2가 "하네스는 자로서 신뢰할 수 있다"고
선언할 때 예측 1의 기여분은 '게이트가 항상 통과된다' 확인뿐이므로, 독립 계산(예: 종가
시계열로 별도 산출한 buy-and-hold)과 비교하도록 바꾸면 문구값을 하게 된다.

### [MINOR] M5. Random 2SE 판정은 통계적으로 무르다

20봉 보유 수익률을 매 봉 겹쳐 계산하므로 (`backtest/harness.py:441-456`) 표본 간
자기상관이 강한데, `stderr_return_pct`(`backtest/harness.py:121-125`)는 iid를 가정한다.
유효 표본은 n/20 근처라 SE가 크게 과소평가되고, `scripts/verify_phase2.py:224`의 2SE
판정은 데이터가 바뀌면 억울한 FAIL을 낼 수 있다. 또 RandomStrategy의 시드가 (seed, 날짜)
뿐이라 (`strategies/dummy.py:115`) AAPL과 005930.KS의 겹치는 날짜에서 같은 난수가 나온다 —
두 종목 검증이 독립 표본 2개가 아니다.

### [MINOR] M6. 기타

- `slice_as_of`(`backtest/harness.py:183`)는 프로덕션 경로에서 쓰이지 않는다 (replay는
  iloc 사용). look-ahead 차단 지점이라는 docstring과 달리 테스트 전용 죽은 코드다.
- `warmup_bars=200`(`config.py:181`)은 52주 고저(252봉, `config.py:91`)와 sma200 기울기
  (220봉)보다 작다. Phase 3 전략은 평가 초반 ~70봉에서 지표 None → UNAVAILABLE을 만나며,
  B3의 정책에 따라 표본이 조용히 줄어든다. 기본값을 272+로 올리는 것이 맞다.

---

## 관점 3 — Phase 3 수용성 검증 (전략 3종 대입)

### [EXPENSIVE-LATER] E1. SetupMetrics는 '전략 옆에 두는 값'이 아니라 '미너비니 필드의 고정 스키마'다

`core/types.py:290-312`의 필드 전부 — pivot, base 길이/깊이, `contraction_ratio`(VCP),
`volume_dryup_ratio` — 가 미너비니 어휘다. 계약이 `extra="forbid"` + `frozen`이므로 전략이
자기 수치를 임의로 추가할 수 없다. 나머지 3종을 실제 대입하면:

- **와인스타인**: 30주선 돌파 가격은 `pivot_price`에 우겨넣을 수 있지만
  (`examples/sample_buy.json:287`이 실제로 그렇게 했다 — 같은 필드명에 다른 의미), Stage
  진입 후 경과 주수, 저점 절상 횟수 같은 핵심 수치는 자리가 없어 notes 문자열로 밀려난다
  (`examples/sample_buy.json:235` "12주 경과"가 이미 BOOL check의 reason 문자열이다).
- **CANSLIM**: 베이스 카운트(몇 번째 베이스인지 — 오닐 방법론의 핵심 변수), 패턴 유형
  (cup-with-handle / flat / double-bottom), 핸들 정보가 전부 없다.
- **Qullamaggie**: 선행 상승률(gate check의 actual 46.2로만 존재,
  `examples/sample_incomplete_bar.json:225`), 컨솔리데이션 상·하단, EP 갭% 가 없다.

지금 구조로 Phase 3를 진행하면 전략마다 SCHEMA_VERSION을 올리며 필드를 덧대거나(4회
breaking change), notes 문자열에 수치를 숨기게 된다(계약의 존재 이유 부정). 공통 코어
(pivot 계열)를 유지하되 전략별 타입 확장(예: 전략명 태그가 붙은 union, 또는 스키마가 느슨한
`extras: dict[str, float]`)을 **지금** 설계하는 쪽이 싸다. `SetupState`도 같은 문제가 있다:
`CONTRACTING`(`core/types.py:77`)은 VCP 전용 상태이고, Qullamaggie EP(갭업 당일 진입)는
BASE_FORMING→PIVOT_READY 파이프라인에 대응하는 상태가 없다.

### [EXPENSIVE-LATER] E2. 게이트 탈락 = setup 미판정인데, 계약은 이를 NO_SETUP으로 위장한다

`strategies/base.py:102-112` — 탈락 분기에서 `detect_setup`과 `build_setup_metrics`를
호출하지 않고 `setup_state=SetupState.NO_SETUP`, 기본 SetupMetrics(전부 None)를 채운다.
두 가지 결과:

1. **'판정 안 함'과 '셋업 없음'의 구분이 사라진다.** score는 `None ≠ 0.0`을 validator까지
   동원해 지키면서 (`core/types.py:336-344`), setup_state는 같은 원칙을 어긴다. UNEVALUATED
   값이 없다.
2. **근접도 정렬의 대상인 근소 탈락 종목이 L3를 못 그린다.** 7/8 탈락 종목(JNJ 사례)이야말로
   "피벗까지 얼마나 남았나"를 보고 싶은 대상인데, 그 종목의 setup_state는 항상 NO_SETUP,
   setup_metrics는 전부 null이다 (`examples/sample_gate_reject.json:147-156`). 워치리스트
   화면의 핵심 행에서 L3 열이 비게 된다.

셋업 탐지는 채점(SCORE)이 아니므로 게이트-우선 원칙과 충돌하지 않는다. 탈락 종목에도
detect_setup을 돌릴지, 아니면 UNEVALUATED 상태를 추가할지는 Phase 3에서 전략 4종이 이
경로를 각자 구현하기 전에 정해야 한다.

### [EXPENSIVE-LATER] E7. 전략 config ↔ IndicatorConfig ↔ 계약 필드명의 3중 결합

`WeinsteinConfig.ma_period_daily=150`(`config.py:146`)은 `IndicatorConfig.sma_periods`에
150이 있고(`config.py:77`) 계약 필드명이 `sma150`(`core/types.py:372`)이라는 사실에 조용히
의존한다. 기울기도 마찬가지다 — `WeinsteinConfig.slope_lookback=20`은
`sma150_slope_20d_pct`라는 **이름에 20이 박힌** 필드와 별개로 존재한다
(`indicators/snapshot.py:61-63`이 이 함정을 주석으로 인정한다). 파라미터 스윕이 목적인
frozen dataclass인데, 정작 이 값들을 replace로 바꾸면 스냅샷에 해당 필드가 없거나 필드명이
거짓말이 된다. 스윕 가능한 파라미터와 계약에 이름이 박힌 파라미터를 구분해 문서화하거나,
전략이 스냅샷 고정 필드 대신 기간을 키로 조회하는 경로를 마련해야 스윕이 안전해진다.

### [MINOR] M7. 예시 목업과 config 기본값이 이미 어긋나 있다

`WeinsteinConfig.volume_confirm_ratio = 2.0`(`config.py:149`)인데 목업의 와인스타인
volume_confirmation threshold는 1.0이다 (`examples/sample_buy.json:243`,
`examples/sample_gate_reject.json:206`). 목업은 손으로 채운 것이라 강제 장치가 없고, Phase 3
구현이 config를 따르는 순간 목업이 실제 출력과 달라진다 — "계약 변경 시 여기가 먼저
깨진다"는 examples의 존재 의의가 임계값 차원에서는 작동하지 않는다는 신호다.

---

## 관점 4 — 자기 규칙 위반 점검

"하지 말 것" 목록을 항목별로 대조했다. 코드가 실제로 지키는 항목: 점수 합산 금지(테스트
잠김), evaluate 오버라이드 금지(더미 3종 준수), 외부 TA 라이브러리(import 없음), 렌더러
판정(미구현이라 위반 불가), 네트워크 격리(conftest 차단 확인), 지표 테스트(전 지표 손계산
테스트 존재). 위반 또는 회색 지대:

- **[BLOCKER] B4 (재게)** — "값이 두 곳에 중복 저장된 부분의 드리프트": consensus 중복
  필드 5개 중 4개가 미검증. 상세는 관점 1.
- **[EXPENSIVE-LATER] E2 (재게)** — "계산 불가를 0.0이나 False로 채우지 말 것"의
  setup_state 판:  NO_SETUP이 채움값으로 쓰인다.
- **[BLOCKER] B3 (재게)** — "UNAVAILABLE이 FAIL로 둔갑" 금지가 GateCheck 레벨에서는
  지켜지지만 verdict 레벨에서 깨진다.
- **[MINOR] M8a. `None or 0.0` 패턴이 전략 파일에 있다** — `strategies/dummy.py:233`
  `future = self._future_return_pct(ctx) or 0.0`. 금지 목록의 문구 그대로다. build_score는
  게이트 통과 시에만 불리고 통과는 future 존재를 함의하므로 현재 도달 불가지만, 이 파일은
  Phase 3 전략 작성자가 베낄 참조 구현이다.
- **[MINOR] M8b. model_dump가 json_out 밖에서 호출된다** — `scripts/verify_phase1.py:198`.
  예외 조항은 '계약 테스트'뿐이므로 스크립트는 위반이다. 더 근본적으로는
  `render/json_out.py:17-29`가 전부 NotImplementedError라서 **규칙을 지킬 수 있는 경로
  자체가 아직 없다.** 직렬화 진입점이 비어 있는 채로 호출부가 먼저 늘고 있다.
- **[MINOR] M8c. 검증 스크립트의 하드코딩** — `scripts/verify_phase2.py:119`의 `5.0`
  (초과수익 강조 임계값), `:188`의 문자열 `"(n < 30)"`(config의 `min_sample_size`가 바뀌면
  거짓말이 된다). 표시용이라 심각하진 않지만 임계값 단일 출처 원칙의 예외가 스크립트에
  쌓이기 시작한 지점이다.
- **[MINOR] M8d. '재무 조언 문구 금지'와 RiskPlan 필드의 긴장** — `RLevel.suggested_action`
  ("1/3 부분 익절", `examples/sample_buy.json:355`)과 `exit_rules`는 실행 권고 문장이다.
  '진단과 근거'와 '조언'의 경계를 어디에 긋는지 프로젝트 차원의 정의가 없으면, 이 필드들의
  문구가 규칙 위반인지 아닌지 리뷰 때마다 논쟁하게 된다. 규칙 쪽에 예외를 명시하든 문구를
  조건 서술형("~시 청산 조건 충족")으로 통일하든 정리해 둘 것.

---

## 후속 조치 내역 (2026-08-19, 리뷰 직후 반영)

계약 JSON **형태(스키마)는 변경하지 않는** 범위에서 다음을 수정했다. 테스트 256개 전부
통과, `verify_phase2.py` 세 예측 전부 PASS 확인.

| 항목 | 조치 |
|---|---|
| B1 | 진입 기준을 verdict==BUY로 변경. `BacktestResult`에 `gate_passed_not_entered` 집단 추가, 3집단 분리 집계 (`backtest/harness.py`) |
| B2 | `replay`/`audit_lookahead`/`evaluate_results`에 `regime_by_date`/`stage_by_date`/`rs_percentile_by_date` 시점별 주입 경로 추가. 주입 시리즈의 point-in-time 책임은 호출부에 있음을 문서화 |
| B3 | `strategies/base.py`의 `build_gate_result()`로 게이트 판정 규약 단일화 — UNAVAILABLE은 PASS로 세지 않음(AND 게이트 차단). verdict enum 차원의 구분은 Phase 3 스키마 개정 대상으로 유보 |
| B4 | DiagnosisReport validator 확장: gate_progress 빈 리스트 우회 차단, progress_ratio·verdict_counts·buy_strategies·gate_passed_strategies·agreement 전부 원본 대조. agreement 의미를 examples 기준(전체 전략 분모)으로 확정하고 enum 주석 수정 |
| E2 | 게이트 탈락 종목에도 detect_setup/build_setup_metrics 수행 (`StrategyBase.evaluate`). UNEVALUATED enum 추가는 스키마 변경이라 유보 |
| E5 | 감사 비교를 (verdict, passed, score) 3필드에서 **StrategyVerdict 전체 동등성**으로 확대. 표본 수·무상태 가정·주입 시리즈 한계를 docstring에 명시. 결정론 요구사항을 Strategy Protocol에 문서화 |
| M4 | verify_phase2 예측 1을 하네스 코드를 쓰지 않는 독립 계산과의 비교로 교체. 같은 성질의 단위 테스트 추가 |
| M5 | Random 2SE 판정에 유효표본(n/horizon) 보정. RandomStrategy 시드에 티커(crc32) 혼합 — 종목 간 표본 독립화 |
| M6 | `warmup_bars` 200→272 (252 고저 + 20 기울기). 죽은 코드 `slice_as_of` 제거 |
| M8a | dummy의 `or 0.0` 패턴 제거 — 도달 불가 분기를 명시적 예외로 |
| M8b | `render/json_out.py` 구현 (to_payload/to_json/json_schema). verify_phase1의 직접 `model_dump_json` 호출을 `to_json`으로 교체 |
| M8c | verify_phase2의 `5.0` 매직값을 명명 상수로, `(n < 30)` 리터럴을 config 값으로 |

**미조치 (스키마 개정 필요 — Phase 3 착수 시 한 번의 SCHEMA_VERSION 인상으로 묶어서 처리 권장):**
E1(SetupMetrics/SetupState 전략별 확장), E3(게이트 근접도 마진 필드), E4(BETWEEN용
threshold_high), M1(regime 근거), M2(전략 에러 표현), M3(SetupState 순서), M7(목업-config
임계값 드리프트 — 진짜 전략 출력으로 목업을 교체할 때 자연 해소), E6(지표 사전 계산
리팩터링 — 스윕 구현 시점에), E8(워치리스트 요약 계약).

---

## 관점별 결론 요약

1. **계약**: 4계층 UI의 뼈대는 그려진다. 그러나 근접도 정렬의 품질(E3), 근소 탈락 종목의
   L3(E2), consensus 잠금(B4)이 빠져 있어 "샘플 3개만으로 목표 UI를 온전히 그릴 수 있다"고는
   말할 수 없다.
2. **하네스**: 구조적 look-ahead 차단은 진짜다. 그러나 verdict 무시(B1)와 컨텍스트 결손(B2)
   때문에 **현재 하네스가 잴 수 있는 것은 '항상 BUY인 전략'뿐이다** — 공교롭게도 더미 3종이
   전부 그렇다. 예측 1은 동어반복(M4)이라 증명력이 표기보다 약하다.
3. **Phase 3 수용성**: StrategyBase/GateResult 골격은 4전략을 수용한다. SetupMetrics와
   SetupState는 수용하지 못한다(E1). 지금 고치면 스키마 1회 변경, 나중이면 전략당 1회다.
4. **자기 규칙**: 대부분 구조로 강제되어 있으나, 강제 장치가 "있다고 주장되지만 실제로는
   부분적인" 지점(B4의 validator, B3의 verdict 레벨)이 정확히 문서의 자신감이 가장 큰
   지점과 겹친다.

---

## Phase 3 착수 시 조치 (2026-08-19)

리뷰가 "한 번의 SCHEMA_VERSION 인상으로 묶어서 처리"를 권고한 항목 중, Phase 3 진행에
직접 필요한 두 건을 처리했다. **SCHEMA_VERSION 1.0.0 -> 1.1.0.**

| 항목 | 조치 |
|---|---|
| E1 | `SetupMetrics`를 **공통 코어 + 전략별 detail** 구조로 변경. VCP 어휘(`contraction_ratio` / `volume_dryup_ratio`)를 `MinerviniSetup`으로 이동하고 `contraction_count` 추가. `SetupDetail`은 `kind` 판별 유니온이며, 새 전략 타입을 덧붙이는 것은 additive 변경이다. **아직 구현하지 않은 전략의 타입은 만들지 않았다** — 구현 전에 지은 필드명은 추측이고, 이 프로젝트는 미확정 필드를 계약에 넣지 않는다 |
| E3 | `GateCheck.shortfall_pct` 추가. 계산 주체를 `strategies/base.py`의 `build_gate_check()` 한 곳으로 고정해 프론트가 comparator 방향을 해석하지 않게 했다. threshold가 0인 조건(기울기 > 0)은 정규화 불가라 None이며, 이는 알려진 한계다 |
| B2 (잔여) | `regime/market.py`와 `data/universe.py`의 근사 RS를 구현해 주입 경로를 실제로 채웠다. 벤치마크 픽스처 2종(SPY, ^KS11) 추가. `tests/test_regime_and_rs.py`가 시점 정합성을 잠근다 |

**여전히 미조치** (스키마 변경이 필요하지만 Phase 3에 불필요): E4(BETWEEN용 threshold_high),
M1(regime 근거), M2(전략 에러 표현), M3(SetupState 순서), E6(지표 사전 계산), E8(워치리스트
요약 계약). M7(목업-config 임계값 드리프트)은 목업이 여전히 손으로 채운 값이라 남아 있다.

### Phase 3에서 새로 발견해 고친 결함

리뷰에 없던 것들로, 전부 구현 중 테스트가 잡아냈다.

1. **피벗을 베이스 최고가로 잡으면 돌파를 영원히 탐지할 수 없다.** 베이스 최고가에는
   오늘 봉의 고가가 포함되므로 종가가 그것을 넘는 것이 수학적으로 불가능하다.
   피벗을 마지막 수축의 고점(스윙 고점)으로 바꿨다.
2. **신고가 돌파 시 베이스 앵커가 오늘 봉으로 점프해 베이스가 소멸한다.** 앵커 후보를
   최근 `min_base_length_days` 봉에서 제외해 앵커를 과거에 고정했다.
3. **게이트 체크의 `actual`에 기준값을 넣어 미달 폭이 항상 0이었다.**
   `price_above_sma50` 등에서 actual과 threshold가 같은 값이었다. 회귀 테스트 추가.

---

## Phase 3.5 조치 (2026-08-19)

| 항목 | 조치 |
|---|---|
| E6 | **해결.** `build_indicator_frame()` + `snapshot_at()`으로 지표를 시계열 단위로 한 번만 계산한다. replay가 시점마다 윈도우 전체를 재계산하던 O(n²)가 O(n)이 됐고 실측 33배(6ms -> 0.18ms/봉) 빨라졌다. 전체 테스트 시간도 17초에서 5초로 줄었다. 안전성은 가정이 아니라 `tests/test_snapshot.py`가 '전체로 계산한 t번째 값 == df[:t+1]로 계산한 마지막 값'으로 검증한다 |
| 표본 부족 | `evaluate_panel()` 추가. 종목별 요약을 평균내지 않고 **원본 Outcome을 합쳐** 다시 요약한다. US 29종목 320건 / KOSPI 10종목 74건으로 `min_sample_size`를 넘겼다 |
| RS 근사 제거 | Phase 3의 `approximate_rs_percentile_series`를 삭제했다. '자기 과거 대비 가속도'와 '유니버스 대비 수준'이라는 서로 다른 두 개념을 남겨 두면 잘못된 쪽을 쓰게 된다. 유니버스가 없으면 RS는 None이고 게이트는 UNAVAILABLE이다 |

**여전히 미조치**: E4(BETWEEN용 threshold_high), M1(regime 근거), M2(전략 에러 표현),
M3(SetupState 순서), E8(워치리스트 요약 계약), M7(목업-config 임계값 드리프트).

### Phase 3.5에서 확인된 한계

- **생존편향은 유니버스를 갖춰도 제거되지 않았다.** 목록이 현재 상장 종목만 담기
  때문이며, 시점별 구성종목 데이터베이스가 없으면 원리적으로 불가능하다.
  이제는 RS 백분위의 **분모**까지 편향되므로 Phase 3보다 영향이 커졌다.
- **백테스트 패널이 유니버스보다 작다.** RS는 종가만 필요해 115종목을 쓰지만,
  백테스트는 OHLCV가 필요해 커밋 가능한 크기(29종목)로 제한했다. 패널 선정은
  성과와 무관한 결정론적 간격(stride)이지만, 유니버스 전체가 아니라는 점은 남는다.

---

## Phase 4 조치 (2026-08-19)

전략을 3종으로 늘렸다 (와인스타인 Stage Analysis, Qullamaggie 브레이크아웃).
CANSLIM은 재무 데이터(발표일 `as_of` 필터)가 필요해 별도 Phase로 분리했다.

| 항목 | 조치 |
|---|---|
| 스키마 1.2.0 | `SetupDetail`이 실제 판별 유니온이 됐다 (`MinerviniSetup \| WeinsteinSetup \| QullamaggieSetup`). additive 변경이지만 계약 문자열이 바뀌므로 `SCHEMA_VERSION`을 올리고 목업 3종을 갱신했다 |
| 프랙탈 스윙 공유 | 미너비니에만 있던 스윙 고저 판정을 `indicators/core.py`로 올렸다 (`swing_high_flags` / `swing_low_flags` / `swing_positions`). 전략 3종이 각자 다른 k로 같은 정의를 쓴다. 손계산 테스트 6종 추가 |
| 시점 정합성 CI화 | `tests/test_strategies_lookahead.py`가 전략 3종에 `audit_lookahead()`를 돌린다. 이전에는 더미 전략만 CI에서 감사받았고 진짜 전략은 verify 스크립트(사람이 실행)에서만 검사됐다 |
| 게이트 중복 방지 | 와인스타인 게이트에서 '30주선 위 / 30주선 상승'을 뺐다. Stage 2 판정이 이미 함의하므로 다시 세면 진행률이 부풀고 워치리스트 근접도 정렬이 낙관 쪽으로 틀어진다. 회귀 테스트로 잠갔다 |

### Phase 4에서 측정된 것

`python scripts/verify_phase4.py` 기준 (us_large 29종목 / kospi 10종목 패널):

- **판정이 갈린다**: 마지막 봉 기준 us 10/29종목, kospi 2/10종목에서 세 전략의 판정이
  엇갈렸다. 갈리지 않았다면 방법론을 나란히 둘 이유가 없었을 것이다.
- **선별성이 크게 다르다**: 진입률 minervini 0.5% / weinstein 8.7% / qullamaggie 0.3%
  (us 표본 3종목, 1269봉 기준). 와인스타인은 게이트가 자주 열려 무조건부 매수에
  가까워지고, 실제로 20봉 초과수익이 -0.28%p로 벤치마크에 붙었다.
- **감사는 깨끗하다**: 새 전략 2종 모두 look-ahead 위반 0건.
- **표본은 충분하다**: 풀링 진입이 us 320/1287/153건, kospi 74/75/96건으로
  `min_sample_size`(30)를 전부 넘겼다.

### Phase 4에서 발견된 결함 (미해결)

1. **Qullamaggie의 SCORE가 성과와 역방향이다.** 표본이 충분한 점수 구간만 비교했을 때
   높은 구간의 평균수익이 낮은 구간보다 나빴다 (us: 60-70 +2.71% vs 70-85 -1.19%,
   kospi: 60-70 +6.49% vs 70-85 +3.36%). 게이트는 정상 작동하지만 채점 항목이
   타이밍을 재고 있다는 증거가 없다. verify 스크립트가 이 역전을 자동 검출한다
   (`score_direction_holds`).
   **이 표본에 맞춰 가중치를 조정하는 것은 과적합**이므로 하지 않았다. 파라미터 스윕
   기능(`backtest.sweep`)이 붙고 홀드아웃 구간을 나눌 수 있게 된 뒤에 다룰 문제다.
2. **와인스타인 게이트가 느슨하다.** `min_rs_percentile=50`은 '중앙값 이상'이라
   대형주 유니버스에서는 사실상 필터가 되지 못한다. 방법론 해석상 국면 전환 초기를
   잡으려는 의도였으나, 결과는 벤치마크 수렴이다. 같은 이유로 임의 조정은 하지 않았고
   스윕 대상 1순위로 기록해 둔다.
3. **`stage2_age_days`는 관측 하한이다.** 워밍업 구간에서 조건이 False로 떨어지므로
   실제보다 짧게 나올 수 있다. 신선도를 **과대평가하지 않는 방향**의 편향이라 그대로 뒀다.

4. **목업의 와인스타인·Qullamaggie 판정이 구현과 다르다.** `examples/`의 두 판정은
   Phase 0에서 손으로 지어낸 것이라 게이트 체크 id와 배점이 실제 구현과 어긋난다
   (미너비니 판정은 Phase 3에서 맞췄다). 계약 형태는 유효하고 `tests/test_examples.py`가
   보여주려는 성질(전략마다 만점 척도가 다르다 / UNAVAILABLE은 FAIL과 다르다)도
   그대로여서 이번에는 건드리지 않았다. 다만 **프론트가 이 id로 키잉하면 안 된다** —
   기존 M7(목업 드리프트)과 같은 성격의 부채로 묶어 둔다.

**여전히 미조치** (Phase 0~2 리뷰 항목): E4(BETWEEN용 threshold_high), M1(regime 근거),
M2(전략 에러 표현), M3(SetupState 순서), E8(워치리스트 요약 계약),
M7(목업-config 임계값 드리프트 — Phase 4에서 목업-전략 id 드리프트가 추가됐다).
