"""StockContext — 지표 계산이 끝난 상태 객체.

전략은 원시 DataFrame이나 네트워크를 절대 만지지 않는다. 오직 이 객체만 받는다.
같은 지표를 전략마다 다시 계산하는 낭비와, 전략마다 다른 값을 쓰는 사고를 동시에 막는다.

**백테스트에서의 의미**: 시점 t의 StockContext에는 t 이하의 봉만 들어 있다.
미래 봉은 물리적으로 존재하지 않으므로 전략이 실수로 미래를 볼 방법이 없다.
의도적으로 보려면 하네스가 별도 경로로 주입해야만 하고, 그 경로는 감사 대상이다
(backtest/harness.py의 LookaheadAudit 참조).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from config import AppConfig
from core.types import BarMeta, DiagnosticWarning, IndicatorSnapshot, MarketRegime, Stage
from indicators.snapshot import IndicatorFrame, snapshot_at


@dataclass(frozen=True)
class StockContext:
    """한 티커에 대한 평가 입력 일체.

    ohlcv는 오름차순(과거 -> 현재) DatetimeIndex를 가진다. 전략은 이를 전제한다.
    마지막 행이 '지금'이며, 그 뒤는 존재하지 않는다.
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
        return float(self.ohlcv["close"].iloc[-1])

    @property
    def as_of(self) -> date:
        """마지막 봉의 거래일. 이 시점 이후는 알 수 없다."""
        return self.ohlcv.index[-1].date()

    @property
    def volume_reliable(self) -> bool:
        """거래량 기반 조건을 평가해도 되는지.

        False면 전략은 거래량 GateCheck를 UNAVAILABLE로 만들어야 한다. FAIL이 아니다.
        """
        return self.bar_meta.volume_judgements_reliable

    def has(self, *fields: str) -> bool:
        """지정한 IndicatorSnapshot 필드가 전부 None이 아닌지.

        게이트 조건을 UNAVAILABLE로 낼지 판단할 때 쓴다.
        존재하지 않는 필드명을 넘기면 오타이므로 AttributeError로 즉시 터진다.
        """
        return all(getattr(self.indicators, field) is not None for field in fields)


def build_context(
    ticker: str,
    ohlcv: pd.DataFrame,
    config: AppConfig,
    *,
    regime: MarketRegime,
    bar_meta: BarMeta,
    stage: Stage = Stage.UNDEFINED,
    rs_percentile: float | None = None,
    rs_line_new_high: bool | None = None,
    warnings: tuple[DiagnosticWarning, ...] = (),
    indicator_frame: IndicatorFrame | None = None,
    position: int = -1,
) -> StockContext:
    """OHLCV로부터 지표를 계산해 StockContext를 만든다.

    여기가 지표 계산의 유일한 진입점이다. 전략은 이 함수를 호출하지 않는다.

    rs_percentile은 유니버스가 필요하므로 호출부가 주입한다.
    stage 판정은 regime/market.py의 몫이다.

    indicator_frame: 사전 계산된 지표 시계열. 백테스트가 같은 시계열을 시점마다
    다시 계산하지 않도록 넘긴다. 이때 ohlcv는 시점까지 잘린 창이고 position은
    **프레임 기준** 인덱스다. 넘기지 않으면 ohlcv로 그 자리에서 계산한다.
    """
    if indicator_frame is None:
        from indicators.snapshot import build_indicator_frame

        indicator_frame, position = build_indicator_frame(ohlcv, config.indicators), -1

    return StockContext(
        ticker=ticker.upper(),
        ohlcv=ohlcv,
        indicators=snapshot_at(
            indicator_frame,
            position,
            rs_percentile=rs_percentile,
            rs_line_new_high=rs_line_new_high,
        ),
        bar_meta=bar_meta,
        regime=regime,
        stage=stage,
        config=config,
        warnings=warnings,
    )
