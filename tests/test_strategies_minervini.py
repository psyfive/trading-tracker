"""미너비니 전략 테스트.

핵심 검증 대상은 점수가 아니라 **구조**다:
  - 추세 템플릿 미달 종목은 REJECTED_BY_GATE이고 score가 None이어야 한다
  - 지표가 None이면 UNAVAILABLE이어야 한다. FAIL이 아니다
  - 게이트 탈락 종목에도 셋업 판정은 수행한다 (채점이 아니므로)
  - 임계값이 config에서 온다 (하드코딩 검출)
  - 같은 컨텍스트는 항상 같은 판정 (look-ahead 감사의 전제)

점수의 '정확한 값'은 검증하지 않는다. 그건 방법론 해석의 문제이지 코드의 문제가 아니다.
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
from strategies.minervini import MinerviniStrategy, detect_base

MINERVINI = DEFAULT_CONFIG.minervini

GATE_CHECK_IDS = [
    "price_above_sma150_200",
    "sma150_above_sma200",
    "sma200_trending_up",
    "sma50_above_sma150_200",
    "price_above_sma50",
    "above_52w_low",
    "near_52w_high",
    "rs_percentile",
]


def frame(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """종가 리스트로 OHLCV를 만든다. 고저는 종가 ±0.8%."""
    index = pd.bdate_range("2023-01-02", periods=len(closes))
    volumes = volumes or [1_000_000.0] * len(closes)
    return pd.DataFrame(
        {
            "open": [c * 0.999 for c in closes],
            "high": [c * 1.008 for c in closes],
            "low": [c * 0.992 for c in closes],
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def uptrend_with_base(n: int = 400, base_len: int = 45) -> pd.DataFrame:
    """추세 템플릿 8조건을 만족하는 상승 추세 + 마지막 구간 베이스(수축).

    베이스 구간은 진폭이 점점 줄어드는 진동이라 VCP 수축이 탐지된다.
    """
    rise = n - base_len
    closes = [100.0 + i * (200.0 / rise) for i in range(rise)]
    top = closes[-1]
    for j in range(base_len):
        amplitude = 8.0 * (1.0 - j / base_len)  # 점점 조여든다
        closes.append(top - amplitude * abs(math.sin(j / 3.0)))
    volumes = [2_000_000.0] * rise + [900_000.0] * base_len  # 베이스에서 거래량 건조
    return frame(closes, volumes)


def downtrend(n: int = 400) -> pd.DataFrame:
    closes = [300.0 - i * (200.0 / n) for i in range(n)]
    return frame(closes)


def context(
    df: pd.DataFrame,
    *,
    rs: float | None = 85.0,
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


def strategy(**overrides) -> MinerviniStrategy:
    return MinerviniStrategy(replace(MINERVINI, **overrides) if overrides else MINERVINI)


# ===========================================================================
# 게이트 — 추세 템플릿
# ===========================================================================


def test_uptrend_passes_the_full_trend_template():
    verdict = strategy().evaluate(context(uptrend_with_base()))
    assert verdict.gate.passed is True
    assert verdict.gate.pass_count == verdict.gate.total == 8


def test_gate_check_ids_are_stable():
    """프론트엔드가 id로 키잉하므로 집합과 순서가 고정되어야 한다."""
    verdict = strategy().evaluate(context(uptrend_with_base()))
    assert [c.id for c in verdict.gate.checks] == GATE_CHECK_IDS


def test_downtrend_stock_is_rejected_by_gate():
    verdict = strategy().evaluate(context(downtrend()))
    assert verdict.gate.passed is False
    assert verdict.verdict is Verdict.REJECTED_BY_GATE


def test_rejected_stock_has_no_score():
    """게이트 탈락 시 score is None. 0.0이 아니다."""
    verdict = strategy().evaluate(context(downtrend()))
    assert verdict.score is None
    assert verdict.max_score is None
    assert verdict.components == []


def test_build_score_is_not_called_when_gate_fails():
    """StrategyBase.evaluate()가 채점을 건너뛰는지 — 호출되면 즉시 터진다."""
    strat = strategy()
    strat.build_score = lambda ctx: pytest.fail("게이트 탈락인데 build_score가 호출됐다")
    strat.evaluate(context(downtrend()))


# ===========================================================================
# UNAVAILABLE — 데이터 없음은 조건 미달이 아니다
# ===========================================================================


def test_short_history_yields_unavailable_not_fail():
    """상장 6개월 종목의 sma200/52주 조건은 UNAVAILABLE이어야 한다."""
    verdict = strategy().evaluate(context(uptrend_with_base().tail(120)))
    statuses = {c.id: c.status for c in verdict.gate.checks}
    assert statuses["sma200_trending_up"] is CheckStatus.UNAVAILABLE
    assert statuses["above_52w_low"] is CheckStatus.UNAVAILABLE
    assert statuses["near_52w_high"] is CheckStatus.UNAVAILABLE
    assert verdict.gate.unavailable_count >= 3


def test_missing_rs_is_unavailable_and_blocks_the_and_gate():
    """RS 유니버스가 없으면 UNAVAILABLE. AND 게이트이므로 통과가 막힌다."""
    verdict = strategy().evaluate(context(uptrend_with_base(), rs=None))
    rs_check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert rs_check.status is CheckStatus.UNAVAILABLE
    assert rs_check.actual is None
    assert verdict.gate.passed is False
    assert verdict.gate.unavailable_count == 1
    assert verdict.gate.failed_checks == []


def test_unavailable_rejection_says_so_in_notes():
    """'데이터 없어 보류'와 '조건 미달 탈락'이 사용자에게 구분돼 보여야 한다."""
    verdict = strategy().evaluate(context(uptrend_with_base(), rs=None))
    assert any("UNAVAILABLE" in note for note in verdict.notes)


def test_unavailable_checks_carry_no_shortfall():
    verdict = strategy().evaluate(context(uptrend_with_base(), rs=None))
    for check in verdict.gate.unavailable_checks:
        assert check.shortfall_pct is None


# ===========================================================================
# 미달 마진 (E3)
# ===========================================================================


def test_failed_rs_check_reports_a_normalized_shortfall():
    verdict = strategy().evaluate(context(uptrend_with_base(), rs=35.0))
    rs_check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert rs_check.status is CheckStatus.FAIL
    # |35 - 70| / 70 * 100 = 50.0
    assert rs_check.shortfall_pct == pytest.approx(50.0)


def test_near_miss_has_smaller_shortfall_than_far_miss():
    """근접도 정렬의 근거. 같은 조건 실패라도 미달 폭이 구분돼야 한다."""
    near = strategy().evaluate(context(uptrend_with_base(), rs=65.0))
    far = strategy().evaluate(context(uptrend_with_base(), rs=20.0))
    near_gap = next(c for c in near.gate.checks if c.id == "rs_percentile").shortfall_pct
    far_gap = next(c for c in far.gate.checks if c.id == "rs_percentile").shortfall_pct
    assert near_gap < far_gap


# ===========================================================================
# 임계값은 config에서 온다
# ===========================================================================


def test_thresholds_come_from_config():
    """RS 기준을 올리면 같은 종목이 탈락해야 한다. 하드코딩이면 이 테스트가 깨진다."""
    df = uptrend_with_base()
    assert strategy(min_rs_percentile=70.0).evaluate(context(df, rs=75.0)).gate.passed is True
    assert strategy(min_rs_percentile=90.0).evaluate(context(df, rs=75.0)).gate.passed is False


def test_gate_threshold_values_are_reported_from_config():
    verdict = strategy().evaluate(context(uptrend_with_base()))
    rs_check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert rs_check.threshold == MINERVINI.min_rs_percentile


def test_buy_threshold_comes_from_config():
    """buy_min_score_pct를 100으로 올리면 어떤 셋업도 BUY가 되지 않는다."""
    df = uptrend_with_base()
    strict = strategy(buy_min_score_pct=100.1).evaluate(context(df))
    assert strict.verdict is not Verdict.BUY


# ===========================================================================
# 베이스 / 피벗 탐지
# ===========================================================================


def test_base_is_detected_in_a_consolidation():
    base = detect_base(context(uptrend_with_base()), MINERVINI)
    assert base is not None
    assert base.length_days >= MINERVINI.min_base_length_days
    assert base.low_price < base.base_high


def test_pivot_is_not_the_base_maximum():
    """회귀 테스트.

    피벗을 베이스 전체 최고가로 잡으면 오늘 봉의 고가가 포함되어 종가가 피벗을
    넘는 것이 수학적으로 불가능해지고, BREAKOUT/EXTENDED가 영원히 나오지 않는다.
    피벗은 마지막 수축의 고점(스윙 고점)이어야 한다.
    """
    base = detect_base(context(uptrend_with_base()), MINERVINI)
    assert base is not None
    assert base.pivot_price <= base.base_high
    assert base.pivot_price < base.base_high or base.length_days <= MINERVINI.swing_fractal_k * 2


def test_breakout_state_is_reachable():
    """돌파 상태가 실제로 도달 가능해야 한다 (피벗 정의 회귀 방지)."""
    df = uptrend_with_base()
    # 마지막 봉을 피벗 위로 밀어 올린다
    base = detect_base(context(df), MINERVINI)
    assert base is not None
    pushed = df.copy()
    breakout_price = base.pivot_price * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = breakout_price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = breakout_price * 1.005
    assert strategy().detect_setup(context(pushed)) in (
        SetupState.BREAKOUT,
        SetupState.EXTENDED,
    )


def test_extended_stock_is_avoided():
    df = uptrend_with_base()
    base = detect_base(context(df), MINERVINI)
    pushed = df.copy()
    far = base.pivot_price * (1.0 + (MINERVINI.extended_pct_above_pivot + 5.0) / 100.0)
    pushed.iloc[-1, pushed.columns.get_loc("close")] = far
    pushed.iloc[-1, pushed.columns.get_loc("high")] = far * 1.005
    verdict = strategy().evaluate(context(pushed))
    if verdict.gate.passed:
        assert verdict.setup_state is SetupState.EXTENDED
        assert verdict.verdict is Verdict.AVOID


def test_no_base_yields_no_setup():
    """베이스를 잡을 만큼 데이터가 없으면 NO_SETUP. 추측하지 않는다."""
    assert detect_base(context(uptrend_with_base().tail(30)), MINERVINI) is None


# ===========================================================================
# 셋업 판정은 게이트 탈락 종목에도 수행한다 (리뷰 E2)
# ===========================================================================


def test_setup_is_evaluated_even_when_the_gate_fails():
    """근소 탈락 종목이야말로 '피벗까지 얼마나 남았나'를 보고 싶은 대상이다."""
    verdict = strategy(min_rs_percentile=99.0).evaluate(context(uptrend_with_base(), rs=50.0))
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.setup_state is not SetupState.NO_SETUP
    assert verdict.setup_metrics.pivot_price is not None


def test_rejected_stock_keeps_setup_metrics_detail():
    verdict = strategy(min_rs_percentile=99.0).evaluate(context(uptrend_with_base(), rs=50.0))
    assert verdict.setup_metrics.detail is not None
    assert verdict.setup_metrics.detail.kind == "minervini"


# ===========================================================================
# 셋업 수치가 계약에 실린다
# ===========================================================================


def test_setup_metrics_carry_the_minervini_detail():
    verdict = strategy().evaluate(context(uptrend_with_base()))
    metrics = verdict.setup_metrics
    assert metrics.pivot_price is not None
    assert metrics.base_length_days is not None
    assert metrics.detail is not None
    assert metrics.detail.kind == "minervini"
    assert metrics.detail.contraction_count is not None


def test_vcp_contractions_are_detected_in_a_narrowing_base():
    """진폭이 줄어드는 베이스에서 수축이 잡히고 비율이 1 미만이어야 한다."""
    base = detect_base(context(uptrend_with_base()), MINERVINI)
    assert base is not None
    assert base.contraction_count >= MINERVINI.min_contractions
    assert base.contraction_ratio is not None
    assert base.contraction_ratio < 1.0


# ===========================================================================
# 미완성 봉 — 거래량 항목을 0점이 아니라 만점에서 제외
# ===========================================================================


def test_incomplete_bar_drops_the_volume_component_from_max_score():
    df = uptrend_with_base()
    complete = strategy().evaluate(context(df, volume_reliable=True))
    intraday = strategy().evaluate(context(df, volume_reliable=False))

    assert complete.gate.passed and intraday.gate.passed
    assert any(c.id == "volume_dryup" for c in complete.components)
    assert not any(c.id == "volume_dryup" for c in intraday.components)
    assert intraday.max_score < complete.max_score


def test_incomplete_bar_never_yields_buy():
    """거래량 확인 없이 매수 판정을 내지 않는다."""
    verdict = strategy().evaluate(context(uptrend_with_base(), volume_reliable=False))
    assert verdict.verdict is not Verdict.BUY


def test_incomplete_bar_explains_the_reduced_max_score():
    verdict = strategy().evaluate(context(uptrend_with_base(), volume_reliable=False))
    assert any("만점에서 제외" in note for note in verdict.notes)


# ===========================================================================
# 결정론 — look-ahead 감사의 전제
# ===========================================================================


def test_evaluation_is_deterministic():
    """같은 컨텍스트는 항상 같은 판정. 깨지면 감사가 무력화된다."""
    df = uptrend_with_base()
    strat = strategy()
    assert strat.evaluate(context(df)) == strat.evaluate(context(df))


def test_two_instances_agree():
    """인스턴스 간 상태 공유가 없어야 한다."""
    df = uptrend_with_base()
    assert strategy().evaluate(context(df)) == strategy().evaluate(context(df))


# ===========================================================================
# 실제 픽스처 — 계약을 통과하는가
# ===========================================================================


def test_runs_on_real_fixture_without_error(aapl):
    verdict = strategy().evaluate(context(aapl, ticker="AAPL"))
    assert verdict.strategy_name == "minervini"
    assert verdict.gate.total == 8


def test_score_never_exceeds_max_on_real_fixture(aapl):
    for cut in (300, 450, 600, len(aapl)):
        verdict = strategy().evaluate(context(aapl.iloc[:cut], ticker="AAPL"))
        if verdict.score is not None:
            assert 0.0 <= verdict.score <= verdict.max_score


# ===========================================================================
# 회귀 — actual/threshold 혼동
# ===========================================================================


def test_gate_checks_report_the_measured_value_not_the_threshold():
    """회귀 테스트.

    '주가 > 50일선' 조건에서 actual에도 50일선을 넣으면 actual == threshold가 되어
    미달 폭이 항상 0으로 나온다. 게이트 판정 자체는 맞아 보이지만
    shortfall_pct가 무의미해지고 프론트에 표시되는 숫자도 거짓말이 된다.
    """
    ctx = context(uptrend_with_base())
    checks = {c.id: c for c in strategy().evaluate(ctx).gate.checks}

    assert checks["price_above_sma50"].actual == pytest.approx(ctx.price)
    assert checks["price_above_sma50"].threshold == pytest.approx(ctx.indicators.sma50)
    assert checks["price_above_sma150_200"].actual == pytest.approx(ctx.price)
    assert checks["price_above_sma150_200"].threshold == pytest.approx(
        max(ctx.indicators.sma150, ctx.indicators.sma200)
    )


def test_price_below_sma50_produces_a_nonzero_shortfall():
    """actual/threshold가 뒤섞이면 이 값이 0이 되어 근접도 정렬이 죽는다."""
    verdict = strategy().evaluate(context(downtrend()))
    checks = {c.id: c for c in verdict.gate.checks}
    failing = checks["price_above_sma50"]
    assert failing.status is CheckStatus.FAIL
    assert failing.shortfall_pct is not None
    assert failing.shortfall_pct > 0.0
