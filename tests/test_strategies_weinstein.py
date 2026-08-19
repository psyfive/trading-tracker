"""와인스타인 Stage Analysis 전략 테스트.

미너비니 테스트와 같은 것을 본다 — 점수의 '정확한 값'이 아니라 **구조**다.
여기에 더해 이 전략에만 있는 두 가지를 잠근다:

  1. 게이트가 Stage 2와 **중복되는 조건을 세지 않는다** (진행률이 부풀지 않게)
  2. 시장 국면이 **이진 필터**로 작동한다 (점수를 깎는 것이 아니라 통과를 막는다)
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
from regime.market import classify_stage
from strategies.weinstein import WeinsteinStrategy, detect_range, stage2_age_days

WEINSTEIN = DEFAULT_CONFIG.weinstein

GATE_CHECK_IDS = [
    "stage_is_2",
    "price_above_ma10w",
    "market_not_risk_off",
    "rs_percentile",
    "dollar_volume",
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


def uptrend_with_range(n: int = 400, range_len: int = 40) -> pd.DataFrame:
    """상승 추세 뒤에 거래범위가 붙은 시계열. 범위 끝에서 상단에 근접한다."""
    rise = n - range_len
    closes = [100.0 + i * (200.0 / rise) for i in range(rise)]
    top = closes[-1]
    for j in range(range_len):
        closes.append(top - 6.0 * abs(math.sin(j / 4.0)))
    volumes = [1_500_000.0] * rise + [1_200_000.0] * range_len
    return frame(closes, volumes)


def downtrend(n: int = 400) -> pd.DataFrame:
    closes = [300.0 - i * (200.0 / n) for i in range(n)]
    return frame(closes)


def context(
    df: pd.DataFrame,
    *,
    rs: float | None = 80.0,
    stage: Stage = Stage.STAGE_2,
    regime: MarketRegime = MarketRegime.RISK_ON,
    volume_reliable: bool = True,
    ticker: str = "TEST",
):
    return build_context(
        ticker,
        df,
        DEFAULT_CONFIG,
        regime=regime,
        stage=stage,
        rs_percentile=rs,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED if volume_reliable else SessionState.OPEN,
            is_bar_complete=volume_reliable,
            bars_available=len(df),
            volume_judgements_reliable=volume_reliable,
        ),
    )


def strategy(**overrides) -> WeinsteinStrategy:
    return WeinsteinStrategy(replace(WEINSTEIN, **overrides) if overrides else WEINSTEIN)


# ===========================================================================
# 게이트 구성 — Stage 2와 중복되는 조건을 세지 않는다
# ===========================================================================


def test_gate_check_ids_are_stable():
    verdict = strategy().evaluate(context(uptrend_with_range()))
    assert [c.id for c in verdict.gate.checks] == GATE_CHECK_IDS


def test_gate_does_not_recount_what_stage_2_already_implies():
    """설계 잠금.

    Stage 2 판정은 '30주선 위 + 30주선 상승'을 이미 함의한다. 같은 사실을 별도
    조건으로 또 세면 pass_count/total 진행률이 부풀고, 워치리스트의 게이트 근접도
    정렬이 실제보다 낙관적으로 나온다.
    """
    ids = {c.id for c in strategy().evaluate(context(uptrend_with_range())).gate.checks}
    assert "price_above_ma30w" not in ids
    assert "ma30w_trending_up" not in ids


def test_stage_2_stock_passes_the_gate():
    verdict = strategy().evaluate(context(uptrend_with_range()))
    assert verdict.gate.passed is True
    assert verdict.gate.pass_count == verdict.gate.total == 5


def test_non_stage_2_is_rejected_by_gate():
    verdict = strategy().evaluate(context(uptrend_with_range(), stage=Stage.STAGE_3))
    assert verdict.gate.passed is False
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.score is None and verdict.components == []


def test_downtrend_fails_the_short_ma_condition():
    verdict = strategy().evaluate(context(downtrend(), stage=Stage.STAGE_4))
    statuses = {c.id: c.status for c in verdict.gate.checks}
    assert statuses["stage_is_2"] is CheckStatus.FAIL
    assert statuses["price_above_ma10w"] is CheckStatus.FAIL


# ===========================================================================
# 시장 국면은 이진 필터다 (점수를 깎는 것이 아니다)
# ===========================================================================


def test_risk_off_market_blocks_the_gate():
    verdict = strategy().evaluate(context(uptrend_with_range(), regime=MarketRegime.RISK_OFF))
    check = next(c for c in verdict.gate.checks if c.id == "market_not_risk_off")
    assert check.status is CheckStatus.FAIL
    assert verdict.gate.passed is False
    assert verdict.verdict is Verdict.REJECTED_BY_GATE


def test_caution_market_still_passes():
    """CAUTION은 역풍 경고이지 진입 금지가 아니다."""
    verdict = strategy().evaluate(context(uptrend_with_range(), regime=MarketRegime.CAUTION))
    check = next(c for c in verdict.gate.checks if c.id == "market_not_risk_off")
    assert check.status is CheckStatus.PASS


def test_bool_checks_carry_no_numeric_fields():
    """BOOL 조건에 actual/threshold를 채우면 프론트가 '>= None' 문구를 만든다."""
    verdict = strategy().evaluate(context(uptrend_with_range()))
    for check_id in ("stage_is_2", "market_not_risk_off"):
        check = next(c for c in verdict.gate.checks if c.id == check_id)
        assert check.actual is None and check.threshold is None
        assert check.shortfall_pct is None


# ===========================================================================
# UNAVAILABLE — 데이터 없음은 조건 미달이 아니다
# ===========================================================================


def test_undefined_stage_is_unavailable_not_fail():
    """Stage를 주입받지 못했으면 '조건 미달'이 아니라 '확인 못 함'이다."""
    verdict = strategy().evaluate(context(uptrend_with_range(), stage=Stage.UNDEFINED))
    check = next(c for c in verdict.gate.checks if c.id == "stage_is_2")
    assert check.status is CheckStatus.UNAVAILABLE
    assert verdict.gate.passed is False
    assert verdict.gate.failed_checks == []


def test_missing_rs_is_unavailable_and_blocks_the_and_gate():
    verdict = strategy().evaluate(context(uptrend_with_range(), rs=None))
    check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert check.status is CheckStatus.UNAVAILABLE
    assert verdict.gate.passed is False
    assert verdict.gate.unavailable_count == 1


def test_short_history_yields_unavailable_checks():
    verdict = strategy().evaluate(context(uptrend_with_range().tail(30)))
    statuses = {c.id: c.status for c in verdict.gate.checks}
    assert statuses["price_above_ma10w"] is CheckStatus.UNAVAILABLE
    assert statuses["dollar_volume"] is CheckStatus.UNAVAILABLE


# ===========================================================================
# 임계값은 config에서 온다
# ===========================================================================


def test_thresholds_come_from_config():
    df = uptrend_with_range()
    assert strategy(min_rs_percentile=50.0).evaluate(context(df, rs=60.0)).gate.passed is True
    assert strategy(min_rs_percentile=90.0).evaluate(context(df, rs=60.0)).gate.passed is False


def test_liquidity_threshold_comes_from_config():
    df = uptrend_with_range()
    passing = strategy().evaluate(context(df))
    strict = strategy(min_dollar_volume=1e15).evaluate(context(df))
    assert passing.gate.passed is True
    check = next(c for c in strict.gate.checks if c.id == "dollar_volume")
    assert check.status is CheckStatus.FAIL
    assert check.threshold == 1e15


def test_buy_threshold_comes_from_config():
    verdict = strategy(buy_min_score_pct=100.1).evaluate(context(uptrend_with_range()))
    assert verdict.verdict is not Verdict.BUY


# ===========================================================================
# Stage 2 신선도 — 이 전략 고유 계산
# ===========================================================================


def test_stage2_age_counts_consecutive_days():
    age = stage2_age_days(context(uptrend_with_range()), WEINSTEIN)
    assert age is not None and age > 0


def test_stage2_age_is_none_without_enough_history():
    """30주선이 없으면 신선도를 지어내지 않는다."""
    assert stage2_age_days(context(uptrend_with_range().tail(100)), WEINSTEIN) is None


def test_stage2_age_is_zero_in_a_downtrend():
    assert stage2_age_days(context(downtrend()), WEINSTEIN) == 0


def test_fresh_stage2_scores_higher_than_stale_one():
    """신선도 항목이 실제로 나이에 반응해야 한다. 상수면 채점이 무의미하다."""
    ctx = context(uptrend_with_range())
    fresh = strategy(ideal_stage2_age_days=1000, max_stage2_age_days=2000).evaluate(ctx)
    stale = strategy(ideal_stage2_age_days=1, max_stage2_age_days=2).evaluate(ctx)
    fresh_score = next(c for c in fresh.components if c.id == "stage_freshness").earned
    stale_score = next(c for c in stale.components if c.id == "stage_freshness").earned
    assert fresh_score > stale_score


# ===========================================================================
# 거래범위 / 저항선 탐지
# ===========================================================================


def test_range_is_detected():
    trading_range = detect_range(context(uptrend_with_range()), WEINSTEIN)
    assert trading_range is not None
    assert trading_range.length_days >= WEINSTEIN.min_range_length_days
    assert trading_range.low_price < trading_range.range_high


def test_breakout_state_is_reachable():
    """저항선을 오늘 고가로 잡으면 돌파가 영원히 탐지되지 않는다 (미너비니 회귀와 같은 함정)."""
    df = uptrend_with_range()
    trading_range = detect_range(context(df), WEINSTEIN)
    assert trading_range is not None
    pushed = df.copy()
    price = trading_range.resistance * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = price * 1.005
    assert strategy().detect_setup(context(pushed)) in (
        SetupState.BREAKOUT,
        SetupState.EXTENDED,
    )


def test_extended_stock_is_avoided():
    df = uptrend_with_range()
    trading_range = detect_range(context(df), WEINSTEIN)
    pushed = df.copy()
    far = trading_range.resistance * (1.0 + (WEINSTEIN.extended_pct_above_pivot + 5.0) / 100.0)
    pushed.iloc[-1, pushed.columns.get_loc("close")] = far
    pushed.iloc[-1, pushed.columns.get_loc("high")] = far * 1.005
    verdict = strategy().evaluate(context(pushed))
    if verdict.gate.passed:
        assert verdict.setup_state is SetupState.EXTENDED
        assert verdict.verdict is Verdict.AVOID


def test_contracting_is_never_used():
    """CONTRACTING은 VCP 어휘다. Stage Analysis는 수축 횟수를 세지 않는다."""
    for df in (uptrend_with_range(), downtrend(), uptrend_with_range(range_len=25)):
        assert strategy().detect_setup(context(df)) is not SetupState.CONTRACTING


# ===========================================================================
# 셋업 판정은 게이트 탈락 종목에도 수행한다
# ===========================================================================


def test_setup_is_evaluated_even_when_the_gate_fails():
    verdict = strategy().evaluate(context(uptrend_with_range(), stage=Stage.STAGE_3))
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.setup_metrics.pivot_price is not None
    assert verdict.setup_metrics.detail is not None
    assert verdict.setup_metrics.detail.kind == "weinstein"


def test_setup_metrics_carry_the_weinstein_detail():
    metrics = strategy().evaluate(context(uptrend_with_range())).setup_metrics
    assert metrics.detail.kind == "weinstein"
    assert metrics.detail.ma30w is not None
    assert metrics.detail.stage2_age_days is not None
    assert metrics.base_length_days is not None


# ===========================================================================
# 미완성 봉
# ===========================================================================


def test_incomplete_bar_drops_the_volume_component_from_max_score():
    df = uptrend_with_range()
    complete = strategy().evaluate(context(df, volume_reliable=True))
    intraday = strategy().evaluate(context(df, volume_reliable=False))
    assert complete.gate.passed and intraday.gate.passed
    assert any(c.id == "breakout_volume" for c in complete.components)
    assert not any(c.id == "breakout_volume" for c in intraday.components)
    assert intraday.max_score < complete.max_score


def test_incomplete_bar_never_yields_buy():
    verdict = strategy().evaluate(context(uptrend_with_range(), volume_reliable=False))
    assert verdict.verdict is not Verdict.BUY


# ===========================================================================
# 결정론 / 실제 픽스처
# ===========================================================================


def test_evaluation_is_deterministic():
    df = uptrend_with_range()
    strat = strategy()
    assert strat.evaluate(context(df)) == strat.evaluate(context(df))


def test_two_instances_agree():
    df = uptrend_with_range()
    assert strategy().evaluate(context(df)) == strategy().evaluate(context(df))


def test_runs_on_real_fixture_without_error(aapl):
    verdict = strategy().evaluate(context(aapl, ticker="AAPL"))
    assert verdict.strategy_name == "weinstein"
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
    ctx = context(uptrend_with_range())
    checks = {c.id: c for c in strategy().evaluate(ctx).gate.checks}
    assert checks["price_above_ma10w"].actual == pytest.approx(ctx.price)
    assert checks["price_above_ma10w"].threshold == pytest.approx(ctx.indicators.sma50)


def test_failed_rs_check_reports_a_normalized_shortfall():
    verdict = strategy().evaluate(context(uptrend_with_range(), rs=25.0))
    check = next(c for c in verdict.gate.checks if c.id == "rs_percentile")
    assert check.status is CheckStatus.FAIL
    # |25 - 50| / 50 * 100 = 50.0
    assert check.shortfall_pct == pytest.approx(50.0)


# ===========================================================================
# 돌파 거래량 — BUY의 필요조건 (리뷰 B1)
# ===========================================================================


def breakout_frame(volume_multiple: float = 1.0) -> pd.DataFrame:
    """저항선을 돌파한 시계열. 돌파 구간(최근 2k봉)의 거래량을 배수로 지정한다."""
    df = uptrend_with_range()
    trading_range = detect_range(context(df), WEINSTEIN)
    assert trading_range is not None

    pushed = df.copy()
    price = trading_range.resistance * 1.02
    pushed.iloc[-1, pushed.columns.get_loc("close")] = price
    pushed.iloc[-1, pushed.columns.get_loc("high")] = price * 1.005

    span = WEINSTEIN.swing_fractal_k * 2
    column = pushed.columns.get_loc("volume")
    pushed.iloc[-span:, column] = pushed["volume"].iloc[-span:] * volume_multiple
    return pushed


def test_breakout_without_volume_is_not_a_buy():
    """'와인스타인의 돌파는 거래량이 조건'이라는 문구를 로직이 실제로 지켜야 한다."""
    verdict = strategy().evaluate(context(breakout_frame(volume_multiple=1.0)))
    assert verdict.setup_state is SetupState.BREAKOUT
    assert verdict.verdict is Verdict.WATCH
    assert any("돌파 거래량" in note for note in verdict.notes)


def test_breakout_with_volume_can_be_a_buy():
    verdict = strategy().evaluate(context(breakout_frame(volume_multiple=5.0)))
    assert verdict.setup_state is SetupState.BREAKOUT
    assert verdict.verdict is Verdict.BUY


def test_breakout_volume_requirement_comes_from_config():
    df = breakout_frame(volume_multiple=5.0)
    assert strategy().evaluate(context(df)).verdict is Verdict.BUY
    assert strategy(volume_confirm_ratio=99.0).evaluate(context(df)).verdict is Verdict.WATCH


def test_high_score_cannot_replace_the_missing_breakout_volume():
    """다른 항목이 만점이어도 거래량 0점을 메워 BUY가 되면 안 된다 (채점만으로는 못 막는다)."""
    verdict = strategy().evaluate(context(breakout_frame(volume_multiple=1.0), rs=100.0))
    assert verdict.score is not None
    assert verdict.verdict is not Verdict.BUY


# ===========================================================================
# Stage 신선도 — Stage 판정과 같은 자를 쓴다 (리뷰 M1, E2)
# ===========================================================================


def test_stage2_age_uses_the_same_rule_as_the_stage_classifier():
    """게이트의 Stage(주입값)와 신선도가 세는 Stage 2가 같은 조건식이어야 한다."""
    df = uptrend_with_range()
    ctx = context(df)
    age = stage2_age_days(ctx, WEINSTEIN)
    assert age is not None
    # 마지막 봉이 Stage 2라면 age >= 1, 아니라면 0이어야 한다.
    assert (classify_stage(df, DEFAULT_CONFIG.regime) is Stage.STAGE_2) == (age >= 1)


def test_stage2_age_is_capped_and_does_not_scan_the_whole_history():
    """계산 창이 상수로 묶여야 백테스트가 O(n^2)로 되돌아가지 않는다."""
    long_uptrend = frame([100.0 + i * 0.5 for i in range(900)])
    age = stage2_age_days(context(long_uptrend), WEINSTEIN)
    assert age == WEINSTEIN.max_stage2_age_days


def test_stage2_age_cap_does_not_change_the_freshness_score():
    """상한에서 자른 값이든 그 이상이든 신선도는 0점이다 — 정보 손실이 없다."""
    strict = strategy(max_stage2_age_days=WEINSTEIN.max_stage2_age_days)
    long_uptrend = frame([100.0 + i * 0.5 for i in range(900)])
    _, _, components = strict.build_score(context(long_uptrend))
    freshness = next(c for c in components if c.id == "stage_freshness")
    assert freshness.earned == 0.0


def test_stage_parameters_cannot_drift_from_the_regime_config():
    """스윕에서 한쪽만 바꾸면 화면의 Stage와 점수의 Stage가 갈라진다 — 즉시 터져야 한다."""
    with pytest.raises(ValueError, match="Stage"):
        replace(DEFAULT_CONFIG, weinstein=replace(WEINSTEIN, ma_period_daily=100))
    with pytest.raises(ValueError, match="Stage"):
        replace(DEFAULT_CONFIG, weinstein=replace(WEINSTEIN, stage_flat_slope_pct=0.0))
