"""Qullamaggie 전략 테스트.

구조 검증은 다른 전략과 같고, 이 전략에만 있는 두 가지를 추가로 잠근다:

  1. **직전 급등(prior move)** 이 게이트 조건이고, 측정 구간이 없으면 UNAVAILABLE이다
  2. ADR·거래대금이 **가산점이 아니라 자격 요건**이다 (게이트에 있다)
"""

from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd
import pytest

from config import DEFAULT_CONFIG
from core.context import build_context
from core.types import (
    BarMeta,
    CheckStatus,
    MarketRegime,
    SessionState,
    SetupState,
    Stage,
    Verdict,
)
from strategies.qullamaggie import QullamaggieStrategy, detect_consolidation

QULLA = DEFAULT_CONFIG.qullamaggie

GATE_CHECK_IDS = [
    "prior_move",
    "adr_pct",
    "dollar_volume",
    "rs_percentile",
    "price_above_ema21",
]


def frame(closes: list[float], volumes: list[float], spread: float = 0.02) -> pd.DataFrame:
    """종가·거래량 리스트로 OHLCV를 만든다.

    spread가 ADR을 결정한다. 기본 2%면 high/low = 1.04 이므로 ADR(20) ~= 4%로
    게이트의 min_adr_pct(3%)를 넘긴다. 이 전략은 변동성이 자격 요건이라
    저변동성 시계열로는 어떤 테스트도 게이트를 통과하지 못한다.
    """
    index = pd.bdate_range("2023-01-02", periods=len(closes))
    return pd.DataFrame(
        {
            "open": [c * 0.999 for c in closes],
            "high": [c * (1.0 + spread) for c in closes],
            "low": [c * (1.0 - spread) for c in closes],
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def surge_then_consolidation(
    n: int = 400, consolidation_len: int = 40, spread: float = 0.02
) -> pd.DataFrame:
    """급등 후 컨솔리데이션. 컨솔 끝에서 상단에 근접하고 거래량이 마른다."""
    rise = n - consolidation_len
    closes = [100.0 + i * (300.0 / rise) for i in range(rise)]
    top = closes[-1]
    for j in range(consolidation_len):
        # 진폭이 줄어드는 진동 — 컨솔 끝에서 상단에 근접한다 (진입 후보 상태)
        amplitude = 20.0 * (1.0 - j / consolidation_len)
        closes.append(top - amplitude * abs(math.sin(j / 5.0)))

    volumes = [2_000_000.0] * rise
    for j in range(consolidation_len):
        volumes.append(1_400_000.0 - 800_000.0 * j / consolidation_len)
    return frame(closes, volumes, spread)


def flat_and_quiet(n: int = 400) -> pd.DataFrame:
    """급등도 변동성도 없는 시계열. 이 전략의 대상이 아니다."""
    closes = [100.0 + math.sin(i / 10.0) for i in range(n)]
    return frame(closes, [2_000_000.0] * n, spread=0.002)


def context(
    df: pd.DataFrame,
    *,
    rs: float | None = 90.0,
    volume_reliable: bool = True,
    ticker: str = "TEST",
):
    return build_context(
        ticker,
        df,
        DEFAULT_CONFIG,
        regime=MarketRegime.RISK_ON,
        stage=Stage.STAGE_2,
        rs_percentile=rs,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED if volume_reliable else SessionState.OPEN,
            is_bar_complete=volume_reliable,
            bars_available=len(df),
            volume_judgements_reliable=volume_reliable,
        ),
    )


def strategy(**overrides) -> QullamaggieStrategy:
    return QullamaggieStrategy(replace(QULLA, **overrides) if overrides else QULLA)


# ===========================================================================
# 게이트
# ===========================================================================


def test_gate_check_ids_are_stable():
    verdict = strategy().evaluate(context(surge_then_consolidation()))
    assert [c.id for c in verdict.gate.checks] == GATE_CHECK_IDS


def test_surge_then_consolidation_passes_the_gate():
    verdict = strategy().evaluate(context(surge_then_consolidation()))
    assert verdict.gate.passed is True
    assert verdict.gate.pass_count == verdict.gate.total == 5


def test_quiet_stock_is_rejected_by_gate():
    """급등도 변동성도 없으면 추세가 곱더라도 이 방법론의 대상이 아니다."""
    verdict = strategy().evaluate(context(flat_and_quiet()))
    assert verdict.gate.passed is False
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.score is None and verdict.components == []


def test_low_volatility_fails_the_adr_condition_with_a_shortfall():
    """ADR은 가산점이 아니라 자격 요건이다. 미달 폭이 나와야 근접도 정렬이 산다."""
    verdict = strategy().evaluate(context(surge_then_consolidation(spread=0.005)))
    check = next(c for c in verdict.gate.checks if c.id == "adr_pct")
    assert check.status is CheckStatus.FAIL
    assert check.shortfall_pct is not None and check.shortfall_pct > 0.0


def test_liquidity_is_a_gate_condition_not_a_bonus():
    verdict = strategy(min_dollar_volume=1e15).evaluate(context(surge_then_consolidation()))
    check = next(c for c in verdict.gate.checks if c.id == "dollar_volume")
    assert check.status is CheckStatus.FAIL
    assert verdict.gate.passed is False


# ===========================================================================
# 직전 급등 — 이 전략의 핵심 게이트
# ===========================================================================


def test_prior_move_is_measured_before_the_consolidation():
    consolidation = detect_consolidation(context(surge_then_consolidation()), QULLA)
    assert consolidation is not None
    assert consolidation.prior_move_pct is not None
    assert consolidation.prior_move_pct >= QULLA.min_prior_move_pct


def test_prior_move_is_unavailable_without_enough_history():
    """측정 창이 없으면 '급등 없음(FAIL)'이 아니라 '확인 못 함(UNAVAILABLE)'이다.

    짧은 데이터로 계산한 작은 상승률을 미달로 단정하면 신규 상장주가 조용히 탈락한다.
    """
    short = surge_then_consolidation(n=120)
    verdict = strategy().evaluate(context(short))
    check = next(c for c in verdict.gate.checks if c.id == "prior_move")
    assert check.status is CheckStatus.UNAVAILABLE
    assert check.actual is None
    assert verdict.gate.passed is False


def test_prior_move_threshold_comes_from_config():
    df = surge_then_consolidation()
    assert strategy(min_prior_move_pct=10.0).evaluate(context(df)).gate.passed is True
    assert strategy(min_prior_move_pct=500.0).evaluate(context(df)).gate.passed is False


# ===========================================================================
# UNAVAILABLE / 임계값
# ===========================================================================


def test_missing_rs_is_unavailable_and_blocks_the_and_gate():
    verdict = strategy().evaluate(context(surge_then_consolidation(), rs=None))
    check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert check.status is CheckStatus.UNAVAILABLE
    assert verdict.gate.passed is False
    assert verdict.gate.failed_checks == []


def test_thresholds_come_from_config():
    df = surge_then_consolidation()
    assert strategy(min_rs_percentile=80.0).evaluate(context(df, rs=85.0)).gate.passed is True
    assert strategy(min_rs_percentile=95.0).evaluate(context(df, rs=85.0)).gate.passed is False


def test_buy_threshold_comes_from_config():
    verdict = strategy(buy_min_score_pct=100.1).evaluate(context(surge_then_consolidation()))
    assert verdict.verdict is not Verdict.BUY


# ===========================================================================
# 컨솔리데이션 / 피벗
# ===========================================================================


def test_consolidation_is_detected():
    consolidation = detect_consolidation(context(surge_then_consolidation()), QULLA)
    assert consolidation is not None
    assert consolidation.length_days >= QULLA.consolidation_min_days
    assert consolidation.low_price < consolidation.high_price


def test_breakout_state_is_reachable():
    df = surge_then_consolidation()
    consolidation = detect_consolidation(context(df), QULLA)
    pushed = df.copy()
    price = consolidation.pivot_price * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = price * 1.005
    assert strategy().detect_setup(context(pushed)) in (
        SetupState.BREAKOUT,
        SetupState.EXTENDED,
    )


def test_extended_stock_is_avoided():
    df = surge_then_consolidation()
    consolidation = detect_consolidation(context(df), QULLA)
    pushed = df.copy()
    far = consolidation.pivot_price * (1.0 + (QULLA.extended_pct_above_pivot + 5.0) / 100.0)
    pushed.iloc[-1, pushed.columns.get_loc("close")] = far
    pushed.iloc[-1, pushed.columns.get_loc("high")] = far * 1.005
    verdict = strategy().evaluate(context(pushed))
    if verdict.gate.passed:
        assert verdict.setup_state is SetupState.EXTENDED
        assert verdict.verdict is Verdict.AVOID


def test_no_consolidation_yields_no_setup():
    assert detect_consolidation(context(surge_then_consolidation().tail(40)), QULLA) is None


# ===========================================================================
# 셋업 판정은 게이트 탈락 종목에도 수행한다
# ===========================================================================


def test_setup_is_evaluated_even_when_the_gate_fails():
    verdict = strategy(min_rs_percentile=99.9).evaluate(
        context(surge_then_consolidation(), rs=50.0)
    )
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.setup_metrics.pivot_price is not None
    assert verdict.setup_metrics.detail.kind == "qullamaggie"


def test_setup_metrics_carry_the_qullamaggie_detail():
    metrics = strategy().evaluate(context(surge_then_consolidation())).setup_metrics
    assert metrics.detail.kind == "qullamaggie"
    assert metrics.detail.prior_move_pct is not None
    assert metrics.detail.ma_distance_pct is not None
    assert metrics.base_length_days is not None


def test_each_strategy_keeps_its_own_setup_vocabulary():
    """같은 종목·같은 날이라도 셋업 어휘는 방법론마다 다르다.

    각자의 SetupMetrics에 보존되는 것이 이 프로젝트의 설계다
    (IndicatorSnapshot에 넣으면 한쪽이 다른 쪽의 정의를 강요당한다).
    """
    from strategies.minervini import MinerviniStrategy

    ctx = context(surge_then_consolidation())
    qulla = strategy().build_setup_metrics(ctx)
    minervini = MinerviniStrategy(DEFAULT_CONFIG.minervini).build_setup_metrics(ctx)

    assert qulla.detail.kind == "qullamaggie"
    assert minervini.detail.kind == "minervini"
    assert qulla.detail.prior_move_pct is not None
    assert minervini.detail.contraction_ratio is not None
    assert not hasattr(qulla.detail, "contraction_ratio")


# ===========================================================================
# 미완성 봉
# ===========================================================================


def test_incomplete_bar_drops_the_volume_component_from_max_score():
    df = surge_then_consolidation()
    complete = strategy().evaluate(context(df, volume_reliable=True))
    intraday = strategy().evaluate(context(df, volume_reliable=False))
    assert complete.gate.passed and intraday.gate.passed
    assert any(c.id == "volume_dryup" for c in complete.components)
    assert not any(c.id == "volume_dryup" for c in intraday.components)
    assert intraday.max_score < complete.max_score


def test_incomplete_bar_never_yields_buy():
    verdict = strategy().evaluate(context(surge_then_consolidation(), volume_reliable=False))
    assert verdict.verdict is not Verdict.BUY


# ===========================================================================
# 결정론 / 실제 픽스처
# ===========================================================================


def test_evaluation_is_deterministic():
    df = surge_then_consolidation()
    strat = strategy()
    assert strat.evaluate(context(df)) == strat.evaluate(context(df))


def test_two_instances_agree():
    df = surge_then_consolidation()
    assert strategy().evaluate(context(df)) == strategy().evaluate(context(df))


def test_runs_on_real_fixture_without_error(aapl):
    verdict = strategy().evaluate(context(aapl, ticker="AAPL"))
    assert verdict.strategy_name == "qullamaggie"
    assert verdict.gate.total == 5


def test_score_never_exceeds_max_on_real_fixture(aapl):
    for cut in (300, 450, 600, len(aapl)):
        verdict = strategy().evaluate(context(aapl.iloc[:cut], ticker="AAPL"))
        if verdict.score is not None:
            assert 0.0 <= verdict.score <= verdict.max_score


# ===========================================================================
# 회귀 — actual/threshold 혼동
# ===========================================================================


def test_gate_checks_report_the_measured_value_not_the_threshold():
    ctx = context(surge_then_consolidation())
    checks = {c.id: c for c in strategy().evaluate(ctx).gate.checks}
    assert checks["price_above_ema21"].actual == pytest.approx(ctx.price)
    assert checks["price_above_ema21"].threshold == pytest.approx(ctx.indicators.ema21)
    assert checks["adr_pct"].actual == pytest.approx(ctx.indicators.adr20_pct, abs=1e-4)


# ===========================================================================
# 돌파 거래량 — BUY의 필요조건 (리뷰 B1)
# ===========================================================================


def breakout_frame(volume_multiple: float = 1.0) -> pd.DataFrame:
    """컨솔 상단을 돌파한 시계열. 돌파 구간(최근 2k봉)의 거래량을 배수로 지정한다."""
    df = surge_then_consolidation()
    consolidation = detect_consolidation(context(df), QULLA)
    assert consolidation is not None

    pushed = df.copy()
    price = consolidation.pivot_price * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = price * 1.005

    span = QULLA.swing_fractal_k * 2
    column = pushed.columns.get_loc("volume")
    pushed.iloc[-span:, column] = pushed["volume"].iloc[-span:] * volume_multiple
    return pushed


def test_breakout_without_volume_is_not_a_buy():
    """컨솔 건조도는 '조용히 조였는가'이지 '돌파를 확인했는가'가 아니다."""
    verdict = strategy().evaluate(context(breakout_frame(volume_multiple=1.0)))
    assert verdict.setup_state is SetupState.BREAKOUT
    assert verdict.verdict is Verdict.WATCH
    assert any("돌파 거래량" in note for note in verdict.notes)


def test_breakout_with_volume_can_be_a_buy():
    verdict = strategy(buy_min_score_pct=50.0).evaluate(
        context(breakout_frame(volume_multiple=6.0))
    )
    assert verdict.setup_state is SetupState.BREAKOUT
    assert verdict.verdict is Verdict.BUY


def test_breakout_volume_threshold_comes_from_config():
    df = breakout_frame(volume_multiple=6.0)

    def verdict_with(ratio: float):
        return strategy(breakout_volume_ratio=ratio, buy_min_score_pct=50.0).evaluate(context(df))

    assert verdict_with(1.5).verdict is Verdict.BUY
    assert verdict_with(99.0).verdict is Verdict.WATCH


def test_breakout_volume_ratio_is_reported_in_setup_metrics():
    detail = strategy().evaluate(context(breakout_frame(6.0))).setup_metrics.detail
    assert detail is not None
    assert detail.breakout_volume_ratio is not None


def test_volume_dryup_worst_case_comes_from_config():
    """'컨솔 평균과 같으면 0점'은 판정 기준이지 항등원이 아니다 — config에서 온다."""
    # 건조도가 이상(0.7)과 최악(1.0) 사이에 오도록 컨솔 후반 거래량을 올린다.
    # 양 극단에 있으면 어떤 worst 값을 넣어도 점수가 같아 테스트가 아무것도 잠그지 못한다.
    df = surge_then_consolidation()
    span = QULLA.swing_fractal_k * 2
    column = df.columns.get_loc("volume")
    df.iloc[-span:, column] = df["volume"].iloc[-span:] * 1.5

    lenient = strategy(max_volume_dryup_ratio=5.0)
    strict = strategy(max_volume_dryup_ratio=QULLA.ideal_volume_dryup_ratio)

    def dryup(strat):
        components = strat.build_score(context(df))[2]
        return next(c.earned for c in components if c.id == "volume_dryup")

    assert dryup(lenient) > dryup(strict)


def test_ema21_is_not_labelled_as_a_20_day_line():
    """계약 필드는 ema21이다. 라벨만 '20일선'이면 화면에서 다른 선을 보고 검증하게 된다."""
    verdict = strategy().evaluate(context(surge_then_consolidation()))
    check = next(c for c in verdict.gate.checks if c.id == "price_above_ema21")
    assert "20일선" not in check.label
    assert "EMA21" in check.label


# ===========================================================================
# 진입 정의 — 돌파를 확인하고 살 것인가 (config로 재는 대상)
# ===========================================================================


def test_pivot_ready_is_a_buy_by_default():
    verdict = strategy().evaluate(context(surge_then_consolidation()))
    assert verdict.setup_state is SetupState.PIVOT_READY
    assert verdict.verdict is Verdict.BUY


def test_requiring_a_breakout_downgrades_pivot_ready_to_watch():
    verdict = strategy(require_breakout_for_buy=True).evaluate(
        context(surge_then_consolidation())
    )
    assert verdict.setup_state is SetupState.PIVOT_READY
    assert verdict.verdict is Verdict.WATCH
    assert any("돌파 확인 후" in note for note in verdict.notes)


def test_requiring_a_breakout_does_not_touch_other_setup_states():
    df = surge_then_consolidation()
    consolidation = detect_consolidation(context(df), QULLA)
    pushed = df.copy()
    price = consolidation.pivot_price * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = price * 1.005

    off = strategy().evaluate(context(pushed))
    on = strategy(require_breakout_for_buy=True).evaluate(context(pushed))
    assert off.setup_state is not SetupState.PIVOT_READY
    assert on.verdict is off.verdict
