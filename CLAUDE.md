# trading-tracker

티커를 입력받아 여러 추세추종 방법론(미너비니 SEPA, 와인스타인 Stage Analysis,
오닐 CANSLIM, Qullamaggie)으로 **각각 독립 평가**하고 판정을 나란히 보여주는 진단 시스템.

매매를 자동 실행하지 않는다. 진단과 근거 제시까지가 범위다.

## 현재 상태 (Phase 7 완료)

계약(v1.4.0) + 데이터 + 지표 + 하네스 + 전략 3종 + 유니버스 RS + **진단 파이프라인**까지
구현됐다. `python main.py AAPL`이 실제로 동작한다.

- **Phase 0 (계약)**: `core/types.py`, `config.py`, `examples/`
- **Phase 1 (데이터·지표)**: `indicators/core.py`, `indicators/snapshot.py`,
  `data/fetcher.py`, 고정 CSV 픽스처
- **Phase 2 (하네스)**: `backtest/harness.py`, `strategies/base.py`(템플릿 메서드),
  `strategies/dummy.py`(검증용 더미 3종), `core/context.py`
- **Phase 3 (전략)**: `strategies/minervini.py`, `regime/market.py`
- **Phase 3.5 (유니버스)**: `data/universes/*.txt` 목록, 교차단면 RS 백분위,
  다종목 패널 집계(`evaluate_panel`), 지표 사전 계산
- **Phase 4 (전략 2종 추가)**: `strategies/weinstein.py`, `strategies/qullamaggie.py`,
  스키마 1.2.0(`WeinsteinSetup` / `QullamaggieSetup`), 프랙탈 스윙 지표 공유
- **Phase 4 리뷰 반영** (`docs/review_phase3-4.md`): 돌파 거래량을 BUY의 필요조건으로,
  베이스 깊이 상한 적용, 스키마 1.3.0(`breakout_volume_ratio`를 미너비니·Qullamaggie
  detail에 추가), RS 백분위 규약 통일, `core/report.py`(리포트 조립),
  `scripts/make_examples.py`(예시 자동 생성)
- **Phase 5 (파이프라인)**: `main.diagnose()`(유일한 오케스트레이션 지점),
  `risk/planner.py`, `render/cli.py`, `strategies/registry.py`,
  `data/universe.py`의 종가 수집, 스키마 1.4.0(`risk_plans`)
- **Phase 6 (스윕·홀드아웃)**: `backtest/sweep.py`, `EvalWindow`(평가 시점 제한),
  학습/홀드아웃 분할 + embargo, 진입 정의 플래그(`require_breakout_for_buy`)
- **Phase 7 (워치리스트)**: `main.scan()`, `core/watchlist.py`, 스키마 1.5.0
  (`WatchlistReport` — 두 번째 루트 계약), `MarketData`(시장 재료 1회 생성),
  수집·스캔 진행 표시
- **미구현**: CANSLIM(재무 데이터 필요), 웹 프론트엔드

### 스캔은 진단을 여러 번 돌린 것이다

`main.scan()`은 유니버스 종목마다 `diagnose()`를 **그대로** 부르고 결과를 요약해
워치리스트로 조립한다. 요약용 별도 계산을 만들면 '목록에서는 BUY였는데 눌러 보니
WATCH'가 언젠가 반드시 생긴다 — 판정 경로가 하나여야 목록과 상세가 같은 말을 한다.
`tests/test_watchlist.py`와 `scripts/verify_phase7.py`가 이 일치를 실제로 대조한다.

- **시장 공통 재료는 `MarketData`로 한 번만 만든다** (국면·유니버스 종가·RS 프레임).
  종목마다 다시 만들면 116종목 스캔이 RS 프레임을 116번 계산한다.
- 한 종목의 수집 실패는 스캔을 멈추지 않고 `failed`에 이유와 함께 남는다.
  조용히 빠지면 '116종목 스캔'이라는 말이 거짓이 된다.
- 실측 비용은 종목당 15~18ms다 (지표+전략 3종). 무거운 것은 수집이고 그것은
  종목별 캐시로 이미 해결돼 있다 — 첫 진단 때 유니버스 OHLCV가 통째로 캐시된다.

### 워치리스트는 요약 계약이다

`WatchlistReport`(1.5.0)는 `DiagnosisReport`와 **다른 루트 계약**이다. 진단 리포트를
그대로 나열하면 29종목에 440KB이고, 그중 대부분은 표 한 줄에 필요 없는 설명 문장이다
(실측 요약 39KB = 8.8%). 상세가 필요하면 그 티커를 개별 진단한다.

- `StrategySummary`에 `checks` / `components` / `notes` / `setup_metrics`를 넣지 말 것.
  넣는 순간 요약이 아니게 된다 (`tests/test_types_contract.py`가 막는다).
- **정렬이 계약이다**: `(BUY 전략 수, 게이트 진행률)` 내림차순, 동점은 티커순.
  조립(`core/watchlist.py`)이 정렬하고 validator가 순서를 강제하므로 **렌더러는 다시
  정렬하지 않는다.** 게이트 근접도가 두 번째 키인 이유는 8개 중 7개를 통과한 종목이
  내일 조건을 채울 후보이기 때문이다 — `GateProgress`와 `shortfall_pct`가 Phase 0부터
  계약에 있던 것이 이 정렬을 위해서였다.
- `--top` / `--verdict`는 **표시 필터**다. 계약은 스캔한 전부를 담고, 화면에서 몇 개를
  보여줄지만 고르며, 걸러낸 개수를 함께 출력한다.

