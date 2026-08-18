"""시장 국면 판정.

개별 종목 판정 위에 걸리는 상위 필터다. RISK_OFF에서는 전략이 BUY를 내더라도
REGIME_RISK_OFF 경고가 붙는다. 국면이 개별 전략 점수를 깎지는 않는다 (판정 분리 보존).
"""

from __future__ import annotations

import pandas as pd

from config import RegimeConfig
from core.types import MarketRegime, Stage


def classify_regime(index_ohlcv: pd.DataFrame, config: RegimeConfig) -> MarketRegime:
    """지수 추세와 분산일 수로 RISK_ON / CAUTION / RISK_OFF 판정."""
    raise NotImplementedError


def count_distribution_days(index_ohlcv: pd.DataFrame, lookback: int) -> int:
    """거래량 증가를 동반한 하락일 수."""
    raise NotImplementedError


def classify_stage(
    close: pd.Series, ma: pd.Series, ma_slope_pct: float | None
) -> Stage:
    """와인스타인 Stage 1~4 판정. 근거 부족 시 UNDEFINED."""
    raise NotImplementedError
