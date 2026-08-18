# trading-tracker

티커를 입력받아 여러 추세추종 방법론(미너비니 SEPA, 와인스타인 Stage Analysis,
오닐 CANSLIM, Qullamaggie)으로 **각각 독립 평가**하고 판정을 나란히 보여주는 진단 시스템.

매매를 자동 실행하지 않는다. 진단과 근거 제시까지가 범위다.

## 현재 상태 (2026-08-18)

스캐폴딩 + 데이터 계약만 확정된 상태다.

- **구현 완료**: `core/types.py` (전체 데이터 계약), `config.py` (임계값 dataclass),
  `tests/test_types_contract.py` + `tests/test_examples.py` (계약 불변식 91개 테스트),
  `examples/` (프론트엔드용 목업 JSON 3종)
- **시그니처만**: 그 외 모든 모듈. 본문은 `raise NotImplementedError`.
- **미설치 의존성**: `pandas`, `yfinance`, `pyarrow`, `rich`. 설치 전에는
  `core.types` / `config` 외 모듈은 import되지 않는다. `pip install -r requirements.txt`.

## 아키텍처 원칙

### 1. 2단계 판정 구조: GATE → SCORE

- **GATE**는 이진 필터다. 추세 조건(200일선 위, Stage 2, RS 백분위 등)은 전부 여기 들어간다.
- 게이트 탈락 종목은 **점수를 계산하지 않고** 즉시 `REJECTED_BY_GATE`.
  `score=None`이지 `score=0.0`이 아니다. '채점 안 함'과 '낮은 점수'는 다르다.
- **SCORE**는 게이트를 통과한 종목의 진입 타이밍 품질만 채점한다.
- 추세 조건을 가산점 항목으로 옮기지 말 것. 이것이 이 프로젝트의 핵심 설계다.
- 이 순서는 `StrategyBase.evaluate()` 템플릿 메서드와 `StrategyVerdict`의
  `_gate_first_invariant` validator로 **구조적으로** 강제된다. 우회하지 말 것.

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
| `data/universe.py` | RS 백분위 계산용 유니버스 |
| `indicators/core.py` | SMA/EMA/RSI/MACD/BB/ATR 직접 구현 |
| `strategies/base.py` | `Strategy` Protocol + `StrategyBase` 템플릿 |
| `strategies/*.py` | 전략별 GATE/SCORE 구현 |
| `regime/market.py` | 시장 국면 판정 |
| `risk/planner.py` | 손절 / 포지션 사이징 / R-multiple |
| `backtest/harness.py` | 과거 시점 재현 검증 러너 |
| `render/cli.py` | rich 렌더러 |
| `render/json_out.py` | 프론트엔드용 직렬화 (유일한 진입점) |
| `examples/*.json` | 손으로 채운 목업 리포트. 계약 변경 시 여기가 먼저 깨진다 |

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
python -m pytest tests/test_types_contract.py -v
```

```bash
python -c "import json, core.types as t; print(json.dumps(t.DiagnosisReport.model_json_schema(), indent=2, ensure_ascii=False))"
```

계약을 바꾸면 `examples/`의 목업 3개도 함께 고쳐야 한다. `tests/test_examples.py`가 막아준다.