### 파라미터는 학습 구간에서 고르고 홀드아웃으로 확인한다

`backtest/sweep.py`. 3년 한 표본에서 후보 k개를 돌려 최고를 고르면 그 값에는 표본
노이즈가 섞이고, 노이즈 부분은 다음 기간에 재현되지 않는다. 그래서 구조로 강제한다:

- **선택은 `SweepResult.best_train`이 한다** — 학습 구간 성과만 본다. 홀드아웃 수치는
  고른 뒤에 붙는 확인값이며, 보고 다시 고르면 그 순간 홀드아웃이 아니다.
- **학습 표본이 `min_sample_size` 미만이면 고르지 않는다** (`best_train`이 None).
  '고를 근거가 없다'와 '기본값이 최고다'는 다른 결론이다.
- **두 구간 사이를 embargo만큼 비운다.** 보유기간이 겹치면 학습 막바지 시그널의 성과가
  홀드아웃 봉으로 측정되어 구간이 섞인다. 기본값은 `max(horizons) + entry_offset_bars`.
- 스윕은 **후보를 기각하는 도구**다. 홀드아웃에서 무너지면 과적합이라는 증거가 되지만,
  살아남는 것은 '기각되지 않았다'는 뜻일 뿐 최적값이라는 뜻이 아니다.
- `scripts/verify_phase6.py`는 측정만 하고 **config를 고치지 않는다.** 임계값 변경은
  사람의 결정이다.

**구간을 나눌 때 df를 잘라내지 않는다.** `EvalWindow`는 '평가할 시점'만 제한한다.
뒤쪽 구간을 df째로 잘라 넘기면 그 구간의 지표가 워밍업 부족으로 None이 되어, 같은
전략이 구간마다 다른 것을 보게 된다 — 분할의 목적은 '언제를 평가하는가'를 나누는
것이지 '무엇을 아는가'를 바꾸는 것이 아니다.

### 진입 정의는 config가 들고 있다

`require_breakout_for_buy`(전략 3종 각자의 config). True면 PIVOT_READY(돌파 전 피벗
근접)는 BUY가 아니라 WATCH다. 세 방법론의 원전은 모두 돌파를 확인하고 사지만 현재
BUY의 대부분은 PIVOT_READY이며, 어느 쪽이 나은지는 재서 정할 문제다. 기본값 False를
유지하는 이유는 조용히 바꾸면 이전 Phase의 수치와 비교가 불가능해지기 때문이다.

### 진단 한 건은 `main.diagnose()`로만 흐른다

수집 -> 지표 -> 국면/Stage -> RS -> 전략별 평가 -> 리스크 플랜 -> 리포트.
CLI와 향후 웹 API가 이 함수 하나를 공유한다. 여기서 임계값을 비교하지 않는다 —
재료를 모아 전략에 넘기고 결과를 조립할 뿐이다.

**RS 유니버스는 파이프라인의 선행 조건이다.** 세 전략 모두 게이트에 RS 조건이 있고,
RS가 None이면 UNAVAILABLE이며, UNAVAILABLE은 AND 게이트를 막는다. 즉
`load_universe_closes()`가 유니버스 **전체**의 종가를 확보하지 못하면 어떤 종목을
진단해도 전부 REJECTED_BY_GATE가 나온다. 그 화면은 '이 종목이 나쁘다'와 구분되지
않으므로 실패 시 CRITICAL 경고로 이유를 남긴다 (`scripts/verify_phase5.py`가 이
시나리오를 재현해 확인한다). 첫 실행은 구성종목 수만큼 네트워크를 타고, 이후에는
하루 단위 캐시를 읽는다.

**전략 하나가 터져도 나머지는 낸다.** `WarningCode.STRATEGY_ERROR`의 계약상 의미가
'해당 전략만 실패'이므로, 예외를 낸 전략은 판정 목록에서 빠지고 경고로 남는다.
빠진 전략을 REJECTED_BY_GATE로 채우면 '그 방법론이 거절했다'는 거짓이 된다
(컨센서스 분모도 그만큼 줄어든다).

### 리스크 플랜은 전략마다 따로다

`risk_plans: dict[전략명, RiskPlan]`이다. 세 전략의 피벗이 다르므로 진입가가 다르고,
진입가가 다르면 손절가·주수·목표가가 전부 달라진다. 하나로 합치려면 '어느 방법론을
따를 것인가'를 골라야 하는데 그 선택은 사용자의 몫이지 조립 코드의 몫이 아니다.

- 플랜은 **진입 의사가 있는 판정(BUY/WATCH)에만** 붙는다. `DiagnosisReport` validator가
  강제한다 — AVOID나 게이트 탈락에 매수 계획이 실리면 안 된다.
- 진입가는 **피벗이 아직 위면 피벗, 이미 넘었으면 현재가**다. 돌파 전인데 현재가로
  계산하면 손절폭이 실제보다 넓게 나온다.
- 손절은 ATR 배수와 `max_stop_pct` 중 **타이트한 쪽**이다. 방법론별 손절 규칙
  (베이스 저점 아래 등)은 아직 없다 — 넣으려면 전략이 손절 후보를 `SetupMetrics`에
  싣는 계약 변경이 먼저다.
- `risk_amount`는 예산이 아니라 **체결 기준 실제 손실액**이다. 포지션 상한에 걸려
  주수가 깎이면 실제 리스크도 줄어드는데, 예산을 그대로 실으면 화면의 숫자가 거짓이 된다.

