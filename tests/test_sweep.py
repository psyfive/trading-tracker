"""파라미터 스윕 + 학습/홀드아웃 분할 테스트.

스윕은 '결과를 좋아 보이게 만드는' 도구가 되기 쉬우므로, 여기서 잠그는 것은 성능이
아니라 **규율**이다:

  - 학습 창과 홀드아웃 창이 실제로 겹치지 않는가 (embargo 포함)
  - 창이 df를 잘라내지 않고 '평가 시점'만 제한하는가
  - 선택이 **학습 성과만** 보는가 (홀드아웃이 좋다고 골라지지 않는가)
  - 고를 근거(표본)가 없으면 고르지 않는가
  - 만들어서는 안 되는 config 조합이 조용히 돌지 않는가
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.harness import EvalWindow, GroupStats, PanelResult, evaluate_panel, replay
from backtest.sweep import (
    SWEEP_WARNINGS,
    Parameter,
    Split,
    SweepError,
    SweepPoint,
    SweepResult,
    build_split,
    replace_field,
    sweep,
)
from config import DEFAULT_CONFIG
from strategies.dummy import AlwaysBuyStrategy, RandomStrategy

BT = DEFAULT_CONFIG.backtest


def frame(seed: int, n: int = 500) -> pd.DataFrame:
    close = 100.0 * np.exp(
        np.cumsum(np.random.default_rng(seed).normal(0.0005, 0.02, n))
    )
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.bdate_range("2023-01-02", periods=n),
    )


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return {f"T{i}": frame(i) for i in range(3)}


@pytest.fixture(scope="module")
def config():
    return replace(
        DEFAULT_CONFIG,
        backtest=replace(
            BT,
            warmup_bars=250,
            horizons=(20,),
            strict_lookahead=False,
            min_sample_size=5,
        ),
    )


# ===========================================================================
# EvalWindow — 시점만 제한한다
# ===========================================================================


def test_window_start_is_inclusive_and_end_is_exclusive():
    window = EvalWindow(start=date(2024, 1, 10), end=date(2024, 2, 1))
    assert window.contains(date(2024, 1, 10)) is True
    assert window.contains(date(2024, 1, 31)) is True
    assert window.contains(date(2024, 2, 1)) is False
    assert window.contains(date(2024, 1, 9)) is False


def test_open_window_contains_everything():
    assert EvalWindow().contains(date(2020, 1, 1)) is True


def test_replay_only_evaluates_inside_the_window(frames, config):
    df = frames["T0"]
    cutoff = df.index[400].date()
    signals = replay(
        AlwaysBuyStrategy(), "T0", df, config, window=EvalWindow(start=cutoff, label="뒤")
    )
    assert signals
    assert all(signal.as_of >= cutoff for signal in signals)


def test_window_does_not_shorten_the_history_the_strategy_sees(frames, config):
    """창을 잘라내기로 구현하면 홀드아웃 구간의 지표가 워밍업 부족으로 None이 된다.

    같은 시점의 판정이 '전체 평가'와 '창 평가'에서 같아야 그 함정을 피한 것이다.
    """
    df = frames["T0"]
    cutoff = df.index[420].date()

    full = {s.as_of: s for s in replay(RandomStrategy(seed=3), "T0", df, config)}
    windowed = replay(
        RandomStrategy(seed=3), "T0", df, config, window=EvalWindow(start=cutoff)
    )
    assert windowed
    for signal in windowed:
        assert signal == full[signal.as_of]


def test_panel_records_which_window_it_ran_on(frames, config):
    result = evaluate_panel(
        AlwaysBuyStrategy,
        frames,
        config,
        audit_tickers=0,
        window=EvalWindow(start=frames["T0"].index[400].date(), label="홀드아웃"),
    )[0]
    assert result.window_label == "홀드아웃"


# ===========================================================================
# 분할 — 두 구간이 섞이면 안 된다
# ===========================================================================


def test_split_windows_do_not_overlap(frames, config):
    split = build_split(frames, config.backtest)
    assert split.train.end is not None and split.holdout.start is not None
    assert split.train.end < split.holdout.start


def test_split_leaves_an_embargo_gap(frames, config):
    """보유기간이 겹치면 학습 막바지 시그널의 성과가 홀드아웃 봉으로 측정된다."""
    split = build_split(frames, config.backtest)
    days = sorted({t.date() for df in frames.values() for t in df.index})
    gap = days.index(split.holdout.start) - days.index(split.train.end)
    assert gap == split.embargo_bars
    assert split.embargo_bars == max(config.backtest.horizons) + config.backtest.entry_offset_bars


def test_embargo_can_be_set_explicitly(frames, config):
    split = build_split(frames, replace(config.backtest, embargo_bars=40))
    assert split.embargo_bars == 40


def test_split_refuses_when_training_period_would_vanish(frames, config):
    with pytest.raises(SweepError, match="학습 구간"):
        build_split(frames, replace(config.backtest, holdout_fraction=0.99))


def test_split_rejects_a_nonsense_fraction(frames, config):
    with pytest.raises(SweepError):
        build_split(frames, replace(config.backtest, holdout_fraction=1.5))


def test_split_signals_are_disjoint_in_time(frames, config):
    """실제로 두 창을 돌렸을 때 같은 날짜가 양쪽에 들어가지 않아야 한다."""
    split = build_split(frames, config.backtest)
    df = frames["T0"]
    train = {s.as_of for s in replay(AlwaysBuyStrategy(), "T0", df, config, window=split.train)}
    holdout = {
        s.as_of for s in replay(AlwaysBuyStrategy(), "T0", df, config, window=split.holdout)
    }
    assert train and holdout
    assert train.isdisjoint(holdout)


# ===========================================================================
# config 변형
# ===========================================================================


def test_replace_field_touches_only_the_named_field():
    changed = replace_field(DEFAULT_CONFIG, "minervini", "min_rs_percentile", 55.0)
    assert changed.minervini.min_rs_percentile == 55.0
    assert changed.minervini.buy_min_score_pct == DEFAULT_CONFIG.minervini.buy_min_score_pct
    assert changed.weinstein == DEFAULT_CONFIG.weinstein
    # 원본은 오염되지 않는다 (frozen + replace).
    assert DEFAULT_CONFIG.minervini.min_rs_percentile != 55.0


@pytest.mark.parametrize(
    ("section", "field"),
    [("nope", "min_rs_percentile"), ("minervini", "nope")],
)
def test_replace_field_rejects_unknown_paths(section, field):
    with pytest.raises(SweepError):
        replace_field(DEFAULT_CONFIG, section, field, 1.0)


def test_config_invariants_still_kill_bad_combinations():
    """AppConfig.__post_init__이 막는 조합을 스윕이 우회하면 안 된다.

    와인스타인의 Stage 파라미터는 RegimeConfig와 같아야 한다 — 한쪽만 바꾸면
    화면의 Stage와 전략이 세는 Stage 2가 조용히 갈라진다.
    """
    with pytest.raises(ValueError):
        replace_field(DEFAULT_CONFIG, "weinstein", "ma_period_daily", 100)


# ===========================================================================
# 선택 규율 — 학습만 본다
# ===========================================================================


def stats(n: int, mean: float, minimum: int = 5) -> GroupStats:
    return GroupStats(
        label="테스트",
        n=n,
        mean_return_pct=mean,
        median_return_pct=mean,
        stdev_return_pct=1.0,
        win_rate_pct=50.0,
        mean_max_adverse_pct=-1.0,
        worst_max_adverse_pct=-2.0,
        min_sample_size=minimum,
    )


def panel(n: int, excess: float, *, minimum: int = 5, label: str = "학습") -> PanelResult:
    """초과수익이 excess가 되도록 벤치마크를 0으로 둔 패널 결과."""
    empty = stats(0, 0.0, minimum)
    return PanelResult(
        strategy_name="test",
        horizon=20,
        tickers=3,
        tickers_with_entries=3,
        signals=stats(n, excess, minimum),
        gate_passed_not_entered=empty,
        gate_rejected=empty,
        benchmark=stats(n, 0.0, minimum),
        by_score_bucket=(),
        audits=(),
        entries_by_ticker=(),
        window_label=label,
    )


def point(label: str, value, train_excess: float, holdout_excess: float, n: int = 50):
    return SweepPoint(
        label=label,
        value=value,
        config=DEFAULT_CONFIG,
        train=panel(n, train_excess),
        holdout=panel(n, holdout_excess, label="홀드아웃"),
    )


def result(points, baseline=1.0) -> SweepResult:
    return SweepResult(
        strategy_name="test",
        parameter=Parameter("minervini", "min_rs_percentile", [p.value for p in points]),
        horizon=20,
        split=Split(EvalWindow(label="학습"), EvalWindow(label="홀드아웃"), 21, 500, 350, 150),
        points=tuple(points),
        baseline_value=baseline,
    )


def test_selection_uses_training_performance_only():
    """홀드아웃이 더 좋은 값이 있어도 선택은 학습에서만 이뤄져야 한다."""
    swept = result(
        [
            point("a", 1.0, train_excess=3.0, holdout_excess=-5.0),
            point("b", 2.0, train_excess=1.0, holdout_excess=+9.0),
        ]
    )
    assert swept.best_train.label == "a"


def test_underpowered_training_sample_yields_no_choice():
    """표본이 기준에 못 미치면 '고를 근거가 없다'가 정답이다."""
    swept = result([point("a", 1.0, 3.0, 1.0, n=2), point("b", 2.0, 5.0, 1.0, n=3)])
    assert swept.best_train is None
    assert "근거가 없다" in swept.verdict


def test_decay_measures_the_gap_between_train_and_holdout():
    swept = result([point("a", 1.0, train_excess=4.0, holdout_excess=-1.0)])
    assert swept.points[0].decay_pct == pytest.approx(-5.0)


def test_verdict_calls_out_a_collapse_in_holdout():
    swept = result([point("a", 9.0, train_excess=4.0, holdout_excess=-1.0)], baseline=1.0)
    assert "재현되지 않았다" in swept.verdict


def test_verdict_says_so_when_the_default_already_wins():
    swept = result(
        [
            point("baseline", 1.0, train_excess=4.0, holdout_excess=2.0),
            point("other", 2.0, train_excess=1.0, holdout_excess=3.0),
        ],
        baseline=1.0,
    )
    assert "기본값이 최고" in swept.verdict


def test_baseline_point_is_findable():
    swept = result([point("a", 1.0, 1.0, 1.0), point("b", 2.0, 2.0, 2.0)], baseline=2.0)
    assert swept.baseline.value == 2.0


# ===========================================================================
# 러너 — 실제로 도는가
# ===========================================================================


def test_sweep_runs_every_value_on_both_windows(frames, config):
    swept = sweep(
        lambda cfg: RandomStrategy(seed=5),
        Parameter("minervini", "buy_min_score_pct", [50.0, 70.0]),
        frames,
        config,
    )
    assert [p.label for p in swept.points] == [
        "buy_min_score_pct=50.0",
        "buy_min_score_pct=70.0",
    ]
    for swept_point in swept.points:
        assert swept_point.train.window_label == "학습"
        assert swept_point.holdout.window_label == "홀드아웃"
        assert swept_point.train.signals.n > 0


def test_sweep_records_the_baseline_value(frames, config):
    swept = sweep(
        lambda cfg: RandomStrategy(seed=5),
        Parameter("minervini", "buy_min_score_pct", [50.0, 60.0]),
        frames,
        config,
    )
    assert swept.baseline_value == DEFAULT_CONFIG.minervini.buy_min_score_pct


def test_sweep_needs_values(frames, config):
    with pytest.raises(SweepError, match="스윕할 값이 없다"):
        sweep(
            lambda cfg: RandomStrategy(seed=5),
            Parameter("minervini", "buy_min_score_pct", []),
            frames,
            config,
        )


def test_sweep_rejects_a_horizon_the_backtest_does_not_measure(frames, config):
    with pytest.raises(SweepError, match="horizons"):
        sweep(
            lambda cfg: RandomStrategy(seed=5),
            Parameter("minervini", "buy_min_score_pct", [60.0]),
            frames,
            config,
            horizon=999,
        )


def test_sweep_is_deterministic(frames, config):
    parameter = Parameter("minervini", "buy_min_score_pct", [60.0])
    first = sweep(lambda cfg: RandomStrategy(seed=5), parameter, frames, config)
    second = sweep(lambda cfg: RandomStrategy(seed=5), parameter, frames, config)
    assert first.points[0].train.signals == second.points[0].train.signals


def test_sweep_warnings_state_the_multiple_comparison_problem():
    """숫자와 함께 다녀야 하는 경고. 없으면 스윕 결과가 '최적값'으로 읽힌다."""
    text = " ".join(SWEEP_WARNINGS)
    assert "다중비교" in text
    assert "홀드아웃" in text


# ===========================================================================
# 노이즈 폭 — argmax가 뽑은 승자가 진짜 승자인가
# ===========================================================================


def test_margin_is_the_gap_between_first_and_second():
    swept = result([point("a", 1.0, 3.0, 0.0), point("b", 2.0, 1.0, 0.0)])
    assert swept.train_margin_pct == pytest.approx(2.0)


def test_a_hairline_win_is_reported_as_indistinguishable():
    """차이가 표준오차 안이면 '최고'는 파라미터가 아니라 표본이 뽑은 것이다."""
    swept = result([point("a", 1.0, 0.53, 0.0), point("b", 2.0, 0.50, 0.0)])
    assert swept.best_is_within_noise is True
    assert "구분되지 않는다" in swept.verdict


def test_a_wide_win_is_not_dismissed_as_noise():
    swept = result([point("a", 1.0, 30.0, 5.0), point("b", 2.0, 1.0, 0.0)])
    assert swept.best_is_within_noise is False
    assert "구분되지 않는다" not in swept.verdict


def test_margin_is_none_with_a_single_candidate():
    swept = result([point("a", 1.0, 3.0, 1.0), point("b", 2.0, 5.0, 1.0, n=2)])
    assert swept.train_margin_pct is None
    assert swept.best_is_within_noise is False
