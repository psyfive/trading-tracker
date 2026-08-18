"""검증 러너.

과거 시점 t를 잘라서 그 시점의 StockContext를 재구성하고 전략을 평가한다.
look-ahead 금지: t 시점 판정에 t+1 이후 봉을 절대 쓰지 않는다.

전략 점수를 합산하지 않는 원칙은 백테스트에서도 유지된다.
성과는 전략별로 따로 집계한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from config import AppConfig
from core.types import DiagnosisReport
from strategies.base import Strategy


@dataclass(frozen=True)
class BacktestResult:
    """전략 하나의 성과. 전략끼리 합산하지 않는다."""

    strategy_name: str
    trades: int
    win_rate: float
    avg_r: float
    max_drawdown_pct: float
    expectancy_r: float


def slice_as_of(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """as_of 이하의 봉만 반환. look-ahead 차단 지점."""
    raise NotImplementedError


def replay(
    ticker: str,
    strategies: list[Strategy],
    config: AppConfig,
    start: date,
    end: date,
) -> list[DiagnosisReport]:
    """기간 내 각 거래일에 대해 진단 리포트를 생성한다."""
    raise NotImplementedError


def evaluate_results(reports: list[DiagnosisReport], config: AppConfig) -> list[BacktestResult]:
    """리포트 시퀀스에서 전략별 성과를 계산한다."""
    raise NotImplementedError


def sweep(
    ticker: str,
    base_config: AppConfig,
    param_grid: dict[str, list[float]],
    start: date,
    end: date,
) -> pd.DataFrame:
    """dataclasses.replace()로 설정 변형본을 만들어 파라미터 스윕."""
    raise NotImplementedError