### 전략 목록은 `strategies/registry.py` 하나다

`main.py`가 전략 이름을 알면 전략 추가가 코어 수정을 요구하게 된다(원칙 2 위반).
백테스트 스크립트·목업 생성기도 이 레지스트리를 쓴다 — 호출부마다 목록을 들고 있으면
'어떤 전략이 도는가'가 곳마다 달라진다. `strategies/dummy.py`의 더미 3종은 등록하지
않는다 (하네스 검증용이지 매매 판단용이 아니다).

### 전략마다 게이트의 축이 다르다

세 전략의 게이트는 조건 수도 내용도 다르다. **같은 질문을 세 번 하는 게이트가 아니라
서로 다른 질문을 하는 게이트**여야 판정이 갈리고, 판정이 갈려야 나란히 둘 이유가 있다.

| 전략 | 게이트가 묻는 것 | 조건 수 |
|---|---|---|
| minervini | 이동평균 8개 조건이 정렬됐는가 | 8 |
| weinstein | Stage 2인가 + 10주선/국면/RS/유동성 | 5 |
| qullamaggie | 직전 급등이 있었는가 + ADR/거래대금/RS/EMA21 | 5 |

`us_large` 패널 실측 진입률은 minervini 0.2% / weinstein 5.0% / qullamaggie 0.3%다
(표본 3종목 기준. 돌파 거래량 조건을 넣기 전에는 0.5% / 8.7% / 0.3%였다).
게이트가 자주 열리는 전략은 무조건부 매수에 수렴하며, 그때 초과수익이 0 근처로
나오는 것은 결함이 아니라 정합성 신호다 (와인스타인 20봉 초과수익 -0.76%p).

**함의 관계인 조건을 중복해서 세지 말 것.** 와인스타인 게이트에 '30주선 위'와
'30주선 상승'을 다시 넣으면 Stage 2가 이미 함의하는 사실을 세 번 세게 되어
`pass_count/total` 진행률이 부풀고 워치리스트 근접도 정렬이 낙관 쪽으로 틀어진다.
`tests/test_strategies_weinstein.py`가 이 설계를 잠근다.

### 돌파 거래량은 BUY의 필요조건이다 (세 전략 공통)

세 방법론 모두 '돌파를 산다'고 말하고, 원전 모두 돌파 거래량 확인을 요구한다.
그런데 채점 항목으로만 두면 거래량 0점이어도 다른 항목이 그 자리를 메워 BUY가 나온다 —
Phase 4까지가 실제로 그 상태였다 (와인스타인은 "돌파는 거래량이 조건이다"라는 문구를
출력하면서 강제하지 않았고, 미너비니의 `breakout_volume_ratio`는 참조 0곳의 죽은 config였다).

지금은 **`decide()`의 이진 조건**이다. 게이트가 아니라 decide인 이유: 게이트는 추세·국면·
자격 요건을 묻는 자리이고, 돌파 거래량은 그 뒤의 셋업·타이밍 문제다.

- 확인 대상은 `setup_state is BREAKOUT`일 때뿐이다. PIVOT_READY는 아직 돌파가 없어
  확인할 대상 자체가 없으므로 notes에 "돌파 시 무엇을 확인해야 하는지"를 남긴다.
  **현재 BUY의 대부분은 PIVOT_READY다** — 이 조건은 돌파 진입만 거른다.
- 비율을 낼 수 없으면(50일 평균 거래량 미산출) **확인 실패**로 본다. 확인 못 한 조건을
  충족으로 치지 않는다 (게이트의 UNAVAILABLE 정책과 같은 방향).
- 계산은 `strategies/base.py`의 `breakout_volume_ratio()` 하나다 (최근 2k봉 최대 거래량 /
  50일 평균). 값은 `SetupMetrics.detail.breakout_volume_ratio`로 계약에 실린다 —
  판정을 가른 수치는 근거로 보여야 한다.
- 미너비니는 여기에 더해 **베이스 깊이 상한**(`max_base_depth_pct`)을 BUY의 필요조건으로
  둔다. 깊은 베이스는 돌파해도 사지 않는다. 셋업 판정 자체는 그대로 수행한다.

### 프랙탈 스윙은 지표다 (전략 3종이 공유한다)

`swing_high_flags` / `swing_low_flags` / `swing_positions`가 `indicators/core.py`에 있다.
"i봉이 i±k 구간의 극값인가"는 k만 정해지면 계산식이 하나뿐이므로 지표의 자격을 갖는다.
전략은 각자 자기 config의 k로 호출한다.

마지막 봉은 **절대 스윙 고점이 될 수 없다**(양옆 k봉이 필요하므로). 이 성질이
돌파 탐지를 가능하게 한다 — 오늘 고가가 피벗이 되면 '오늘 종가가 오늘 고가를 넘어야'
하므로 BREAKOUT이 영원히 나오지 않는다. Phase 3에서 실제로 밟았던 함정이다.

### 표본은 종목을 늘려서만 모인다

미너비니는 선별적이라 종목 하나당 3년에 5~24건밖에 진입하지 않는다.
`evaluate_panel()`이 여러 종목의 **원본 Outcome을 합쳐** 다시 요약한다 —
종목별 요약을 평균내면 표본이 적은 종목에 과한 가중치가 실린다.
US 29종목 풀링으로 진입 166건, KOSPI 10종목으로 35건이 모인다
(돌파 거래량 조건 추가 전에는 320건 / 74건이었다).
봉이 모자라 평가하지 못한 티커는 조용히 사라지지 않고 `PanelResult.skipped_tickers`에 남는다 —
분모가 줄어든 사실이 안 보이면 '29종목 패널'이라는 말이 거짓이 된다.

