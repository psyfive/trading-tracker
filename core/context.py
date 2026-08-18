"""StockContext — 지표 계산이 끝난 상태 객체.

전략은 원시 DataFrame이나 네트워크를 절대 만지지 않는다. 오직 이 객체만 받는다.
같은 지표를 전략마다 다시 계산하는 낭비와, 전략마다 다른 값을 쓰는 사고를 동시에 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from config import AppConfig
from core.types import BarMeta, DiagnosticWarning, IndicatorSnapshot, MarketRegime, Stage


@dataclass(frozen=True)
class StockContext:
    """한 티커에 대한 평가 입력 일체.

    ohlcv는 오름차순(과거 -> 현재) DatetimeIndex를 가진다. 전략은 이를 전제한다.
    """

    ticker: str
    ohlcv: pd.DataFrame
    indicators: IndicatorSnapshot
    bar_meta: BarMeta
    regime: MarketRegime
    stage: Stage
    config: AppConfig
    warnings: tuple[DiagnosticWarning, ...] = ()

    @property
    def price(self) -> float:
        """마지막 봉 종가. 미완성 봉이면 실시간 값이다."""
        raise NotImplementedError

    @property
    def as_of(self) -> date:
        raise NotImplementedError

    @property
    def volume_reliable(self) -> bool:
        """거래량 기반 조건을 평가해도 되는지.

        False면 전략은 거래량 GateCheck를 UNAVAILABLE로 만들어야 한다. FAIL이 아니다.
        """
        raise NotImplementedError

    def has(self, *fields: str) -> bool:
        """지정한 IndicatorSnapshot 필드가 전부 None이 아닌지.

        게이트 조건을 UNAVAILABLE로 낼지 판단할 때 쓴다.
        """
        raise NotImplementedError


def build_context(
    ticker: str,
    ohlcv: pd.DataFrame,
    config: AppConfig,
    *,
    regime: MarketRegime,
    rs_percentile: float | None = None,
) -> StockContext:
    """OHLCV로부터 지표를 전부 계산해 StockContext를 만든다.

    여기가 지표 계산의 유일한 진입점이다. 전략은 이 함수를 호출하지 않는다.
    """
    raise NotImplementedError
