"""하네스 검증용 더미 전략 3종. **매매 판단에 쓰는 전략이 아니다.**

진짜 전략을 만들기 전에 '자'가 맞는지 확인하는 것이 목적이다.
각 전략은 결과가 미리 예측되며, 예측이 어긋나면 전략이 아니라 하네스를 의심해야 한다.

  AlwaysBuy        -> buy-and-hold와 거의 같아야 한다 (매 봉 진입하므로)
  Random           -> 시장 평균 근처여야 한다 (초과수익이 나오면 하네스 버그)
  PerfectHindsight -> 말도 안 되는 초과수익 + look-ahead 감사에 적발되어야 한다

PerfectHindsight는 **의도적으로 미래를 참조한다.** 다만 그것이 가능한 유일한 경로는
하네스가 명시적으로 주입하는 뒷문(requires_full_history)뿐이다.
StockContext에는 t 이하의 봉만 들어 있으므로 실수로는 미래를 볼 수 없다.
"""

from __future__ import annotations

import random

import pandas as pd

from core.context import StockContext
from core.types import (
    CheckStatus,
    Comparator,
    GateCheck,
    GateResult,
    ScoreComponent,
    SetupState,
    Verdict,
)
from strategies.base import StrategyBase


def _gate(strategy: str, checks: list[GateCheck], *, required_all: bool = True) -> GateResult:
    passed_count = sum(c.status is CheckStatus.PASS for c in checks)
    return GateResult(
        strategy=strategy,
        passed=passed_count == len(checks) if required_all else passed_count > 0,
        checks=checks,
        pass_count=passed_count,
        total=len(checks),
        unavailable_count=sum(c.status is CheckStatus.UNAVAILABLE for c in checks),
    )


class AlwaysBuyStrategy(StrategyBase):
    """항상 게이트 통과, 점수 50 고정.

    매 봉 진입하므로 결과는 해당 종목의 무조건부 forward return 분포와 같아야 한다.
    즉 buy-and-hold 벤치마크와 사실상 일치해야 정상이다.
    벤치마크와 어긋나면 진입가/보유기간 정렬이 틀린 것이다.
    """

    name = "always_buy"
    version = "1.0.0"

    FIXED_SCORE = 50.0
    MAX_SCORE = 100.0

    def build_gate(self, ctx: StockContext) -> GateResult:
        return _gate(
            self.name,
            [
                GateCheck(
                    id="always_true",
                    label="무조건 통과",
                    status=CheckStatus.PASS,
                    comparator=Comparator.BOOL,
                    reason="더미 전략 — 게이트를 적용하지 않는다",
                )
            ],
        )

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        return (
            self.FIXED_SCORE,
            self.MAX_SCORE,
            [
                ScoreComponent(
                    id="fixed",
                    label="고정 점수",
                    earned=self.FIXED_SCORE,
                    max=self.MAX_SCORE,
                    detail="타이밍을 채점하지 않는다 — 벤치마크 정렬 확인용",
                )
            ],
        )

    def detect_setup(self, ctx: StockContext) -> SetupState:
        return SetupState.NO_SETUP

    def decide(self, ctx, score, max_score, components, setup) -> tuple[Verdict, list[str]]:
        return Verdict.BUY, ["더미 전략 — 항상 BUY"]


class RandomStrategy(StrategyBase):
    """고정 시드 무작위. 게이트 통과 확률 50%, 점수 0~100 균등.

    시드는 (seed, 봉 날짜)로 결정되므로 **같은 날짜는 항상 같은 결과**를 낸다.
    이것이 중요하다 — look-ahead 감사가 두 번 평가해 비교하는데,
    호출 순서에 따라 값이 달라지면 무작위 전략이 억울하게 적발된다.
    """

    name = "random"
    version = "1.0.0"

    MAX_SCORE = 100.0

    def __init__(self, seed: int = 20260818, gate_pass_probability: float = 0.5) -> None:
        self.seed = seed
        self.gate_pass_probability = gate_pass_probability

    def _rng(self, ctx: StockContext) -> random.Random:
        """봉 날짜에 고정된 난수원. 프로세스 간에도 재현된다."""
        return random.Random(self.seed * 1_000_003 + ctx.as_of.toordinal())

    def build_gate(self, ctx: StockContext) -> GateResult:
        draw = self._rng(ctx).random()
        passed = draw < self.gate_pass_probability
        return _gate(
            self.name,
            [
                GateCheck(
                    id="coin_flip",
                    label="무작위 게이트",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    actual=round(draw, 6),
                    threshold=self.gate_pass_probability,
                    comparator=Comparator.LT,
                    reason=f"난수 {draw:.4f} vs 기준 {self.gate_pass_probability}",
                )
            ],
        )

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        rng = self._rng(ctx)
        rng.random()  # 게이트에서 뽑은 난수를 건너뛰어 점수와 분리한다
        score = rng.uniform(0.0, self.MAX_SCORE)
        return (
            score,
            self.MAX_SCORE,
            [
                ScoreComponent(
                    id="coin_score",
                    label="무작위 점수",
                    earned=score,
                    max=self.MAX_SCORE,
                    detail=f"균등분포 난수 {score:.2f}",
                )
            ],
        )

    def detect_setup(self, ctx: StockContext) -> SetupState:
        return SetupState.NO_SETUP

    def decide(self, ctx, score, max_score, components, setup) -> tuple[Verdict, list[str]]:
        return Verdict.BUY, [f"더미 전략 — 무작위 점수 {score:.1f}"]