### 지표는 시계열로 한 번만 계산한다

`build_indicator_frame()`이 지표를 한 번 계산하고 `snapshot_at()`이 시점만 뽑는다.
백테스트가 시점마다 윈도우 전체를 재계산하던 O(n²)를 O(n)으로 줄인 것으로,
실측 33배(6ms -> 0.18ms/봉) 빨라졌다.

안전한 이유는 모든 지표가 후방 참조만 하기 때문이며, 이는 가정이 아니라
`tests/test_snapshot.py`가 '전체로 계산한 t번째 값 == df[:t+1]로 계산한 마지막 값'을
검증하는 성질이다. look-ahead 감사도 두 패스에서 길이가 다른 df로 프레임을 만들어
교차 확인한다.

### 전략을 추가할 때 반드시 볼 것

`strategies/minervini.py`가 참조 구현이다 (게이트 조건이 가장 많고 VCP 어휘가 전부 있다).
`weinstein.py`는 **주입 컨텍스트(Stage·regime)를 게이트에 쓰는 예**,
`qullamaggie.py`는 **추세가 아닌 것(급등 이력·변동성·유동성)을 게이트에 두는 예**다.
새 전략은 이 구조를 따른다:

- `build_gate_check()`로만 GateCheck를 만든다 — `shortfall_pct`(미달 폭) 계산이
  여기 한 곳에 있다. 전략마다 계산하면 프론트가 자기 방식으로 다시 계산하게 된다.
- `build_gate_result()`로만 GateResult를 만든다 — UNAVAILABLE 정책이 여기 고정된다.
- 전략 고유 셋업 수치는 `SetupMetrics.detail`에 **전략별 타입**으로 넣는다
  (`MinerviniSetup` 참조). 공통 코어(pivot/base)는 그대로 쓰고, 새 타입을
  `SetupDetail` 유니온에 덧붙인다 — 이는 additive 변경이다.
- 게이트 체크의 `actual`은 **측정값**이고 `threshold`가 기준값이다. 둘에 같은 값을
  넣으면 게이트 판정은 맞아 보이지만 미달 폭이 항상 0이 되어 근접도 정렬이 죽는다.
- 서로 **함의 관계인 조건을 중복해서 넣지 않는다** (위 '전략마다 게이트의 축이 다르다').
  BOOL 조건은 `actual`/`threshold`를 비우고 `Comparator.BOOL`을 쓴다.
- 새 전략의 시점 정합성은 `tests/test_strategies_lookahead.py`에 한 줄 추가해 잠근다.
  전략별 단위 테스트의 결정론 검사만으로는 시점이 달라졌을 때의 흔들림을 못 잡는다.
  (감사는 미국·KOSPI 픽스처 2종목에서 돈다. 시장이 하나면 통화 스케일·거래일이 다른
  경로가 검사되지 않는다.)
- 탐지(베이스·거래범위·컨솔)는 `@memoize_per_context`를 붙인 메서드로 감싸 한 번만
  계산한다. evaluate() 한 번에 네 번씩 같은 탐지를 돌리던 낭비를 막는다. 캐시 키는
  **ctx 객체의 신원**이어야 한다 — 값(티커·날짜·길이)으로 키를 만들면 look-ahead 감사의
  두 패스가 캐시를 공유해 위반이 사라진 것처럼 보인다.
- 점수 정규화는 `strategies/base.py`의 `decay_score` / `scaled_score`를 쓴다. 전략마다
  복사하면 같은 '이상/최악' 표현이 전략별로 다른 곡선이 된다.
- 전체 히스토리를 훑는 계산을 전략에 두지 않는다. 백테스트는 시점마다 evaluate를 부르므로
  창이 상수로 묶이지 않으면 그 전략만 O(n^2)로 되돌아간다 (와인스타인 `stage2_age_days`가
  실제로 그랬고, 지금은 `max_stage2_age_days`에서 잘라 상수 창으로 계산한다).

### 전략을 만들기 전에 자를 먼저 만들었다

`strategies/dummy.py`의 더미 3종은 **결과가 미리 예측되는** 전략이다.
하네스를 고칠 때마다 `python scripts/verify_phase2.py`로 예측이 여전히 맞는지 확인한다.
예측이 어긋나면 전략이 아니라 하네스를 의심한다.

  - `AlwaysBuy` -> buy-and-hold와 초과수익 정확히 0
  - `Random` -> 초과수익이 2 표준오차 안
  - `PerfectHindsight` -> look-ahead 감사에 적발

### 주입 시리즈(regime / stage / RS)의 시점 정합성은 산출 코드 책임이다

`regime/market.py`와 `data/universe.py`의 `*_series()`는 날짜 t의 값을 t 이하
데이터로만 계산한다. 하네스의 look-ahead 감사는 양쪽 평가에 **같은 시리즈**를 쓰므로
시리즈 안에 스며든 미래 참조를 잡지 못한다. `tests/test_regime_and_rs.py`가
'전체 데이터로 만든 시리즈의 t값 == t에서 잘라 계산한 값'을 검증해 이를 잠근다.

### RS는 유니버스 교차단면 순위다

`rs_percentile_frame()`이 날짜별로 유니버스 종목들의 RS 점수를 순위화한다.
정의 그대로 '같은 시점에 다른 종목들과 비교한 순위'다.

