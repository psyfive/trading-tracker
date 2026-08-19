"""시점별 주입 시리즈(regime / stage) 테스트. RS는 test_universe.py.

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
