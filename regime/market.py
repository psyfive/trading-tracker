"""시장 국면 판정 + Stage 판정.

개별 종목 판정 위에 걸리는 상위 필터다. RISK_OFF에서는 전략이 BUY를 내더라도
REGIME_RISK_OFF 경고가 붙는다. 국면이 개별 전략 점수를 깎지는 않는다 (판정 분리 보존).

## 시점별(point-in-time) 산출이 이 모듈의 계약이다

`*_series()` 함수는 날짜 t의 값을 **t 이하 데이터로만** 계산한다. 백테스트 하네스는
이 시리즈를 그대로 주입받아 쓰며, 하네스의 look-ahead 감사는 시리즈 **안의**
미래 참조를 잡지 못한다 (양쪽 평가에 같은 시리즈를 쓰기 때문). 즉 시점 정합성은
전적으로 이 모듈의 책임이다.

구현상 전부 rolling/shift 기반이라 정의상 후방 참조만 한다. 테스트가 이를 잠근다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from config import RegimeConfig
from core.types import MarketRegime, Stage
from indicators.core import slope_pct, sma


def distribution_day_flags(index_ohlcv: pd.DataFrame, config: RegimeConfig) -> pd.Series:
    """분산일 여부(bool Series).

    분산일 = 거래량이 전일보다 늘면서 종가가 기준 이상 하락한 날.
    기관 매도의 흔적으로 읽는 오닐식 정의다.
    """
    close = index_ohlcv["close"]
    volume = index_ohlcv["volume"]
    drop_pct = (close - close.shift(1)) / close.shift(1) * 100.0
    return (drop_pct <= -config.distribution_min_drop_pct) & (volume > volume.shift(1))


def count_distribution_days(index_ohlcv: pd.DataFrame, config: RegimeConfig) -> int:
    """최근 lookback 구간의 분산일 수 (마지막 봉 기준)."""
    flags = distribution_day_flags(index_ohlcv, config)
    return int(flags.tail(config.distribution_lookback_days).sum())


def _classify(
    close: float,
    sma200: float | None,
    slope_pct_value: float | None,
    distribution_days: int,
    config: RegimeConfig,
) -> MarketRegime:
    """국면 판정 규칙 한 곳.

    지수가 200일선 아래면 RISK_OFF. 위에 있어도 200일선이 상승 중이 아니거나
    분산일이 쌓였으면 CAUTION. 둘 다 만족해야 RISK_ON이다.
    판정 근거가 없으면(200일선 미산출) 낙관도 비관도 하지 않고 CAUTION이다.
    """
    if sma200 is None or slope_pct_value is None:
        return MarketRegime.CAUTION
    if close <= sma200:
        return MarketRegime.RISK_OFF
    if distribution_days > config.caution_max_distribution_days:
        return MarketRegime.CAUTION
    if slope_pct_value <= config.risk_on_min_sma200_slope_pct:
        return MarketRegime.CAUTION
    return MarketRegime.RISK_ON


def classify_regime(index_ohlcv: pd.DataFrame, config: RegimeConfig) -> MarketRegime:
    """마지막 봉 기준 시장 국면."""
    close = index_ohlcv["close"]
    sma200 = sma(close, config.sma_period)
    slopes = slope_pct(sma200, config.slope_lookback)

    last_sma = sma200.iloc[-1]
    last_slope = slopes.iloc[-1]
    return _classify(
        float(close.iloc[-1]),
        None if pd.isna(last_sma) else float(last_sma),
        None if pd.isna(last_slope) else float(last_slope),
        count_distribution_days(index_ohlcv, config),
        config,
    )


def regime_series(index_ohlcv: pd.DataFrame, config: RegimeConfig) -> dict[date, MarketRegime]:
    """날짜별 시장 국면. **각 날짜의 값은 그 날짜 이하 데이터로만 계산된다.**

    하네스의 regime_by_date 인자로 그대로 넘긴다.
    """
    close = index_ohlcv["close"]
    sma200 = sma(close, config.sma_period)
    slopes = slope_pct(sma200, config.slope_lookback)
    distribution = (
        distribution_day_flags(index_ohlcv, config)
        .rolling(window=config.distribution_lookback_days, min_periods=1)
        .sum()
    )

    out: dict[date, MarketRegime] = {}
    for timestamp, price, ma, slope, dist in zip(
        index_ohlcv.index, close, sma200, slopes, distribution, strict=True
    ):
        out[timestamp.date()] = _classify(
            float(price),
            None if pd.isna(ma) else float(ma),
            None if pd.isna(slope) else float(slope),
            0 if pd.isna(dist) else int(dist),
            config,
        )
    return out


def _stage_of(
    close: float, ma: float | None, ma_slope_pct: float | None, config: RegimeConfig
) -> Stage:
    """와인스타인 Stage 판정.

    (주가 vs 이동평균) x (이동평균 기울기 부호)의 2x2다:
      MA 위 + MA 상승  -> STAGE_2 (상승 추세)
      MA 위 + MA 평탄  -> STAGE_3 (천장 다지기)
      MA 아래 + MA 하락 -> STAGE_4 (하락 추세)
      MA 아래 + MA 평탄 -> STAGE_1 (바닥 다지기)

    **2x2가 가진 라벨 함정** (읽는 쪽이 반드시 알아야 한다): 마지막 칸은 '평탄'만이
    아니라 '**상승 중인** MA를 주가가 하회한 경우'까지 삼킨다. 즉 Stage 2 진행 중의
    눌림목이 STAGE_1('바닥 다지기')로 표기된다. 판정 방향은 보수적이라(사지 않는 쪽)
    손실로 이어지지 않지만, 화면 문구로는 오진이다. 게다가 일봉 근사이므로 주봉 기반
    원전보다 훨씬 자주 깜빡인다 — Stage 표기는 '오늘 하루의 상태'로 읽어야 하고,
    STAGE_1은 '바닥'이 아니라 '이동평균 아래에 있다'로 읽는 편이 안전하다.

    라벨을 늘리지 않는 이유는 Stage enum이 계약이고 그 값이 곧 JSON이기 때문이다.
    구분이 필요해지면 SCHEMA_VERSION을 올려 새 상태를 추가해야 한다.

    근거가 없으면 UNDEFINED다. 추측해서 채우지 않는다.
    """
    if ma is None or ma_slope_pct is None:
        return Stage.UNDEFINED

    flat = config.stage_flat_slope_pct
    above = close > ma
    if above:
        return Stage.STAGE_2 if ma_slope_pct > flat else Stage.STAGE_3
    return Stage.STAGE_4 if ma_slope_pct < -flat else Stage.STAGE_1


def classify_stage(ohlcv: pd.DataFrame, config: RegimeConfig) -> Stage:
    """마지막 봉 기준 Stage."""
    close = ohlcv["close"]
    ma = sma(close, config.stage_ma_period)
    slopes = slope_pct(ma, config.slope_lookback)
    last_ma, last_slope = ma.iloc[-1], slopes.iloc[-1]
    return _stage_of(
        float(close.iloc[-1]),
        None if pd.isna(last_ma) else float(last_ma),
        None if pd.isna(last_slope) else float(last_slope),
        config,
    )


def stage_series(ohlcv: pd.DataFrame, config: RegimeConfig) -> dict[date, Stage]:
    """날짜별 Stage. 각 날짜의 값은 그 날짜 이하 데이터로만 계산된다."""
    close = ohlcv["close"]
    ma = sma(close, config.stage_ma_period)
    slopes = slope_pct(ma, config.slope_lookback)

    return {
        timestamp.date(): _stage_of(
            float(price),
            None if pd.isna(ma_value) else float(ma_value),
            None if pd.isna(slope) else float(slope),
            config,
        )
        for timestamp, price, ma_value, slope in zip(ohlcv.index, close, ma, slopes, strict=True)
    }