- 유니버스는 **시장별로 분리**한다 (`data/universes/us_large.txt`, `kospi.txt`).
  거래일이 다른 시장을 섞으면 서로 다른 날짜를 비교하게 된다.
- 구성종목이 `min_universe_size` 미만인 날짜는 백분위를 내지 않는다 (해상도 부족).
- 유니버스에 없는 종목은 `rs_percentile_against()`로 유니버스 분포 대비 순위를 낸다.
- **규약은 strictly-less 비율 하나다**: 백분위 = (자기보다 점수가 낮은 종목 수) / n * 100.
  구성종목 경로가 `rank(pct=True)`를 쓰면 최하위가 0이 아니라 100/n에서 시작해
  비구성종목 경로보다 계통적으로 후해지고, 게이트 경계(70/80)에 걸린 종목의 판정이
  '유니버스 소속 여부'로 뒤집힌다. `tests/test_universe.py`가 두 경로의 일치를 잠근다.
- **생존편향은 제거하지 못했다.** 목록이 현재 상장 종목만 담기 때문이다.
  `survivorship_warning()`을 리포트에 반드시 실을 것.

Phase 3까지 쓰던 지수 대비 근사(`approximate_rs_percentile_series`)는 **삭제했다**.
그것은 '자기 과거 대비 가속도'를 재서 꾸준히 시장을 이기는 종목이 중간 점수를 받았다.
두 가지 RS 개념을 남겨 두면 잘못된 쪽을 쓰는 사고가 나므로,
유니버스가 없으면 RS는 None이고 게이트는 UNAVAILABLE이다.

### look-ahead 방지는 두 겹이다

1. **구조적**: 시점 t의 `StockContext`에는 `df.iloc[:t+1]`만 들어간다. 미래 봉이
   객체 안에 없으므로 실수로는 볼 수 없다. `_assert_point_in_time()`이 매 호출 직전 재확인한다.
2. **행위 감사**: 미래를 보려면 `requires_full_history` 뒷문으로 주입받아야 하고,
   `audit_lookahead()`가 시점별 재현으로 이를 적발한다 — 전체 데이터가 있을 때와
   t에서 잘렸을 때의 판정이 다르면 미래를 쓴 것이다.

진입가는 **시그널 다음 봉 시가**다. 시그널 봉의 종가로 사는 것은 그 종가를 미리 아는 것이다.
진입 기준은 **verdict == BUY**다. 게이트 통과는 진입이 아니다 — WATCH/HOLD/AVOID를 매수로
집계하면 '전략 성과'가 아니라 '게이트 성과'를 재는 것이 된다. 하네스는 진입(BUY) /
통과-미진입 / 게이트 탈락 3집단을 분리 집계한다.

stage / regime / rs_percentile은 하네스가 계산하지 않는다. 백테스트에서 쓰려면 호출부가
`*_by_date` 매핑으로 주입하며, **주입 시리즈 자체가 시점별(point-in-time)로 계산된 것**이어야
한다. 감사는 주입 시리즈 안의 look-ahead까지는 잡지 못한다 (시리즈 산출 코드의 책임).

### 테스트는 네트워크를 타지 않는다

`tests/conftest.py`의 `_block_network`가 `yfinance.download`를 예외로 바꾼다.
실수로 네트워크를 타는 테스트가 들어오면 즉시 실패한다.
실제 수집 경로는 `tests/test_fetcher_network.py`에 격리되어 있고 `--run-network`로만 돈다.

픽스처 갱신이 필요할 때만 사람이 `python scripts/make_fixtures.py`를 실행한다.

## 아키텍처 원칙

### 1. 2단계 판정 구조: GATE → SCORE

- **GATE**는 이진 필터다. 추세 조건(200일선 위, Stage 2, RS 백분위 등)은 전부 여기 들어간다.
- 게이트 탈락 종목은 **점수를 계산하지 않고** 즉시 `REJECTED_BY_GATE`.
  `score=None`이지 `score=0.0`이 아니다. '채점 안 함'과 '낮은 점수'는 다르다.
- **SCORE**는 게이트를 통과한 종목의 진입 타이밍 품질만 채점한다.
- 추세 조건을 가산점 항목으로 옮기지 말 것. 이것이 이 프로젝트의 핵심 설계다.
- 이 순서는 `StrategyBase.evaluate()` 템플릿 메서드와 `StrategyVerdict`의
  `_gate_first_invariant` validator로 **구조적으로** 강제된다. 우회하지 말 것.
- `GateResult`는 `strategies/base.py`의 `build_gate_result()`로만 만든다. **UNAVAILABLE
  정책이 여기 하나로 고정된다**: UNAVAILABLE은 PASS로 세지 않으므로 AND 게이트를 막는다
  (확인 못 한 조건은 충족이 아니다). FAIL과의 구분은 `unavailable_count`로 보존된다.
- **셋업 판정(detect_setup / build_setup_metrics)은 채점이 아니다.** 게이트 탈락 종목에도
  수행한다. 근소 탈락(7/8) 종목이야말로 피벗 근접도를 보고 싶은 대상이다.
- 전략은 **결정론**이어야 한다: 같은 `StockContext`에는 항상 같은 판정. look-ahead 감사가
  같은 시점을 두 번 평가해 비교하므로, 호출 간 상태에 의존하면 감사가 깨지거나 무력화된다.

### 2. 전략은 플러그인

