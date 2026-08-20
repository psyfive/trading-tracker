"""리스크 플래너 테스트.

검증 대상은 '숫자가 예쁜가'가 아니라 **규칙이 지켜지는가**다:

  - 손절은 두 후보 중 **타이트한 쪽**이다 (진입가에 가까운 쪽)
  - 주수는 리스크 예산과 포지션 상한 중 **작은 쪽**이다
  - `risk_amount`는 예산이 아니라 **체결 기준 실제 손실액**이다
  - 플랜은 **진입 의사가 있는 판정**에만 붙는다
  - 임계값은 전부 config에서 온다
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from config import DEFAULT_CONFIG
from core.context import build_context
from core.types import (
    BarMeta,
    CheckStatus,
    Comparator,
    GateCheck,
    GateResult,
    SessionState,
    SetupMetrics,
    SetupState,
    Stage,
    StrategyVerdict,
    Verdict,
)
from core.types import MarketRegime as Regime
from risk.planner import (
    build_risk_plan,
    build_risk_plans,
    compute_stop,
    entry_price,
    r_multiple_prices,
    size_position,
)

RISK = DEFAULT_CONFIG.risk


def frame(last_close: float = 100.0, n: int = 60) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=n)
    closes = [last_close] * n
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * n,
        },
        index=index,
    )


def context(price: float = 100.0, *, complete: bool = True):
    df = frame(price)
    return build_context(
        "TEST",
        df,
        DEFAULT_CONFIG,
        regime=Regime.RISK_ON,
        stage=Stage.STAGE_2,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED if complete else SessionState.OPEN,
            is_bar_complete=complete,
            bars_available=len(df),
            volume_judgements_reliable=complete,
        ),
    )


def verdict(
    outcome: Verdict = Verdict.BUY,
    *,
    pivot: float | None = 105.0,
    name: str = "minervini",
) -> StrategyVerdict:
    gate = GateResult(
        strategy=name,
        passed=outcome is not Verdict.REJECTED_BY_GATE,
        checks=[
            GateCheck(
                id="ok",
                label="통과",
                status=CheckStatus.PASS
                if outcome is not Verdict.REJECTED_BY_GATE
                else CheckStatus.FAIL,
                comparator=Comparator.BOOL,
                reason="테스트",
            )
        ],
        pass_count=1 if outcome is not Verdict.REJECTED_BY_GATE else 0,
        total=1,
    )
    scored = outcome is not Verdict.REJECTED_BY_GATE
    return StrategyVerdict(
        strategy_name=name,
        gate=gate,
        score=50.0 if scored else None,
        max_score=100.0 if scored else None,
        setup_state=SetupState.PIVOT_READY,
        setup_metrics=SetupMetrics(pivot_price=pivot),
        verdict=outcome,
    )


# ===========================================================================
# 진입가 — 피벗이 위면 피벗, 넘었으면 현재가
# ===========================================================================


def test_entry_is_the_pivot_when_price_is_still_below_it():
    """돌파 전이면 체결은 피벗에서 일어난다. 현재가로 계산하면 손절폭이 실제보다 넓어진다."""
    assert entry_price(context(100.0), verdict(pivot=105.0)) == pytest.approx(105.0)


def test_entry_is_the_current_price_once_the_pivot_is_cleared():
    assert entry_price(context(110.0), verdict(pivot=105.0)) == pytest.approx(110.0)


def test_entry_is_none_without_a_pivot():
    """진입 지점이 정의되지 않았으면 추측하지 않는다."""
    assert entry_price(context(100.0), verdict(pivot=None)) is None


# ===========================================================================
# 손절 — 두 후보 중 타이트한 쪽
# ===========================================================================


def test_stop_uses_the_tighter_of_atr_and_max_pct():
    """ATR이 크면 최대 손절폭이 이긴다 (= 더 높은 손절가)."""
    config = replace(RISK, stop_atr_multiple=2.0, max_stop_pct=8.0)
    # ATR 10 -> ATR 손절 80.0, 8% 손절 92.0 -> 타이트한 쪽은 92.0
    assert compute_stop(100.0, 10.0, config) == pytest.approx(92.0)


def test_stop_uses_atr_when_it_is_the_tighter_one():
    config = replace(RISK, stop_atr_multiple=2.0, max_stop_pct=8.0)
    # ATR 1 -> ATR 손절 98.0, 8% 손절 92.0 -> 타이트한 쪽은 98.0
    assert compute_stop(100.0, 1.0, config) == pytest.approx(98.0)


def test_stop_falls_back_to_max_pct_without_atr():
    """ATR을 못 구해도 손절은 있어야 한다 — 없앨 수 있는 값이 아니다."""
    assert compute_stop(100.0, None, replace(RISK, max_stop_pct=8.0)) == pytest.approx(92.0)


def test_stop_thresholds_come_from_config():
    tight = compute_stop(100.0, 1.0, replace(RISK, stop_atr_multiple=1.0))
    loose = compute_stop(100.0, 1.0, replace(RISK, stop_atr_multiple=3.0))
    assert tight > loose


def test_stop_is_always_below_entry():
    for atr in (0.1, 1.0, 50.0, None):
        stop = compute_stop(100.0, atr, RISK)
        assert stop is not None and 0.0 < stop < 100.0


# ===========================================================================
# 사이징
# ===========================================================================


def test_no_equity_means_no_share_count():
    """계좌를 모르면 주수를 지어내지 않는다. 0이 아니라 None이다."""
    assert size_position(100.0, 90.0, replace(RISK, account_equity=None)) == (None, None, None)


def test_shares_follow_the_risk_budget():
    config = replace(RISK, account_equity=100_000.0, risk_pct_per_trade=1.0)
    # 예산 1,000 / 1R 10 = 100주. 포지션 상한(25% = 25,000 / 100 = 250주)보다 작다.
    shares, value, risk_amount = size_position(100.0, 90.0, config)
    assert shares == 100
    assert value == pytest.approx(10_000.0)
    assert risk_amount == pytest.approx(1_000.0)


def test_position_cap_can_bind_before_the_risk_budget():
    """손절이 타이트하면 리스크 예산으로는 살 수 있어도 포지션 상한이 먼저 걸린다."""
    config = replace(
        RISK, account_equity=100_000.0, risk_pct_per_trade=1.0, max_position_pct=10.0
    )
    # 예산 1,000 / 1R 1 = 1,000주지만, 상한 10,000 / 100 = 100주가 이긴다.
    shares, value, _ = size_position(100.0, 99.0, config)
    assert shares == 100
    assert value == pytest.approx(10_000.0)


def test_risk_amount_is_the_filled_risk_not_the_budget():
    """상한에 깎이면 실제 리스크도 예산보다 작다. 예산을 실으면 화면의 숫자가 거짓이 된다."""
    config = replace(
        RISK, account_equity=100_000.0, risk_pct_per_trade=1.0, max_position_pct=10.0
    )
    _, _, risk_amount = size_position(100.0, 99.0, config)
    assert risk_amount == pytest.approx(100.0)  # 예산 1,000이 아니다


def test_r_multiple_prices_are_evenly_spaced():
    assert r_multiple_prices(100.0, 10.0, (1.0, 2.0, 3.0)) == [110.0, 120.0, 130.0]


# ===========================================================================
# 플랜 조립
# ===========================================================================


def test_plan_is_built_for_buy_and_watch():
    ctx = context(100.0)
    for outcome in (Verdict.BUY, Verdict.WATCH):
        assert build_risk_plan(ctx, verdict(outcome), RISK) is not None


@pytest.mark.parametrize("outcome", [Verdict.AVOID, Verdict.HOLD, Verdict.REJECTED_BY_GATE])
def test_no_plan_for_verdicts_without_entry_intent(outcome):
    """사지 않기로 한 방법론이 매수 계획을 내놓으면 안 된다."""
    assert build_risk_plan(context(100.0), verdict(outcome), RISK) is None


def test_no_plan_without_a_pivot():
    assert build_risk_plan(context(100.0), verdict(pivot=None), RISK) is None


def test_plan_carries_config_targets_in_order():
    config = replace(RISK, account_equity=50_000.0, r_targets=(1.0, 2.0, 4.0))
    plan = build_risk_plan(context(100.0), verdict(), config)
    assert [level.multiple for level in plan.r_levels] == [1.0, 2.0, 4.0]
    assert plan.r_levels[0].price < plan.r_levels[-1].price


def test_first_target_moves_the_stop_to_breakeven():
    plan = build_risk_plan(context(100.0), verdict(), RISK)
    assert "본전" in plan.r_levels[0].suggested_action


def test_incomplete_bar_is_stated_in_the_exit_rules():
    plan = build_risk_plan(context(100.0, complete=False), verdict(), RISK)
    assert any("미완성" in rule for rule in plan.exit_rules)


def test_plans_are_keyed_by_strategy_and_differ_with_pivots():
    ctx = context(100.0)
    verdicts = [
        verdict(name="minervini", pivot=105.0),
        verdict(name="weinstein", pivot=112.0),
        verdict(Verdict.AVOID, name="qullamaggie", pivot=108.0),
    ]
    plans = build_risk_plans(ctx, verdicts, replace(RISK, account_equity=100_000.0))

    assert set(plans) == {"minervini", "weinstein"}
    assert plans["minervini"].entry != plans["weinstein"].entry
    assert plans["minervini"].stop != plans["weinstein"].stop
