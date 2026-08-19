"""시점별 주입 시리즈(regime / stage / RS) 테스트.

**이 파일에서 가장 중요한 것은 point-in-time 검증이다.**

백테스트 하네스는 이 시리즈들을 그대로 주입받아 쓰고, look-ahead 감사는 양쪽 평가에
같은 시리즈를 쓰기 때문에 **시리즈 안에 스며든 미래 참조를 잡지 못한다**.
즉 시점 정합성은 전적으로 이 모듈들의 책임이며, 그것을 잠그는 것이 여기다.

검증 방식: 전체 데이터로 만든 시리즈의 날짜 t 값이, t에서 잘라낸 데이터로 계산한
값과 같아야 한다. 다르면 t 이후 데이터가 t의 값에 영향을 준 것이다.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from config import DEFAULT_CONFIG
from core.types import MarketRegime, Stage
from data.universe import (
    approximate_rs_percentile_series,
    benchmark_for,
    relative_strength_line,
    rs_line_new_high_series,
    rs_universe_warning,
)
from regime.market import (
    classify_regime,
    classify_stage,
    count_distribution_days,
    distribution_day_flags,
    regime_series,
    stage_series,
)

REGIME = DEFAULT_CONFIG.regime
UNIVERSE = DEFAULT_CONFIG.universe


def frame(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=len(closes))
    volumes = volumes or [1_000_000.0] * len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


@pytest.fixture
def spy() -> pd.DataFrame:
    from tests.conftest import load_fixture

    return load_fixture("SPY_3y.csv")


@pytest.fixture
def kospi() -> pd.DataFrame:
    from tests.conftest import load_fixture

    return load_fixture("^KS11_3y.csv")


# ===========================================================================
# 시점 정합성 — 이 파일의 핵심
# ===========================================================================


@pytest.mark.parametrize("position", [300, 450, 600, 700])
def test_regime_series_value_matches_a_truncated_recomputation(spy, position):
    """날짜 t의 국면이 t에서 잘라낸 데이터로 계산한 값과 같아야 한다."""
    series = regime_series(spy, REGIME)
    truncated = spy.iloc[: position + 1]
    as_of = truncated.index[-1].date()
    assert series[as_of] is classify_regime(truncated, REGIME)


@pytest.mark.parametrize("position", [300, 450, 600, 700])
def test_stage_series_value_matches_a_truncated_recomputation(spy, position):
    series = stage_series(spy, REGIME)
    truncated = spy.iloc[: position + 1]
    as_of = truncated.index[-1].date()
    assert series[as_of] is classify_stage(truncated, REGIME)


@pytest.mark.parametrize("position", [500, 600, 700])
def test_rs_series_value_matches_a_truncated_recomputation(aapl, spy, position):
    """RS도 마찬가지다. 여기가 새면 백테스트 전체가 조용히 오염된다."""
    full = approximate_rs_percentile_series(aapl["close"], spy["close"], UNIVERSE)
    stock_cut = aapl.iloc[: position + 1]
    as_of = stock_cut.index[-1].date()
    truncated = approximate_rs_percentile_series(
        stock_cut["close"], spy.loc[: stock_cut.index[-1], "close"], UNIVERSE
    )
    assert as_of in truncated
    assert truncated[as_of] == pytest.approx(full[as_of])


def test_rs_new_high_series_is_point_in_time(aapl, spy):
    full = rs_line_new_high_series(aapl["close"], spy["close"], UNIVERSE)
    cut = aapl.iloc[:600]
    as_of = cut.index[-1].date()
    truncated = rs_line_new_high_series(
        cut["close"], spy.loc[: cut.index[-1], "close"], UNIVERSE
    )
    assert truncated[as_of] == full[as_of]


def test_appending_future_bars_does_not_change_past_regime(spy):
    """미래 봉을 덧붙여도 과거 날짜의 값이 바뀌면 안 된다."""
    early = regime_series(spy.iloc[:500], REGIME)
    full = regime_series(spy, REGIME)
    for as_of, value in early.items():
        assert full[as_of] is value


# ===========================================================================
# 국면 판정 규칙
# ===========================================================================


def test_index_below_sma200_is_risk_off():
    """지수가 200일선 아래면 다른 조건과 무관하게 RISK_OFF."""
    closes = [300.0 - i * 0.5 for i in range(260)]
    assert classify_regime(frame(closes), REGIME) is MarketRegime.RISK_OFF


def test_rising_index_above_sma200_is_risk_on():
    closes = [100.0 + i * 0.5 for i in range(300)]
    assert classify_regime(frame(closes), REGIME) is MarketRegime.RISK_ON


def test_insufficient_history_is_caution_not_risk_on():
    """근거가 없으면 낙관하지 않는다. 200일선이 없으면 CAUTION이다."""
    assert classify_regime(frame([100.0] * 50), REGIME) is MarketRegime.CAUTION


def test_distribution_days_downgrade_risk_on_to_caution():
    """상승 추세여도 분산일이 쌓이면 CAUTION으로 내려간다."""
    closes = [100.0 + i * 0.5 for i in range(300)]
    volumes = [1_000_000.0] * 300
    # 마지막 구간에 '거래량 증가 + 하락' 분산일을 심는다
    for offset in range(1, 14, 2):
        closes[-offset] = closes[-offset - 1] * 0.99
        volumes[-offset] = 5_000_000.0
    df = frame(closes, volumes)
    assert count_distribution_days(df, REGIME) > REGIME.caution_max_distribution_days
    assert classify_regime(df, REGIME) is MarketRegime.CAUTION


def test_distribution_day_requires_both_drop_and_volume_increase():
    """하락만으로는, 또는 거래량 증가만으로는 분산일이 아니다."""
    closes = [100.0, 99.0, 98.0, 97.0]
    quiet = frame(closes, [1_000_000.0, 900_000.0, 800_000.0, 700_000.0])
    assert not distribution_day_flags(quiet, REGIME).any()

    loud = frame(closes, [1_000_000.0, 2_000_000.0, 3_000_000.0, 4_000_000.0])
    assert distribution_day_flags(loud, REGIME).sum() == 3


def test_regime_thresholds_come_from_config():
    closes = [100.0 + i * 0.5 for i in range(300)]
    df = frame(closes)
    strict = replace(REGIME, risk_on_min_sma200_slope_pct=999.0)
    assert classify_regime(df, REGIME) is MarketRegime.RISK_ON
    assert classify_regime(df, strict) is MarketRegime.CAUTION


# ===========================================================================
# Stage 판정
# ===========================================================================


def test_rising_price_above_ma_is_stage_2():
    closes = [100.0 + i * 0.5 for i in range(250)]
    assert classify_stage(frame(closes), REGIME) is Stage.STAGE_2


def test_falling_price_below_ma_is_stage_4():
    closes = [300.0 - i * 0.5 for i in range(250)]
    assert classify_stage(frame(closes), REGIME) is Stage.STAGE_4


def test_flat_market_is_not_stage_2_or_4():
    """평탄한 이동평균은 상승도 하락도 아니다 — 1 또는 3이다."""
    closes = [100.0] * 250
    assert classify_stage(frame(closes), REGIME) in (Stage.STAGE_1, Stage.STAGE_3)


def test_short_history_is_undefined_not_guessed():
    assert classify_stage(frame([100.0] * 40), REGIME) is Stage.UNDEFINED


def test_stage_series_covers_every_bar(aapl):
    series = stage_series(aapl, REGIME)
    assert len(series) == len(aapl)
    assert all(isinstance(v, Stage) for v in series.values())


# ===========================================================================
# RS 근사 — 근사임을 드러내는지
# ===========================================================================


def test_rs_line_uses_only_common_trading_days(aapl, kospi):
    """미국 종목과 한국 지수처럼 거래일이 어긋나면 교집합만 쓴다."""
    line = relative_strength_line(aapl["close"], kospi["close"])
    assert len(line) <= min(len(aapl), len(kospi))
    assert line.notna().all()


def test_rs_percentile_is_bounded(aapl, spy):
    values = approximate_rs_percentile_series(aapl["close"], spy["close"], UNIVERSE).values()
    assert values
    assert all(0.0 <= v <= 100.0 for v in values)


def test_rs_is_unavailable_before_enough_history(aapl, spy):
    """순위를 낼 만큼 과거가 없으면 날짜 자체가 없다 — 0으로 채우지 않는다."""
    series = approximate_rs_percentile_series(aapl["close"], spy["close"], UNIVERSE)
    early_dates = [ts.date() for ts in aapl.index[:100]]
    assert not any(d in series for d in early_dates)


def test_accelerating_relative_strength_ranks_higher(spy):
    """이 근사가 실제로 재는 것: 상대강도의 **가속도**다.

    자기 과거 분포에서의 순위이므로, 최근 상대강도가 과거보다 강해지고 있으면 높은
    순위를 받는다. 아래는 그 성질을 잠근다.
    """
    n = len(spy)
    accelerating = pd.Series(
        [100.0 * (1.0 + 0.000004 * i * i) for i in range(n)], index=spy.index, name="close"
    )
    decelerating = pd.Series(
        [100.0 * (1.0 + 0.004 * i - 0.000004 * i * i) for i in range(n)],
        index=spy.index,
        name="close",
    )
    fast = approximate_rs_percentile_series(accelerating, spy["close"], UNIVERSE)
    slow = approximate_rs_percentile_series(decelerating, spy["close"], UNIVERSE)
    last = max(fast)
    assert fast[last] > slow[last]


def test_steady_outperformance_does_not_score_high(spy):
    """**근사의 핵심 한계.** 꾸준히 벤치마크를 이기는 종목은 높은 점수를 받지 못한다.

    자기 과거 대비 순위이므로, 일정한 속도로 앞서는 종목의 RS 점수는 시간에 대해
    평탄하고 순위는 중간값 근처로 수렴한다. 진짜 유니버스 백분위였다면 이런 종목이
    상위권이어야 한다.

    이 한계 때문에 미너비니 RS 게이트(>= 70)는 '강한 종목'이 아니라
    '최근 상대강도가 붙고 있는 종목'을 고르게 된다. Phase 3.5에서 진짜 유니버스
    백분위로 교체되기 전까지 이 차이를 잊으면 백테스트 결과를 오독하게 된다.
    """
    n = len(spy)
    steady = pd.Series(
        [100.0 * (1.0 + 0.004 * i) for i in range(n)], index=spy.index, name="close"
    )
    ranks = approximate_rs_percentile_series(steady, spy["close"], UNIVERSE)
    last = max(ranks)
    assert ranks[last] < 70.0, "꾸준한 초과성과가 상위권으로 나오면 근사 성질이 바뀐 것이다"


def test_rs_universe_warning_says_it_is_an_approximation():
    """근사치를 진짜 백분위인 척 흘리지 않는다."""
    warning = rs_universe_warning()
    assert "근사" in warning.message
    assert warning.field == "indicators.rs_percentile"


def test_benchmark_selection_by_exchange():
    assert benchmark_for("AAPL", REGIME, "US") == "SPY"
    assert benchmark_for("005930.KS", REGIME, "KRX") == "^KS11"
    assert benchmark_for("7203.T", REGIME, None) == REGIME.default_benchmark


def test_universe_based_functions_are_still_deferred():
    """진짜 유니버스 백분위는 Phase 3.5다. 근사치로 대체된 척하지 않는다."""
    from data.universe import compute_rs_scores, load_universe_tickers, rs_percentile

    for call in (
        lambda: load_universe_tickers(UNIVERSE),
        lambda: compute_rs_scores(pd.DataFrame(), UNIVERSE),
        lambda: rs_percentile("AAPL", pd.Series(dtype=float)),
    ):
        with pytest.raises(NotImplementedError, match="Phase 3.5"):
            call()