- 모든 전략은 `strategies/base.py`의 `Strategy` Protocol을 구현한다
  (실무에서는 `StrategyBase`를 상속한다).
- 전략 추가 시 코어(`core/`, `render/`, `main.py`) 수정이 필요하면 설계가 틀린 것이다.
- 전략끼리 서로를 import 하지 않는다. 공통 계산은 `indicators/` 또는 `StockContext`로.
- `StrategyBase.evaluate()`를 오버라이드하지 않는다. 하위 클래스는
  `build_gate` / `build_score` / `detect_setup` / `decide`만 구현한다.

### 3. 모든 임계값은 `config.py`의 dataclass

- 매직 넘버 하드코딩 금지. `200`, `0.25`, `70` 같은 값이 전략 파일에 리터럴로 있으면 안 된다.
- 백테스트 파라미터 스윕 대상이므로 `frozen=True` + `dataclasses.replace()`로 변형한다.
- 전략별 설정은 전략별 dataclass로 분리한다 (`MinerviniConfig`, `QullamaggieConfig`, ...).
- 전략은 자기 config만 받는다. `AppConfig` 전체를 전략에 넘기지 않는다.
- **전략 간 같아야 하는 값은 `AppConfig.__post_init__`이 강제한다.** 와인스타인의 Stage
  파라미터(150선·기울기 lookback·평탄 기준)는 `RegimeConfig`와 같은 값이어야 한다 —
  게이트가 쓰는 Stage는 주입된 값이고 신선도는 자기 config로 직접 세기 때문에, 스윕에서
  한쪽만 `replace()`하면 화면의 Stage와 점수가 세는 Stage 2가 조용히 갈라진다.
  그런 조합은 만들어지는 즉시 ValueError로 죽는다.

### 4. CLI와 프론트엔드는 동일한 JSON 계약을 공유

- 판정 로직은 **한 곳에만** 존재한다. 렌더러는 절대 판정하지 않는다.
- `core/types.py`의 `DiagnosisReport`가 유일한 계약이다.
- CLI = rich 렌더러, 프론트 = React 렌더러. 둘 다 같은 `DiagnosisReport`를 소비한다.
- 렌더러에 `if score > 70:` 같은 코드가 등장하면 로직이 샌 것이다. 전략으로 되돌린다.
- 직렬화는 `render/json_out.py`의 함수만 사용한다. 계약에 별칭(alias) 필드는
  하나도 없으므로 특수 인자는 필요 없지만, date/datetime 포맷과 enum 표현이
  CLI·API·목업 파일에서 갈라지지 않도록 진입점을 하나로 유지한다.

### 5. 외부 TA 라이브러리 금지

- `pandas_ta`, `TA-Lib`, `finta` 등 사용 금지. 지표는 `indicators/core.py`에 직접 구현한다.
- **모든 지표에 손계산 검증 유닛테스트를 붙인다.** 테스트 없는 지표는 머지하지 않는다.
- 기대값을 다른 라이브러리 출력에서 베껴오지 말 것. 그러면 이 원칙이 무의미해진다.
- **RSI는 Wilder smoothing**: seed는 첫 `period`개의 단순평균, 이후
  `(prev * (period-1) + cur) / period`. `ewm(span=14)` 기반 RSI가 **아니다**.
- **ATR도 Wilder 기준**. True Range = `max(H-L, |H-C_prev|, |L-C_prev|)`.
- MACD는 표준 EMA(12, 26, 9). 볼린저는 SMA20 ± 2σ (모집단 표준편차, `ddof=0`).

## 지표인가 셋업 수치인가

**두 전략이 다르게 계산할 수 있으면 지표가 아니다.**

- `IndicatorSnapshot`에는 계산식이 **하나뿐인** 것만 둔다:
  SMA / EMA / RSI / MACD / 볼린저 / ATR / ADR / 52주 고저 / 거래량 평균 / RS.
  누가 계산하든 같은 값이 나오므로 한 번 계산해 공유한다.
- 피벗 가격, 베이스 시작점, 베이스 깊이, 수축 정도처럼 **정의가 방법론마다 갈리는** 값은
  `StrategyVerdict.setup_metrics`(`SetupMetrics`)에 담는다. 판정 주체 옆에 둬야
  "미너비니의 피벗"과 "Qullamaggie의 피벗"이 각각 보존된다.
- 판단이 서면 `SetupMetrics`로 보낸다. 나중에 지표로 승격하는 것은 쉽지만,
  공유 지표에 잘못 넣어두면 두 전략이 조용히 같은 값을 강요당한다.

## 데이터 계약 규약

- `core/types.py`의 필드명/enum 값은 **그대로 JSON 키/값**이다. 변경 = breaking change.
  변경 시 `SCHEMA_VERSION`을 올리고 프론트에 알린다.
- 모든 계약 모델은 `Contract`를 상속한다 (`extra="forbid"`, `frozen=True`).
- **`None`과 `0.0`은 다르다.** 계산 불가는 반드시 `None`. 0으로 채우지 말 것.
- 지표가 `None`이면 그것을 쓰는 `GateCheck`는 **`UNAVAILABLE`**이어야 한다.
  `FAIL`로 처리하면 신규 상장주가 조용히 탈락하고, 사용자는 이유를 모른다.
