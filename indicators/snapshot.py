"""OHLCV -> IndicatorSnapshot 조립.

indicators/core.py가 '계산', 이 모듈이 '마지막 봉 값만 뽑아 계약 객체로 옮기기'를 맡는다.
판정은 하지 않는다. 임계값 비교가 여기 등장하면 전략으로 옮겨야 한다.

핵심 규약: **NaN은 None이 된다.** 0.0으로 채우지 않는다.
워밍업이 끝나지 않아 계산할 수 없는 값은 '없음'이지 '0'이 아니며,
이것을 쓰는 게이트 조건은 나중에 UNAVAILABLE이 된다.

## 사전 계산 (indicator frame)

`build_indicator_frame()`이 지표 시계열을 **한 번에** 계산하고,
`snapshot_at()`이 그중 한 봉만 뽑는다. 백테스트는 시점마다 전체 윈도우를 다시 계산하는
대신 프레임을 한 번 만들어 재사용한다 (O(n^2) -> O(n)).

이것이 안전한 이유: 모든 지표가 rolling/shift/재귀 기반이라 **후방 참조만** 한다.
따라서 전체 시계열로 계산한 t번째 값과 df[:t+1]로 계산한 마지막 값이 같다.
이 동치성은 가정이 아니라 `tests/test_snapshot.py`가 검증하는 성질이며,
백테스트 하네스의 look-ahead 감사도 이를 교차 확인한다 (감사는 두 패스에서
서로 다른 길이의 df로 프레임을 만들기 때문에, 지표가 미래를 보면 판정이 갈린다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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


@dataclass(frozen=True)
class IndicatorFrame:
    """사전 계산된 지표 시계열 묶음. 전부 입력과 같은 길이다."""

    index: pd.DatetimeIndex
    series: dict[str, pd.Series]

    def __len__(self) -> int:
        return len(self.index)


def _value(series: pd.Series | None, position: int) -> float | None:
    """position번째 값을 float | None으로. NaN -> None (0.0이 아니다)."""
    if series is None or len(series) == 0:
        return None
    value = float(series.iloc[position])
    return None if math.isnan(value) else value


def _pct_of(value: float | None, base: float | None) -> float | None:
    """(value - base) / base * 100. 어느 한쪽이라도 없거나 base가 0이면 None."""
    if value is None or base is None or base == 0.0:
        return None
    return (value - base) / base * 100.0


def build_indicator_frame(df: pd.DataFrame, config: IndicatorConfig) -> IndicatorFrame:
    """지표 시계열을 한 번에 계산한다. 시점별로 다시 계산하지 않기 위한 것이다.

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
    avg_volume = volume_sma(volume, config.volume_avg_period)

    series: dict[str, pd.Series] = {
        "close": close,
        "volume": volume,
        "rsi14": rsi(close, config.rsi_period),
        "atr14": atr(high, low, close, config.atr_period),
        "adr20_pct": adr_pct(high, low, config.adr_period),
        "macd": macd_line,
        "macd_signal": macd_signal_line,
        "macd_hist": macd_hist,
        "bb_upper": upper,
        "bb_mid": mid,
        "bb_lower": lower,
        "high_52w": rolling_high(high, config.high_low_lookback),
        "low_52w": rolling_low(low, config.high_low_lookback),
        "avg_volume_50": avg_volume,
        "dollar_volume_50": (close * volume)
        .rolling(window=config.volume_avg_period, min_periods=config.volume_avg_period)
        .mean(),
    }
    for period, line in smas.items():
        series[f"sma{period}"] = line
    for period, line in emas.items():
        series[f"ema{period}"] = line

    for period, lookback in (
        (50, config.slope_lookback_short),
        (150, config.slope_lookback_long),
        (200, config.slope_lookback_long),
    ):
        if period in smas:
            series[f"sma{period}_slope_pct"] = slope_pct(smas[period], lookback)

    return IndicatorFrame(index=df.index, series=series)


def snapshot_at(
    frame: IndicatorFrame,
    position: int = -1,
    *,
    rs_percentile: float | None = None,
    rs_line_new_high: bool | None = None,
) -> IndicatorSnapshot:
    """사전 계산된 프레임에서 한 봉을 뽑아 계약 객체로 옮긴다.

    rs_percentile / rs_line_new_high는 유니버스가 있어야 계산되므로 호출부가 주입한다.
    유니버스가 없으면 None이고, 그것을 쓰는 게이트 조건은 UNAVAILABLE이 된다.
    """
    get = frame.series.get

    def at(name: str) -> float | None:
        return _value(get(name), position)

    last_close = at("close")
    atr14 = at("atr14")
    avg_volume = at("avg_volume_50")
    last_volume = at("volume")
    high_52w, low_52w = at("high_52w"), at("low_52w")
    bb_upper, bb_mid, bb_lower = at("bb_upper"), at("bb_mid"), at("bb_lower")

    return IndicatorSnapshot(
        sma20=at("sma20"),
        sma50=at("sma50"),
        sma150=at("sma150"),
        sma200=at("sma200"),
        ema10=at("ema10"),
        ema21=at("ema21"),
        sma50_slope_10d_pct=at("sma50_slope_pct"),
        sma150_slope_20d_pct=at("sma150_slope_pct"),
        sma200_slope_20d_pct=at("sma200_slope_pct"),
        rsi14=at("rsi14"),
        atr14=atr14,
        atr_pct=None if atr14 is None or not last_close else atr14 / last_close * 100.0,
        adr20_pct=at("adr20_pct"),
        macd=at("macd"),
        macd_signal=at("macd_signal"),
        macd_hist=at("macd_hist"),
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_lower=bb_lower,
        bb_width_pct=(
            None
            if bb_upper is None or bb_lower is None or not bb_mid
            else (bb_upper - bb_lower) / bb_mid * 100.0
        ),
        high_52w=high_52w,
        low_52w=low_52w,
        from_52w_high_pct=_pct_of(last_close, high_52w),
        above_52w_low_pct=_pct_of(last_close, low_52w),
        volume=last_volume,
        avg_volume_50=avg_volume,
        volume_ratio=(None if last_volume is None or not avg_volume else last_volume / avg_volume),
        dollar_volume_50=at("dollar_volume_50"),
        rs_percentile=rs_percentile,
        rs_line_new_high=rs_line_new_high,
    )


def build_indicator_snapshot(
    df: pd.DataFrame,
    config: IndicatorConfig,
    *,
    rs_percentile: float | None = None,
    rs_line_new_high: bool | None = None,
) -> IndicatorSnapshot:
    """마지막 봉 기준 지표 스냅샷. 단건 진단용 편의 함수다.

    백테스트처럼 같은 시계열을 반복해서 쓰는 곳은 build_indicator_frame()을 한 번
    호출하고 snapshot_at()으로 시점만 바꿔 쓴다.
    """
    return snapshot_at(
        build_indicator_frame(df, config),
        -1,
        rs_percentile=rs_percentile,
        rs_line_new_high=rs_line_new_high,
    )