class PerfectHindsightStrategy(StrategyBase):
    """**의도적으로 look-ahead를 저지르는 전략.** 감사 장치를 검증하기 위해서만 존재한다.

    lookahead_bars 만큼 뒤의 종가를 훔쳐보고, 오를 때만 게이트를 통과시킨다.
    결과적으로 말이 안 되는 초과수익이 나와야 하고, LookaheadAudit이 이를 적발해야 한다.

    미래에 접근하는 경로는 하나뿐이다: 하네스가 requires_full_history를 보고
    inject_full_history()로 넣어주는 것. 주입이 없으면 미래를 볼 수 없고,
    그때는 게이트를 통과시키지 않는다.

    감사는 이 성질을 이용한다 — 전체 데이터를 주입한 평가와 t에서 잘린 데이터를
    주입한 평가가 다르면 미래를 쓴 것이다.
    """

    name = "perfect_hindsight"
    version = "1.0.0"

    MAX_SCORE = 100.0
    requires_full_history = True

    def __init__(self, lookahead_bars: int = 20) -> None:
        self.lookahead_bars = lookahead_bars
        self._full: pd.DataFrame | None = None

    def inject_full_history(self, ohlcv: pd.DataFrame) -> None:
        """하네스가 부르는 뒷문. 진짜 전략은 이 메서드를 갖지 않는다."""
        self._full = ohlcv

    def _future_return_pct(self, ctx: StockContext) -> float | None:
        """lookahead_bars 뒤 종가 대비 수익률. 볼 수 없으면 None."""
        if self._full is None:
            return None
        timestamp = ctx.ohlcv.index[-1]
        position = self._full.index.get_indexer([timestamp])[0]
        if position < 0:
            return None
        future_position = position + self.lookahead_bars
        if future_position >= len(self._full):
            return None
        current = float(self._full["close"].iloc[position])
        future = float(self._full["close"].iloc[future_position])
        return (future - current) / current * 100.0

    def build_gate(self, ctx: StockContext) -> GateResult:
        future = self._future_return_pct(ctx)
        if future is None:
            status, actual, reason = (
                CheckStatus.UNAVAILABLE,
                None,
                "미래 데이터를 볼 수 없다 (주입 없음 또는 데이터 끝)",
            )
        else:
            status = CheckStatus.PASS if future > 0.0 else CheckStatus.FAIL
            actual = round(future, 4)
            reason = f"{self.lookahead_bars}봉 뒤 수익률 {future:+.2f}% (미래 참조!)"

        return _gate(
            self.name,
            [
                GateCheck(
                    id="future_return_positive",
                    label=f"{self.lookahead_bars}봉 뒤 상승 (LOOK-AHEAD)",
                    status=status,
                    actual=actual,
                    threshold=0.0,
                    comparator=Comparator.GT,
                    unit="%",
                    reason=reason,
                )
            ],
        )

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        future = self._future_return_pct(ctx) or 0.0
        score = max(0.0, min(self.MAX_SCORE, 50.0 + future * 2.0))
        return (
            score,
            self.MAX_SCORE,
            [
                ScoreComponent(
                    id="future_magnitude",
                    label="미래 상승폭 (LOOK-AHEAD)",
                    earned=score,
                    max=self.MAX_SCORE,
                    detail=f"{self.lookahead_bars}봉 뒤 {future:+.2f}%를 그대로 점수화",
                )
            ],
        )

    def detect_setup(self, ctx: StockContext) -> SetupState:
        return SetupState.NO_SETUP

    def decide(self, ctx, score, max_score, components, setup) -> tuple[Verdict, list[str]]:
        return Verdict.BUY, ["더미 전략 — 미래를 알고 산다. 실전에서 불가능하다"]


DUMMY_STRATEGIES = {
    AlwaysBuyStrategy.name: AlwaysBuyStrategy,
    RandomStrategy.name: RandomStrategy,
    PerfectHindsightStrategy.name: PerfectHindsightStrategy,
}