- 퍼센트는 **0~100 스케일**로 통일 (15% = `15.0`, `0.15` 아님).
- **숫자 필드 접미사 규약** — 스케일이 이름에 드러나야 한다:
  - `_pct` = 0~100 스케일 백분율. 예: `atr_pct`, `base_depth_pct`, `sma200_slope_20d_pct`
  - `_ratio` = 배수. 1.0이 기준. 예: `volume_ratio`, `contraction_ratio`, `volume_dryup_ratio`
  - `_percentile` = 0~100 순위. 예: `rs_percentile`
  - 비율·비례를 나타내면서 접미사가 없는 필드는 금지. `slope`, `depth`, `dryup` 처럼
    맨 이름만 쓰면 읽는 쪽이 0.15인지 15.0인지 알 수 없다.
  - 절대 가격/금액/개수는 예외다 (`sma200`, `atr14`, `volume`, `base_length_days`).
    통화·주식수·일수는 스케일이 자명하다.
  - VCP 수축도와 거래량 건조도는 `_ratio`다 (`contraction_ratio`, `volume_dryup_ratio`).
- 가격/금액은 `float`. 통화 변환은 하지 않는다 (티커 상장 통화 그대로).
- enum은 이름과 값을 일치시킨다 (`RISK_ON = "RISK_ON"`). 계약 테스트가 이를 잠근다.
- `ConsensusSummary`의 필드는 전부 `strategy_verdicts`의 **중복 저장**이며, DiagnosisReport
  validator가 원본과의 일치를 전부 강제한다 (`verdict_counts` / `buy_strategies` /
  `gate_passed_strategies` / `gate_progress`·`progress_ratio` / `agreement`).
  `agreement`의 분모는 전체 전략 수다 — 게이트 탈락도 '비-BUY 의견'으로 센다.
- **미확정 필드를 계약에 넣지 않는다.** 소속 위치나 의미가 아직 정해지지 않았으면
  넣지 말고, 확정되는 Phase에서 `SCHEMA_VERSION`을 올리며 추가한다.
  (예: `PositionState`는 enum만 정의되어 있고 이를 참조하는 필드는 없다.)
- **아직 채워지지 않는 필드에는 그 사실을 주석으로 못 박는다.** `rs_line_new_high`는
  산출 함수는 있지만 호출부가 없어 항상 None이며, CANSLIM Phase에서 연결한다.
  `risk_plans`는 Phase 5에서 채워지기 시작했으나, 진입 의사가 있는 판정이 없으면
  빈 dict다 (없는 것과 '아직 구현 안 된 것'은 다르다). '언젠가 채워지겠지'로 두면
  프론트가 값이 오는 줄 알고 UI를 만든다.

## `is_bar_complete` 규칙

장중 실행 시 당일 봉은 미완성이다. 이때:

- `bar_meta.is_bar_complete = False`, 최상위 `is_bar_complete`도 미러링(validator가 강제).
- `WarningCode.INCOMPLETE_BAR` 경고를 **반드시** 추가한다. 없으면 `DiagnosisReport`
  생성 자체가 `ValidationError`로 실패한다.
- `bar_meta.volume_judgements_reliable = False`로 두고, 거래량 기반 조건
  (돌파 거래량, 볼륨 드라이업)은 **`UNAVAILABLE`**로 처리한다.
  미완성 봉의 누적 거래량으로 "거래량 부족" 판정을 내리면 명백한 오진이다.
- 종가 기반 조건(200일선 돌파 등)도 확정이 아니므로 `notes`에 명시한다.

## 디렉토리 구조

| 경로 | 책임 |
|---|---|
| `config.py` | 모든 임계값 dataclass |
| `core/types.py` | 데이터 계약. 로직 없음 |
| `core/context.py` | `StockContext` — 지표 계산이 끝난 상태 객체 |
| `core/report.py` | 판정 목록 -> `DiagnosisReport` 조립. 컨센서스 파생의 단일 지점 |
| `data/fetcher.py` | yfinance 3년치 OHLCV + parquet 캐시 |
| `data/universe.py` | 유니버스 교차단면 RS 백분위 |
| `data/universes/*.txt` | 유니버스 구성 종목 목록. 생존편향 경고 포함 |
| `indicators/core.py` | SMA/EMA/RSI/MACD/BB/ATR + 프랙탈 스윙 고저 직접 구현 |
| `strategies/base.py` | `Strategy` Protocol + `StrategyBase` 템플릿 |
| `strategies/*.py` | 전략별 GATE/SCORE 구현 |
| `regime/market.py` | 시장 국면 판정 |
| `risk/planner.py` | 손절 / 포지션 사이징 / R-multiple. 전략별 플랜 |
| `strategies/registry.py` | 전략 이름 -> 팩토리. 전략 목록의 단일 출처 |
| `main.py` | CLI + `diagnose()` / `scan()` — 파이프라인의 유일한 오케스트레이션 지점 |
| `core/watchlist.py` | 진단 목록 -> `WatchlistReport` 조립 + 정렬 규약 |
| `backtest/harness.py` | 과거 시점 재현 검증 러너 |
| `backtest/sweep.py` | 파라미터 스윕 + 학습/홀드아웃 분할 |
| `render/cli.py` | rich 렌더러 |
| `render/json_out.py` | 프론트엔드용 직렬화 (유일한 진입점) |
| `strategies/dummy.py` | 하네스 검증용 더미 3종. 매매 판단용이 아니다 |
| `examples/*.json` | **실제 전략 출력**으로 생성한 예시 리포트 (`scripts/make_examples.py`) |
| `docs/review_phase0-2.md` | Phase 0~2 비판적 리뷰와 조치 내역 |
| `docs/review_phase3-4.md` | Phase 3~4 비판적 리뷰와 조치 내역 |
| `docs/phase6_sweep.md` | 스윕·홀드아웃 측정 결과. 세 질문에 대한 답과 한계 |

