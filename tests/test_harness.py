"""백테스트 하네스 테스트.

가장 중요한 것은 look-ahead 방지가 **실제로 작동하는지**다.
PerfectHindsightStrategy가 감사에 걸리지 않으면 이 하네스로 잰 모든 숫자는 무의미하다.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd
import pytest

from backtest.harness import (
    BIAS_WARNINGS,
    LookaheadError,
    Outcome,
    audit_lookahead,
    bucket_outcomes,
    evaluate_results,
    forward_outcome,
    replay,
    slice_as_of,
    summarize,
)
from config import DEFAULT_CONFIG
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
from strategies.dummy import AlwaysBuyStrategy, PerfectHindsightStrategy, RandomStrategy

BT = DEFAULT_CONFIG.backtest


def synthetic(n: int = 400, start: float = 100.0, step: float = 0.15) -> pd.DataFrame:
    """완만한 상승 + 큰 진동. 결정론적이며 시드가 없다.

    진동을 크게 준 이유: 단조 상승 시계열에서는 20봉 뒤 수익률이 항상 양수라
    PerfectHindsight가 고를 것이 없어져 AlwaysBuy와 결과가 같아진다.
    그러면 '미래를 알면 초과수익이 난다'는 검증 자체가 성립하지 않는다.
    """
    index = pd.bdate_range("2024-01-01", periods=n)
    close = [
        start + i * step + 14.0 * math.sin(i / 9.0) + 4.0 * math.sin(i / 2.3)
        for i in range(n)
    ]
    return pd.DataFrame(
        {
            "open": [c - 0.2 for c in close],
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1_000_000.0 + (i % 5) * 10_000 for i in range(n)],
        },
        index=index,
    )


def fast_config(warmup: int = 200):
    """평가 구간을 좁혀 테스트를 빠르게 만든다."""
    return replace(DEFAULT_CONFIG, backtest=replace(BT, warmup_bars=warmup))


class SpyStrategy(StrategyBase):
    """전략이 실제로 받은 데이터의 마지막 봉을 기록한다. 하네스 감시용."""

    name = "spy"
    version = "1.0.0"

    def __init__(self) -> None:
        self.seen: list[tuple[pd.Timestamp, int]] = []

    def build_gate(self, ctx: StockContext) -> GateResult:
        self.seen.append((ctx.ohlcv.index[-1], len(ctx.ohlcv)))
        check = GateCheck(
            id="ok",
            label="통과",
            status=CheckStatus.PASS,
            comparator=Comparator.BOOL,
            reason="spy",
        )
        return GateResult(strategy=self.name, passed=True, checks=[check], pass_count=1, total=1)

    def build_score(self, ctx):
        return 50.0, 100.0, [ScoreComponent(id="s", label="s", earned=50.0, max=100.0, detail="s")]

    def detect_setup(self, ctx):
        return SetupState.NO_SETUP

    def decide(self, ctx, score, max_score, components, setup):
        return Verdict.BUY, []


# ===========================================================================
# 슬라이싱 / 시점 격리
# ===========================================================================


def test_slice_as_of_excludes_later_bars():
    df = synthetic(20)
    cut = df.index[10].date()
    sliced = slice_as_of(df, cut)
    assert sliced.index[-1].date() == cut
    assert len(sliced) == 11


def test_strategy_never_sees_bars_beyond_t():
    """전략이 받은 데이터의 마지막 봉이 항상 평가 시점과 일치해야 한다."""
    df = synthetic(400)
    spy = SpyStrategy()
    replay(spy, "SYN", df, fast_config(warmup=250))

    assert spy.seen, "평가가 한 번도 일어나지 않았다"
    for position, (last_seen, length) in enumerate(spy.seen, start=250):
        assert last_seen == df.index[position]
        assert length == position + 1


def test_strategy_window_grows_by_one_bar_each_step():
    df = synthetic(400)
    spy = SpyStrategy()
    replay(spy, "SYN", df, fast_config(warmup=250))
    lengths = [length for _, length in spy.seen]
    assert lengths == list(range(251, 251 + len(lengths)))


def test_harness_guard_catches_a_corrupted_window(monkeypatch):
    """하네스가 슬라이싱을 잘못하면 조용히 틀리지 않고 즉시 죽어야 한다."""
    import backtest.harness as harness

    df = synthetic(400)
    original = harness.build_context

    def leaky(ticker, ohlcv, config, **kwargs):
        return original(ticker, df, config, **kwargs)  # 전체 df를 흘린다

    monkeypatch.setattr(harness, "build_context", leaky)
    with pytest.raises(LookaheadError, match="마지막 봉"):
        replay(SpyStrategy(), "SYN", df, fast_config(warmup=250))


# ===========================================================================
# 진입 시점 — 시그널 다음 봉 시가
# ===========================================================================


def test_entry_is_next_bar_open_not_signal_bar_close():
    """시그널 봉의 종가로 사는 것은 그 종가를 미리 아는 것이다."""
    df = synthetic(400)
    signals = replay(AlwaysBuyStrategy(), "SYN", df, fast_config(warmup=250))

    first = signals[0]
    signal_position = df.index.get_indexer([pd.Timestamp(first.as_of)])[0]
    assert first.entry_date == df.index[signal_position + 1].date()
    assert first.entry_price == pytest.approx(float(df["open"].iloc[signal_position + 1]))
    assert first.entry_price != pytest.approx(float(df["close"].iloc[signal_position]))


def test_rejected_signals_have_no_entry():
    """게이트 탈락은 진입하지 않는다. 진입가가 남아 있으면 집계가 오염된다."""
    df = synthetic(400)
    signals = replay(RandomStrategy(seed=1), "SYN", df, fast_config(warmup=250))
    rejected = [s for s in signals if not s.gate_passed]
    assert rejected, "탈락 시그널이 하나도 없다 — 무작위 전략이 이상하다"
    assert all(s.entry_price is None and s.entry_date is None for s in rejected)


# ===========================================================================
# forward return 계산
# ===========================================================================


def test_forward_outcome_uses_entry_open_and_exit_close():
    df = pd.DataFrame(
        {
            "open": [10.0, 20.0, 30.0, 40.0],
            "high": [11.0, 21.0, 31.0, 41.0],
            "low": [9.0, 19.0, 8.0, 39.0],
            "close": [10.5, 20.5, 30.5, 40.5],
            "volume": [1.0] * 4,
        },
        index=pd.bdate_range("2026-01-01", periods=4),
    )
    outcome = forward_outcome(df, entry_position=1, horizon=2, score=None, as_of=df.index[0].date())
    # 진입 open[1]=20, 청산 close[2]=30.5 -> (30.5-20)/20*100 = 52.5
    assert outcome.return_pct == pytest.approx(52.5)
    # 보유 구간 저가 min(low[1], low[2]) = 8 -> (8-20)/20*100 = -60
    assert outcome.max_adverse_pct == pytest.approx(-60.0)


def test_forward_outcome_returns_none_past_the_end():
    df = synthetic(10)
    assert forward_outcome(df, 8, 20, score=None, as_of=df.index[0].date()) is None


# ===========================================================================
# 집계
# ===========================================================================


def outcomes(values: list[float]) -> list[Outcome]:
    from datetime import date

    return [Outcome(date(2026, 1, 1), None, v, -abs(v)) for v in values]


def test_summarize_computes_mean_median_and_win_rate():
    stats = summarize("t", outcomes([10.0, -5.0, 20.0, -1.0]), BT)
    assert stats.n == 4
    assert stats.mean_return_pct == pytest.approx(6.0)
    assert stats.median_return_pct == pytest.approx(4.5)
    assert stats.win_rate_pct == pytest.approx(50.0)


def test_summarize_empty_group_is_none_not_zero():
    """표본이 없을 때 0.0을 내놓으면 '수익률 0%'로 오독된다."""
    stats = summarize("empty", [], BT)
    assert stats.n == 0
    assert stats.mean_return_pct is None
    assert stats.win_rate_pct is None


def test_underpowered_flag_tracks_min_sample_size():
    assert summarize("small", outcomes([1.0] * 29), BT).is_underpowered is True
    assert summarize("ok", outcomes([1.0] * 30), BT).is_underpowered is False


def test_score_buckets_partition_without_overlap():
    from datetime import date

    scored = [Outcome(date(2026, 1, 1), s, 1.0, -1.0) for s in (0.0, 39.9, 40.0, 69.9, 84.9, 99.9)]
    buckets = bucket_outcomes(scored, BT)
    assert [b.label for b in buckets] == ["0-40", "40-60", "60-70", "70-85", "85-100"]
    assert sum(b.n for b in buckets) == len(scored)


def test_score_bucket_includes_perfect_100():
    """상한 100이 어느 버킷에도 안 들어가면 만점 시그널이 조용히 사라진다."""
    from datetime import date

    buckets = bucket_outcomes([Outcome(date(2026, 1, 1), 100.0, 1.0, -1.0)], BT)
    assert buckets[-1].n == 1


def test_unscored_signals_are_not_bucketed():
    """게이트 탈락은 score=None이므로 어떤 점수 구간에도 들어가면 안 된다."""
    from datetime import date

    buckets = bucket_outcomes([Outcome(date(2026, 1, 1), None, 1.0, -1.0)], BT)
    assert sum(b.n for b in buckets) == 0


# ===========================================================================
# 더미 전략 예측 검증 — 하네스가 정상이면 결과가 예측된다
# ===========================================================================


def test_always_buy_matches_the_unconditional_benchmark_exactly():
    """매 봉 진입하므로 무조건부 분포와 완전히 같아야 한다.

    조금이라도 어긋나면 진입가나 보유기간 정렬이 틀린 것이다.
    """
    df = synthetic(400)
    for result in evaluate_results(AlwaysBuyStrategy(), "SYN", df, fast_config(warmup=200)):
        assert result.signals.n == result.benchmark.n
        assert result.excess_return_pct == pytest.approx(0.0, abs=1e-12)
        assert result.gate_rejected.n == 0


def test_always_buy_has_a_single_score_bucket():
    """점수 50 고정이므로 40-60 구간에만 들어가야 한다."""
    df = synthetic(400)
    result = evaluate_results(AlwaysBuyStrategy(), "SYN", df, fast_config(warmup=200))[0]
    populated = [b for b in result.by_score_bucket if b.n > 0]
    assert len(populated) == 1
    assert populated[0].label == "40-60"


def test_random_strategy_passes_the_lookahead_audit():
    """무작위여도 봉 날짜에 고정된 시드라 재현된다. 억울하게 적발되면 안 된다."""
    df = synthetic(400)
    audit = audit_lookahead(RandomStrategy(seed=99), "SYN", df, fast_config(warmup=200))
    assert audit.checked_points > 0
    assert audit.clean, audit.violations
    assert audit.declared_full_history is False


def test_random_strategy_splits_into_passed_and_rejected_groups():
    df = synthetic(400)
    result = evaluate_results(RandomStrategy(seed=5), "SYN", df, fast_config(warmup=200))[0]
    assert result.signals.n > 0
    assert result.gate_rejected.n > 0
    assert result.signals.n + result.gate_rejected.n == result.benchmark.n


def test_random_strategy_populates_multiple_score_buckets():
    df = synthetic(400)
    result = evaluate_results(RandomStrategy(seed=5), "SYN", df, fast_config(warmup=200))[0]
    assert sum(b.n > 0 for b in result.by_score_bucket) >= 3


# ===========================================================================
# look-ahead 적발 — 이 파일에서 가장 중요한 테스트
# ===========================================================================


def test_perfect_hindsight_is_caught_by_the_audit():
    """미래를 본 전략은 반드시 적발되어야 한다. 이게 안 되면 하네스는 쓸모없다."""
    df = synthetic(400)
    audit = audit_lookahead(PerfectHindsightStrategy(), "SYN", df, fast_config(warmup=200))
    assert audit.declared_full_history is True
    assert not audit.clean
    assert audit.violations


def test_perfect_hindsight_raises_in_strict_mode():
    df = synthetic(400)
    with pytest.raises(LookaheadError, match="look-ahead"):
        evaluate_results(PerfectHindsightStrategy(), "SYN", df, fast_config(warmup=200))


def test_perfect_hindsight_runs_when_strict_mode_is_off():
    """감사 결과를 보고 싶을 때는 예외 없이 돌려야 한다 — 다만 결과에 위반이 남는다."""
    df = synthetic(400)
    config = replace(
        DEFAULT_CONFIG, backtest=replace(BT, warmup_bars=200, strict_lookahead=False)
    )
    result = evaluate_results(PerfectHindsightStrategy(), "SYN", df, config)[0]
    assert not result.lookahead.clean


def test_perfect_hindsight_cannot_see_the_future_without_injection():
    """뒷문이 없으면 미래를 볼 수 없다. StockContext 경로로는 불가능하다."""
    from core.context import build_context
    from core.types import BarMeta, MarketRegime, SessionState

    df = synthetic(400)
    window = df.iloc[:250]
    ctx = build_context(
        "SYN",
        window,
        DEFAULT_CONFIG,
        regime=MarketRegime.CAUTION,
        bar_meta=BarMeta(
            last_bar_date=window.index[-1].date(),
            session_state=SessionState.CLOSED,
            is_bar_complete=True,
            bars_available=len(window),
            volume_judgements_reliable=True,
        ),
    )
    verdict = PerfectHindsightStrategy().evaluate(ctx)
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.gate.checks[0].status is CheckStatus.UNAVAILABLE


def test_perfect_hindsight_beats_the_benchmark_absurdly():
    """적발되는 것과 별개로, 미래를 알면 실제로 초과수익이 나야 한다.

    나지 않는다면 뒷문 자체가 동작하지 않는 것이고, 그러면 감사 테스트도 무의미하다.
    """
    df = synthetic(400)
    config = replace(
        DEFAULT_CONFIG, backtest=replace(BT, warmup_bars=200, strict_lookahead=False)
    )
    result = evaluate_results(PerfectHindsightStrategy(), "SYN", df, config)[0]
    assert result.excess_return_pct > 0.0
    assert result.signals.win_rate_pct > result.benchmark.win_rate_pct


# ===========================================================================
# 편향 경고 / 계약
# ===========================================================================


def test_bias_warnings_cover_the_three_required_topics():
    joined = " ".join(BIAS_WARNINGS)
    assert "생존편향" in joined
    assert "표본 부족" in joined
    assert "기간 편향" in joined


def test_gate_rejected_group_is_tracked_separately():
    """게이트가 실제로 나쁜 종목을 거르는지 보려면 탈락군 성과가 있어야 한다."""
    df = synthetic(400)
    result = evaluate_results(RandomStrategy(seed=3), "SYN", df, fast_config(warmup=200))[0]
    assert result.gate_rejected.label == "게이트 탈락"
    assert result.gate_rejected.n > 0
    assert result.gate_rejected.mean_return_pct is not None


def test_results_are_produced_per_horizon():
    df = synthetic(400)
    results = evaluate_results(AlwaysBuyStrategy(), "SYN", df, fast_config(warmup=200))
    assert [r.horizon for r in results] == list(BT.horizons)


def test_strategies_are_never_merged_into_one_score():
    """전략별 결과는 끝까지 분리 보존된다."""
    df = synthetic(400)
    always = evaluate_results(AlwaysBuyStrategy(), "SYN", df, fast_config(warmup=200))[0]
    rand = evaluate_results(RandomStrategy(seed=2), "SYN", df, fast_config(warmup=200))[0]
    assert always.strategy_name != rand.strategy_name
    assert not hasattr(always, "combined_score")


# ===========================================================================
# 고정 픽스처 — 실제 데이터로도 도는지
# ===========================================================================


def test_always_buy_matches_benchmark_on_real_fixture(aapl):
    result = evaluate_results(AlwaysBuyStrategy(), "AAPL", aapl.tail(300), fast_config(200))[0]
    assert result.signals.n > 0
    assert result.excess_return_pct == pytest.approx(0.0, abs=1e-12)


def test_perfect_hindsight_is_caught_on_real_fixture(aapl):
    with pytest.raises(LookaheadError):
        evaluate_results(PerfectHindsightStrategy(), "AAPL", aapl.tail(320), fast_config(200))


def test_no_nan_leaks_into_group_stats(aapl):
    result = evaluate_results(RandomStrategy(seed=11), "AAPL", aapl.tail(300), fast_config(200))[0]
    for group in (result.signals, result.gate_rejected, result.benchmark):
        for value in (group.mean_return_pct, group.median_return_pct, group.win_rate_pct):
            assert value is None or not math.isnan(value)
