# trading-tracker

티커를 입력받아 여러 추세추종 방법론(미너비니 SEPA, 와인스타인 Stage Analysis,
오닐 CANSLIM, Qullamaggie)으로 **각각 독립 평가**하고 판정을 나란히 보여주는 진단 시스템.

매매를 자동 실행하지 않는다. 진단과 근거 제시까지가 범위다.

## 현재 상태 (Phase 4 완료)

계약(v1.2.0) + 데이터 + 지표 + 하네스 + **전략 3종** + 유니버스 RS까지 구현됐다.

- **Phase 0 (계약)**: `core/types.py`, `config.py`, `examples/` 목업 3종
- **Phase 1 (데이터·지표)**: `indicators/core.py`, `indicators/snapshot.py`,
  `data/fetcher.py`, 고정 CSV 픽스처
- **Phase 2 (하네스)**: `backtest/harness.py`, `strategies/base.py`(템플릿 메서드),
  `strategies/dummy.py`(검증용 더미 3종), `core/context.py`
- **Phase 3 (전략)**: `strategies/minervini.py`, `regime/market.py`
- **Phase 3.5 (유니버스)**: `data/universes/*.txt` 목록, 교차단면 RS 백분위,
  다종목 패널 집계(`evaluate_panel`), 지표 사전 계산
- **Phase 4 (전략 2종 추가)**: `strategies/weinstein.py`, `strategies/qullamaggie.py`,
  스키마 1.2.0(`WeinsteinSetup` / `QullamaggieSetup`), 프랙탈 스윙 지표 공유
- **미구현**: CANSLIM(재무 데이터 필요), `risk/planner.py`, `render/`,
  `main.py`의 진단 파이프라인
- `main.py`는 인자 파싱까지만 동작한다 (`--help` 정상, 실제 진단은 exit 2)

### 전략마다 게이트의 축이 다르다

세 전략의 게이트는 조건 수도 내용도 다르다. **같은 질문을 세 번 하는 게이트가 아니라
서로 다른 질문을 하는 게이트**여야 판정이 갈리고, 판정이 갈려야 나란히 둘 이유가 있다.

| 전략 | 게이트가 묻는 것 | 조건 수 |
|---|---|---|
| minervini | 이동평균 8개 조건이 정렬됐는가 | 8 |
| weinstein | Stage 2인가 + 10주선/국면/RS/유동성 | 5 |
| qullamaggie | 직전 급등이 있었는가 + ADR/거래대금/RS/20일선 | 5 |

`us_large` 패널 실측 진입률은 minervini 0.5% / weinstein 8.7% / qullamaggie 0.3%다.
게이트가 자주 열리는 전략은 무조건부 매수에 수렴하며, 그때 초과수익이 0 근처로
나오는 것은 결함이 아니라 정합성 신호다 (와인스타인 20봉 초과수익 -0.28%p).

**함의 관계인 조건을 중복해서 세지 말 것.** 와인스타인 게이트에 '30주선 위'와
'30주선 상승'을 다시 넣으면 Stage 2가 이미 함의하는 사실을 세 번 세게 되어
`pass_count/total` 진행률이 부풀고 워치리스트 근접도 정렬이 낙관 쪽으로 틀어진다.
`tests/test_strategies_weinstein.py`가 이 설계를 잠근다.

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
US 29종목 풀링으로 진입 320건, KOSPI 10종목으로 74건이 모인다.

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
| `data/fetcher.py` | yfinance 3년치 OHLCV + parquet 캐시 |
| `data/universe.py` | 유니버스 교차단면 RS 백분위 |
| `data/universes/*.txt` | 유니버스 구성 종목 목록. 생존편향 경고 포함 |
| `indicators/core.py` | SMA/EMA/RSI/MACD/BB/ATR + 프랙탈 스윙 고저 직접 구현 |
| `strategies/base.py` | `Strategy` Protocol + `StrategyBase` 템플릿 |
| `strategies/*.py` | 전략별 GATE/SCORE 구현 |
| `regime/market.py` | 시장 국면 판정 |
| `risk/planner.py` | 손절 / 포지션 사이징 / R-multiple |
| `backtest/harness.py` | 과거 시점 재현 검증 러너 |
| `render/cli.py` | rich 렌더러 |
| `render/json_out.py` | 프론트엔드용 직렬화 (유일한 진입점) |
| `strategies/dummy.py` | 하네스 검증용 더미 3종. 매매 판단용이 아니다 |
| `examples/*.json` | 손으로 채운 목업 리포트. 계약 변경 시 여기가 먼저 깨진다 |
| `docs/review_phase0-2.md` | Phase 0~2 비판적 리뷰와 조치 내역 |

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
- ❌ 미래 데이터 참조(look-ahead) 금지. 백테스트에서 `t` 시점 판정에 `t+1` 봉 사용 금지.
- ❌ 네트워크 호출을 지표/전략 레이어에서 하지 말 것. 데이터는 `data/`에서만.
- ❌ 테스트 없는 지표 추가 금지.
- ❌ 재무 조언 문구를 출력하지 말 것. 진단과 근거만 제시한다.

## 자주 쓰는 명령

```bash
python -m pytest tests/ -q
```

```bash
python scripts/verify_phase4.py
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

계약을 바꾸면 `examples/`의 목업 3개도 함께 고쳐야 한다. `tests/test_examples.py`가 막아준다.