## 코딩 컨벤션

- Python 3.12+. `from __future__ import annotations` 항상.
- 타입 힌트 필수. `dict`/`list` 소문자 제네릭, `X | None` (`Optional` 안 씀).
- pandas: `df["col"]` 사용, `df.col` 금지. `SettingWithCopyWarning` 나오면 `.copy()`.
- 시계열은 항상 **오름차순 정렬**(과거→현재)이고 `DatetimeIndex`다. 함수는 이를 전제한다.
- OHLCV 컬럼명은 소문자 `open/high/low/close/volume`로 정규화한다.
- 지표 함수는 입력 Series와 **같은 길이**의 Series를 반환한다. 워밍업 구간은 `NaN`.
  잘라서 반환하지 말 것 (인덱스 정렬이 깨진다).
- 네이밍: 지표 함수는 `sma(series, period)` 형태. 계약 필드는 `sma200` 형태.
- 예외: 데이터 문제는 `data/` 레이어에서 도메인 예외(`DataError` 계열)로 변환한다.
  전략에서 raw `KeyError`나 yfinance 예외가 올라오면 안 된다.
- 테스트: `tests/test_<module>.py`. 지표 테스트는 손계산 기대값을 상수로 박고 출처 주석을 단다.
- line-length 100, ruff 설정은 `pyproject.toml`.

## 하지 말 것

- ❌ 전략 점수를 평균/합산해 단일 "종합 점수"를 만들지 말 것. 판정은 끝까지 분리 보존.
  `ConsensusSummary`에 평균 필드를 추가하지 말 것 (테스트가 막고 있다).
- ❌ 추세 조건을 SCORE 가산점으로 옮기지 말 것. 게이트에 남긴다.
- ❌ 게이트 탈락 종목의 점수를 계산하지 말 것.
- ❌ `StrategyBase.evaluate()`를 오버라이드하지 말 것.
- ❌ `pandas_ta` / `TA-Lib` / `finta` 등 외부 TA 라이브러리 import 금지.
- ❌ 렌더러(`render/`)에서 판정하지 말 것. 임계값 비교 코드가 있으면 잘못된 것.
- ❌ `model_dump()` / `model_dump_json()`을 `render/json_out.py` 밖에서 호출하지 말 것
  (계약 테스트는 직렬화 형태 자체가 대상이므로 예외).
- ❌ `CheckStatus`를 bool로 접어서 저장하지 말 것. UNAVAILABLE이 FAIL로 둔갑한다.
- ❌ 전략마다 정의가 다른 값을 `IndicatorSnapshot`에 넣지 말 것.
- ❌ 임계값 하드코딩 금지. 전부 `config.py`.
- ❌ 계산 불가를 `0.0`이나 `False`로 채우지 말 것. `None` / `UNAVAILABLE`.
- ❌ 미완성 봉의 거래량으로 거래량 조건을 판정하지 말 것.
- ❌ 돌파(BREAKOUT) 상태에서 거래량 확인 없이 BUY를 내지 말 것. 확인 불가도 BUY가 아니다.
- ❌ `examples/*.json`을 손으로 고치지 말 것. `scripts/make_examples.py`로 재생성한다.
- ❌ config에 있는 임계값을 코드가 소비하지 않은 채 두지 말 것. 스윕 대상 파라미터가
  무엇을 돌려도 결과가 같으면 그 config는 거짓말이다.
- ❌ 홀드아웃 성과를 보고 파라미터를 고르지 말 것. 그 순간 홀드아웃이 아니게 된다.
- ❌ 스윕 결과를 config에 자동 반영하지 말 것. 측정과 결정은 분리한다.
- ❌ 미래 데이터 참조(look-ahead) 금지. 백테스트에서 `t` 시점 판정에 `t+1` 봉 사용 금지.
- ❌ 네트워크 호출을 지표/전략 레이어에서 하지 말 것. 데이터는 `data/`에서만.
- ❌ 테스트 없는 지표 추가 금지.
- ❌ 재무 조언 문구를 출력하지 말 것. 진단과 근거만 제시한다.

## 자주 쓰는 명령

```bash
python -m pytest tests/ -q
```

```bash
python main.py AAPL --equity 100000
```

```bash
python main.py --scan us_large --top 20
```

```bash
python scripts/verify_phase7.py
```

```bash
python scripts/verify_phase6.py
```

```bash
python scripts/verify_phase5.py
```

```bash
python scripts/verify_phase4.py
```

```bash
python scripts/make_examples.py
```

```bash
python scripts/verify_phase3_5.py
```

```bash
python -m pytest tests/test_types_contract.py -v
```

```bash
python -c "import json, core.types as t; print(json.dumps(t.DiagnosisReport.model_json_schema(), indent=2, ensure_ascii=False))"
```

계약이나 전략을 바꾸면 `python scripts/make_examples.py`로 `examples/`를 재생성한다.
`tests/test_examples.py`가 같은 (티커, as_of)를 다시 평가해 파일과 대조하므로,
재생성을 잊으면 테스트가 실패하며 무엇을 해야 하는지 알려 준다.
**예시를 손으로 고치지 말 것** — 손 목업이 구현을 못 따라가는 것이 Phase 3~4에서
두 번 증명됐고, 스키마 검증은 '형태는 맞지만 내용이 거짓인' 파일을 잡지 못한다.
