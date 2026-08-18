"""IndicatorSnapshot 조립 테스트 — 고정 픽스처(실제 3년치)만 사용.

Phase 0에서 손으로 짠 계약이 실제 시장 데이터로도 성립하는지 확인하는 자리다.
특히 'RS는 아직 없으니 None'이 실제로 그렇게 나오는지 —
계산 불가를 0으로 채우지 않는다는 규약이 코드에서 지켜지는지 본다.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from config import DEFAULT_CONFIG
from core.types import (
    BarMeta,
    ConsensusSummary,
    DiagnosisReport,
    IndicatorSnapshot,
    MarketRegime,
    SessionState,
    Stage,
)
from indicators.core import atr, ema, rolling_high, rsi, sma
from indicators.snapshot import build_indicator_snapshot

IND = DEFAULT_CONFIG.indicators


def snapshot_of(df, **kwargs) -> IndicatorSnapshot:
    return build_indicator_snapshot(df, IND, **kwargs)


# ===========================================================================
# 전체 히스토리가 있을 때 — 모든 지표가 채워진다
# ===========================================================================


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_full_history_fills_every_moving_average(name, request):
    snap = snapshot_of(request.getfixturevalue(name))
    for field in ("sma20", "sma50", "sma150", "sma200", "ema10", "ema21"):
        assert getattr(snap, field) is not None, field
        assert getattr(snap, field) > 0.0, field


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_full_history_fills_oscillators_and_volatility(name, request):
    snap = snapshot_of(request.getfixturevalue(name))
    assert 0.0 <= snap.rsi14 <= 100.0
    assert snap.atr14 > 0.0
    assert snap.atr_pct > 0.0
    assert snap.adr20_pct > 0.0
    assert snap.macd is not None and snap.macd_signal is not None
    assert abs(snap.macd_hist - (snap.macd - snap.macd_signal)) < 1e-9


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_bollinger_ordering_holds_on_real_data(name, request):
    snap = snapshot_of(request.getfixturevalue(name))
    assert snap.bb_lower < snap.bb_mid < snap.bb_upper
    assert snap.bb_width_pct > 0.0


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_52w_position_is_internally_consistent(name, request):
    df = request.getfixturevalue(name)
    snap = snapshot_of(df)
    close = float(df["close"].iloc[-1])
    assert snap.low_52w <= snap.high_52w
    assert snap.from_52w_high_pct <= 0.0, "종가가 52주 고가를 넘을 수는 없다"
    assert snap.above_52w_low_pct >= 0.0
    assert abs(snap.from_52w_high_pct - (close - snap.high_52w) / snap.high_52w * 100.0) < 1e-9


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_volume_fields_are_consistent(name, request):
    snap = snapshot_of(request.getfixturevalue(name))
    assert snap.volume > 0.0
    assert snap.avg_volume_50 > 0.0
    assert abs(snap.volume_ratio - snap.volume / snap.avg_volume_50) < 1e-9
    assert snap.dollar_volume_50 > 0.0


def test_snapshot_values_match_direct_indicator_calls(aapl):
    """스냅샷은 지표 함수의 마지막 값을 옮기기만 한다. 재계산하거나 가공하지 않는다."""
    snap = snapshot_of(aapl)
    close, high, low = aapl["close"], aapl["high"], aapl["low"]
    assert abs(snap.sma200 - float(sma(close, 200).iloc[-1])) < 1e-9
    assert abs(snap.ema21 - float(ema(close, 21).iloc[-1])) < 1e-9
    assert abs(snap.rsi14 - float(rsi(close, 14).iloc[-1])) < 1e-9
    assert abs(snap.atr14 - float(atr(high, low, close, 14).iloc[-1])) < 1e-9
    assert abs(snap.high_52w - float(rolling_high(high, 252).iloc[-1])) < 1e-9


# ===========================================================================
# RS는 Phase 3.5 — 지금은 None이어야 한다
# ===========================================================================


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_rs_percentile_is_none_without_a_universe(name, request):
    """유니버스가 없으면 RS 백분위는 계산 불가다. 0.0이나 50.0으로 채우면 안 된다.

    이 None이 나중에 게이트에서 UNAVAILABLE로 이어진다 — Phase 0 규약의 실전 검증.
    """
    snap = snapshot_of(request.getfixturevalue(name))
    assert snap.rs_percentile is None
    assert snap.rs_line_new_high is None


def test_rs_percentile_is_injected_not_computed_here(aapl):
    """지표 레이어는 RS를 계산하지 않는다. 유니버스를 가진 상위가 주입한다."""
    snap = snapshot_of(aapl, rs_percentile=88.0, rs_line_new_high=True)
    assert snap.rs_percentile == 88.0
    assert snap.rs_line_new_high is True


# ===========================================================================
# 히스토리가 짧을 때 — 예외가 아니라 None
# ===========================================================================


def test_short_history_leaves_long_averages_none(aapl):
    """100봉만 있으면 SMA150/200은 None, SMA20/50은 값이 있다."""
    snap = snapshot_of(aapl.tail(100))
    assert snap.sma20 is not None
    assert snap.sma50 is not None
    assert snap.sma150 is None
    assert snap.sma200 is None
    assert snap.sma200_slope_20d_pct is None


def test_short_history_leaves_52w_range_none(aapl):
    snap = snapshot_of(aapl.tail(100))
    assert snap.high_52w is None
    assert snap.low_52w is None
    assert snap.from_52w_high_pct is None
    assert snap.above_52w_low_pct is None


def test_very_short_history_still_returns_a_valid_snapshot(aapl):
    """25봉짜리도 예외 없이 스냅샷이 나온다. 대부분 None일 뿐이다."""
    snap = snapshot_of(aapl.tail(25))
    assert snap.sma20 is not None
    assert snap.sma50 is None
    assert snap.rsi14 is not None


def test_no_nan_ever_leaks_into_the_snapshot(aapl):
    """NaN이 계약에 새어 들어가면 프론트에서 JSON 직렬화가 깨진다."""
    snap = snapshot_of(aapl.tail(60))
    for field in IndicatorSnapshot.model_fields:
        value = getattr(snap, field)
        if isinstance(value, float):
            assert not math.isnan(value), f"{field}에 NaN이 들어갔다"


def test_missing_values_are_none_not_zero(aapl):
    """0.0으로 채우면 '주가 > SMA200' 조건이 항상 참이 되는 조용한 오진이 난다."""
    snap = snapshot_of(aapl.tail(100))
    assert snap.sma200 is not None or snap.sma200 != 0.0
    assert snap.sma200 is None


# ===========================================================================
# Phase 0 계약이 실제 데이터로도 성립하는가
# ===========================================================================


@pytest.mark.parametrize("name", ["aapl", "samsung"])
def test_real_snapshot_passes_the_diagnosis_report_contract(name, request):
    """손으로 짠 계약이 실제 시장 데이터를 담을 수 있는지 — Phase 0 검증의 실전판."""
    df = request.getfixturevalue(name)
    snap = snapshot_of(df)

    report = DiagnosisReport(
        ticker=name.upper(),
        as_of=df.index[-1].date(),
        generated_at=datetime.now(UTC),
        price=float(df["close"].iloc[-1]),
        is_bar_complete=True,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED,
            is_bar_complete=True,
            bars_available=len(df),
            volume_judgements_reliable=True,
        ),
        regime=MarketRegime.CAUTION,
        stage=Stage.UNDEFINED,
        indicators=snap,
        strategy_verdicts=[],
        consensus=ConsensusSummary(total_strategies=0),
    )

    restored = DiagnosisReport.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.indicators.rs_percentile is None


def test_short_history_snapshot_also_passes_the_contract(aapl):
    """신규 상장주처럼 대부분이 None인 스냅샷도 계약을 통과해야 한다."""
    snap = snapshot_of(aapl.tail(80))
    report = DiagnosisReport(
        ticker="NEWLY_LISTED",
        as_of=aapl.index[-1].date(),
        generated_at=datetime.now(UTC),
        price=float(aapl["close"].iloc[-1]),
        is_bar_complete=True,
        bar_meta=BarMeta(
            last_bar_date=aapl.index[-1].date(),
            is_bar_complete=True,
            bars_available=80,
            volume_judgements_reliable=True,
        ),
        regime=MarketRegime.CAUTION,
        stage=Stage.UNDEFINED,
        indicators=snap,
        consensus=ConsensusSummary(total_strategies=0),
    )
    assert report.indicators.sma200 is None
