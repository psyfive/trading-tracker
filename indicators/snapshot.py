"""OHLCV -> IndicatorSnapshot 조립.

indicators/core.py가 '계산', 이 모듈이 '마지막 봉 값만 뽑아 계약 객체로 옮기기'를 맡는다.
판정은 하지 않는다. 임계값 비교가 여기 등장하면 전략으로 옮겨야 한다.

핵심 규약: **NaN은 None이 된다.** 0.0으로 채우지 않는다.
워밍업이 끝나지 않아 계산할 수 없는 값은 '없음'이지 '0'이 아니며,
이것을 쓰는 게이트 조건은 나중에 UNAVAILABLE이 된다.
"""

from __future__ import annotations

import math

import pandas as pd

from config import IndicatorConfig
from core.types import IndicatorSnapshot
from indicators.core import (
    adr_pct,
    atr,
    bollinger,
    ema,
    macd,
    rolling_high,
    rolling_low,
    rsi,
    slope_pct,
    sma,
    volume_sma,
)


def _last(series: pd.Series) -> float | None:
    """마지막 값을 float | None으로. NaN -> None (0.0이 아니다)."""
    if len(series) == 0:
        return None
    value = float(series.iloc[-1])
    return None if math.isnan(value) else value


def _pct_of(value: float | None, base: float | None) -> float | None:
    """(value - base) / base * 100. 어느 한쪽이라도 없거나 base가 0이면 None."""
    if value is None or base is None or base == 0.0:
        return None
    return (value - base) / base * 100.0


def build_indicator_snapshot(
    df: pd.DataFrame,
    config: IndicatorConfig,
    *,
    rs_percentile: float | None = None,
    rs_line_new_high: bool | None = None,
) -> IndicatorSnapshot:
    """마지막 봉 기준 지표 스냅샷.

    rs_percentile / rs_line_new_high는 유니버스가 있어야 계산되므로 호출부에서 주입한다.
    Phase 1에서는 유니버스가 없어 항상 None이 들어온다 — 그리고 그게 맞는 동작이다.

    주의: sma50_slope_10d_pct 같은 필드명은 lookback을 이름에 박아 두었다.
    config.slope_lookback_short / _long을 바꾸면 필드명이 실제 계산과 어긋난다.
    바꿀 일이 생기면 계약 필드명도 함께 바꾸고 SCHEMA_VERSION을 올려야 한다.
    """
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    smas = {period: sma(close, period) for period in config.sma_periods}
    emas = {period: ema(close, period) for period in config.ema_periods}

    upper, mid, lower = bollinger(close, config.bb_period, config.bb_std, config.bb_ddof)
    macd_line, macd_signal_line, macd_hist = macd(
        close, config.macd_fast, config.macd_slow, config.macd_signal
    )

    last_close = _last(close)
    atr14 = _last(atr(high, low, close, config.atr_period))
    avg_volume = _last(volume_sma(volume, config.volume_avg_period))
    last_volume = _last(volume)

    high_52w = _last(rolling_high(high, config.high_low_lookback))
    low_52w = _last(rolling_low(low, config.high_low_lookback))

    bb_upper, bb_mid, bb_lower = _last(upper), _last(mid), _last(lower)
    bb_width_pct = (
        None
        if bb_upper is None or bb_lower is None or not bb_mid
        else (bb_upper - bb_lower) / bb_mid * 100.0
    )

    def slope_of(period: int, lookback: int) -> float | None:
        line = smas.get(period)
        return None if line is None else _last(slope_pct(line, lookback))

    return IndicatorSnapshot(
        sma20=_last(smas[20]) if 20 in smas else None,
        sma50=_last(smas[50]) if 50 in smas else None,
        sma150=_last(smas[150]) if 150 in smas else None,
        sma200=_last(smas[200]) if 200 in smas else None,
        ema10=_last(emas[10]) if 10 in emas else None,
        ema21=_last(emas[21]) if 21 in emas else None,
        sma50_slope_10d_pct=slope_of(50, config.slope_lookback_short),
        sma150_slope_20d_pct=slope_of(150, config.slope_lookback_long),
        sma200_slope_20d_pct=slope_of(200, config.slope_lookback_long),
        rsi14=_last(rsi(close, config.rsi_period)),
        atr14=atr14,
        atr_pct=None if atr14 is None or not last_close else atr14 / last_close * 100.0,
        adr20_pct=_last(adr_pct(high, low, config.adr_period)),
        macd=_last(macd_line),
        macd_signal=_last(macd_signal_line),
        macd_hist=_last(macd_hist),
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_lower=bb_lower,
        bb_width_pct=bb_width_pct,
        high_52w=high_52w,
        low_52w=low_52w,
        from_52w_high_pct=_pct_of(last_close, high_52w),
        above_52w_low_pct=_pct_of(last_close, low_52w),
        volume=last_volume,
        avg_volume_50=avg_volume,
        volume_ratio=(
            None if last_volume is None or not avg_volume else last_volume / avg_volume
        ),
        dollar_volume_50=_last(
            (close * volume).rolling(
                window=config.volume_avg_period, min_periods=config.volume_avg_period
            ).mean()
        ),
        rs_percentile=rs_percentile,
        rs_line_new_high=rs_line_new_high,
    )
