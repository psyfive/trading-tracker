"""미너비니 SEPA — 추세 템플릿(GATE) + VCP 타이밍(SCORE).

GATE(추세 템플릿 8조건)는 전부 이진 필터다. 가산점으로 옮기지 않는다.
SCORE는 게이트를 통과한 종목의 VCP 수축 품질과 피봇 근접도만 채점한다.

지표가 None이면(예: 상장 6개월 종목의 sma200) 해당 GateCheck는 UNAVAILABLE이다. FAIL이 아니다.
"""

from __future__ import annotations

from config import MinerviniConfig
from core.context import StockContext
from core.types import GateResult, ScoreComponent, SetupState, Verdict
from strategies.base import StrategyBase


class MinerviniStrategy(StrategyBase):
    """SEPA 추세 템플릿 + VCP."""

    name = "minervini"
    version = "0.1.0"

    def __init__(self, config: MinerviniConfig) -> None:
        self.config = config

    def build_gate(self, ctx: StockContext) -> GateResult:
        """추세 템플릿 조건들.

        price_above_sma150_200 / sma150_above_sma200 / sma200_trending_up /
        sma50_above_sma150_200 / price_above_sma50 / above_52w_low /
        near_52w_high / rs_percentile
        """
        raise NotImplementedError

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        """VCP 수축 횟수/깊이, 볼륨 드라이업, 피봇 근접도, 베이스 길이."""
        raise NotImplementedError

    def detect_setup(self, ctx: StockContext) -> SetupState:
        raise NotImplementedError

    def decide(
        self,
        ctx: StockContext,
        score: float,
        max_score: float,
        components: list[ScoreComponent],
        setup: SetupState,
    ) -> tuple[Verdict, list[str]]:
        raise NotImplementedError
