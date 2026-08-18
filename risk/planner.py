"""손절 / 포지션 사이징 / R-multiple.

RiskPlan은 전략 판정과 독립이다. 전략이 진입가와 손절가를 제시하면
여기서 계좌 파라미터를 얹어 주수와 R 배수 목표가를 계산한다.

account_equity가 없으면 shares/position_value/risk_amount는 None이다. 0이 아니다.
"""

from __future__ import annotations

from config import RiskConfig
from core.context import StockContext
from core.types import RiskPlan, StrategyVerdict


def build_risk_plan(
    ctx: StockContext,
    verdicts: list[StrategyVerdict],
    config: RiskConfig,
) -> RiskPlan | None:
    """BUY 또는 WATCH 판정이 하나라도 있을 때만 플랜을 만든다. 없으면 None."""
    raise NotImplementedError


def compute_stop(entry: float, atr14: float | None, config: RiskConfig) -> float:
    """ATR 배수 손절과 max_stop_pct 중 더 타이트한 쪽."""
    raise NotImplementedError


def size_position(
    entry: float, stop: float, config: RiskConfig
) -> tuple[int | None, float | None, float | None]:
    """반환: (shares, position_value, risk_amount). equity 없으면 전부 None."""
    raise NotImplementedError


def r_multiple_prices(entry: float, r_per_share: float, targets: tuple[float, ...]) -> list[float]:
    """entry + n * r_per_share."""
    raise NotImplementedError
