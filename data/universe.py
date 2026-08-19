"""유니버스 기반 상대강도(RS) 백분위.

RS 백분위의 정의는 **'같은 시점에 유니버스 내 다른 종목들과 비교한 순위'**다.
Phase 3.5에서 유니버스를 갖추면서 이 정의대로 계산한다.

## 산출 절차

1. 유니버스 종목들의 종가 행렬 (날짜 x 티커)
2. 각 종목·각 날짜의 RS 점수 = 기간별 수익률 가중합 (IBD 방식, 최근 분기에 큰 가중치)
3. **날짜별 교차단면 순위** = 그 날짜의 점수들을 종목 간에 순위화 -> 0~100

3번이 Phase 3까지 쓰던 근사와 결정적으로 다르다. 근사는 자기 과거와 비교해
'상대강도의 가속도'를 쟀지만, 이것은 다른 종목과 비교해 '상대강도의 수준'을 잰다.
꾸준히 시장을 이기는 종목이 이제 제대로 상위권을 받는다.
(그 근사 함수는 Phase 3.5에서 제거했다 — 두 가지 RS 개념을 남겨 두면 잘못된 쪽을
쓰는 사고가 난다. 유니버스가 없으면 RS는 None이고 게이트는 UNAVAILABLE이다.)

## 시점 정합성

날짜 t의 백분위는 t 시점 각 종목의 점수만으로 계산되고, 각 점수는 t 이하 종가만
쓴다. 따라서 정의상 후방 참조뿐이다. 백테스트 하네스는 이 시리즈를 주입받으며,
하네스의 look-ahead 감사는 시리즈 안의 미래 참조를 잡지 못하므로 시점 정합성은
이 모듈의 책임이다. `tests/test_universe.py`가 이를 잠근다.

## 생존편향 — 제거하지 못했다

유니버스 목록(`data/universes/*.txt`)은 **현재 상장 중인** 종목만 담는다.
과거 시점의 실제 구성종목이 아니므로, 그때 존재했다가 사라진 종목이 분모에서 빠진다.
살아남은 종목끼리만 순위를 매기면 백분위가 낙관 쪽으로 치우친다.
없애려면 시점별 구성종목 데이터베이스가 필요하고 그것은 이 프로젝트 범위 밖이다.
리포트는 이 사실을 항상 함께 출력한다 (`survivorship_warning()`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from config import RegimeConfig, UniverseConfig
from core.types import DiagnosticWarning, Severity, WarningCode

UNIVERSE_DIR = Path(__file__).resolve().parent / "universes"


# ---------------------------------------------------------------------------
# 유니버스 목록
# ---------------------------------------------------------------------------


def universe_path(name: str) -> Path:
    return UNIVERSE_DIR / f"{name}.txt"


def load_universe_tickers(name: str) -> list[str]:
    """유니버스 구성 종목 목록. '#' 주석과 빈 줄은 무시한다."""
    path = universe_path(name)
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in UNIVERSE_DIR.glob("*.txt")))
        raise FileNotFoundError(f"유니버스 목록이 없다: {path.name} (사용 가능: {available})")

    return [
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def benchmark_for(ticker: str, regime_config: RegimeConfig, exchange: str | None) -> str:
    """종목이 속한 시장의 벤치마크 지수 티커."""
    return dict(regime_config.benchmark_by_exchange).get(
        exchange or "", regime_config.default_benchmark
    )


def universe_for(exchange: str | None, config: UniverseConfig) -> str:
    """거래소에 대응하는 유니버스 이름."""
    return dict(config.universe_by_exchange).get(exchange or "", config.default_universe)


# ---------------------------------------------------------------------------
# RS 점수 (종목별 시계열)
# ---------------------------------------------------------------------------


def rs_score(closes: pd.Series, config: UniverseConfig) -> pd.Series:
    """한 종목의 RS 원점수 시계열. 기간별 수익률의 가중합.

    가중치는 config.rs_weights (기본 0.4/0.2/0.2/0.2)로 최근 구간에 더 큰 값을 준다.
    구간은 rs_lookback_days를 가중치 개수로 등분한다 (252일 -> 63/126/189/252).

    한 구간이라도 계산 불가면 그 날짜는 NaN이다. 일부 구간만으로 점수를 만들면
    상장 기간이 짧은 종목이 유리해지는 역전이 생긴다.
    """
    periods = len(config.rs_weights)
    step = max(1, config.rs_lookback_days // periods)
    total = pd.Series(0.0, index=closes.index)
    usable = pd.Series(True, index=closes.index)

    for i, weight in enumerate(config.rs_weights, start=1):
        past = closes.shift(step * i)
        change = (closes - past) / past * 100.0
        usable &= change.notna()
        total = total + weight * change.fillna(0.0)

    return total.where(usable)


def rs_score_frame(closes: pd.DataFrame, config: UniverseConfig) -> pd.DataFrame:
    """유니버스 전체의 RS 원점수. 반환: 날짜 x 티커."""
    return closes.apply(lambda column: rs_score(column, config))


# ---------------------------------------------------------------------------
# 교차단면 백분위
# ---------------------------------------------------------------------------


def rs_percentile_frame(scores: pd.DataFrame, config: UniverseConfig) -> pd.DataFrame:
    """날짜별 교차단면 순위 -> 0~100.

    그 날짜에 점수가 있는 종목이 min_universe_size 미만이면 전부 NaN이다.
    표본이 적으면 백분위 해상도가 떨어지는데, 그것을 그럴듯한 숫자로 내보내면
    게이트 임계값(>= 70)의 의미가 조용히 달라진다.
    """
    available = scores.notna().sum(axis=1)
    ranked = scores.rank(axis=1, pct=True) * 100.0
    return ranked.where(available >= config.min_universe_size)


def rs_percentile_series(ticker: str, percentiles: pd.DataFrame) -> dict[date, float]:
    """유니버스 구성종목의 백분위 시계열. 값이 없는 날짜는 키 자체가 없다."""
    if ticker not in percentiles.columns:
        return {}
    return {
        timestamp.date(): float(value)
        for timestamp, value in percentiles[ticker].items()
        if pd.notna(value)
    }


def rs_percentile_against(
    closes: pd.Series, universe_scores: pd.DataFrame, config: UniverseConfig
) -> dict[date, float]:
    """유니버스에 **속하지 않은** 종목의 백분위.

    종목의 RS 점수를 구한 뒤, 각 날짜의 유니버스 점수 분포에서 그 점수가 차지하는
    위치를 찾는다. 구성종목이 아니어도 '유니버스 대비 순위'라는 정의는 그대로
    성립한다 — 자기 과거와 비교하는 것이 아니라 다른 종목들과 비교하기 때문이다.
    """
    scores = rs_score(closes, config).reindex(universe_scores.index)
    available = universe_scores.notna().sum(axis=1)

    out: dict[date, float] = {}
    for timestamp, score in scores.items():
        if pd.isna(score) or available.get(timestamp, 0) < config.min_universe_size:
            continue
        row = universe_scores.loc[timestamp].dropna()
        out[timestamp.date()] = float((row < score).sum()) / len(row) * 100.0
    return out


def rs_line_new_high_series(
    stock_close: pd.Series, benchmark_close: pd.Series, config: UniverseConfig
) -> dict[date, bool]:
    """RS 라인(종목/벤치마크)이 lookback 구간 신고가인지.

    백분위와 달리 유니버스가 필요 없다 — 벤치마크 하나와의 비율이면 정의가 완결된다.
    """
    aligned = pd.concat([stock_close, benchmark_close], axis=1, join="inner")
    aligned.columns = ["stock", "benchmark"]
    line = aligned["stock"] / aligned["benchmark"]
    peak = line.rolling(
        window=config.rs_lookback_days, min_periods=config.rs_lookback_days
    ).max()
    return {
        timestamp.date(): bool(value >= high)
        for timestamp, value, high in zip(line.index, line, peak, strict=True)
        if pd.notna(high)
    }


# ---------------------------------------------------------------------------
# 경고 — 숫자와 항상 함께 다녀야 한다
# ---------------------------------------------------------------------------


def survivorship_warning(universe_name: str, size: int) -> DiagnosticWarning:
    """유니버스가 생존편향을 갖는다는 사실."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.INFO,
        message=(
            f"RS 백분위가 '{universe_name}' 유니버스 {size}종목 대비 순위다. "
            "이 목록은 현재 상장 중인 종목만 담고 있어 과거 시점의 실제 구성종목이 아니다 — "
            "그때 존재했다 사라진 종목이 분모에서 빠지므로 백분위가 낙관 쪽으로 치우친다. "
            "시점별 구성종목 데이터가 없는 한 이 편향은 제거되지 않는다"
        ),
        field="indicators.rs_percentile",
    )


def small_universe_warning(size: int, minimum: int) -> DiagnosticWarning:
    """유니버스가 작아 백분위 해상도가 낮다는 경고."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.WARN,
        message=(
            f"유니버스가 {size}종목으로 기준 {minimum}종목에 가깝다. 백분위 해상도가 "
            f"약 {100.0 / max(size, 1):.1f}p 단위이므로 임계값 부근의 판정이 거칠어진다"
        ),
        field="indicators.rs_percentile",
    )
