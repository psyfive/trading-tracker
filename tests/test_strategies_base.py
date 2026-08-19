"""전략 공통 헬퍼 — 캐시 규약과 돌파 거래량 계산.

여기서 잠그는 것은 두 가지다:

  1. `memoize_per_context`의 키가 **ctx 객체의 신원**이라는 사실. 값(티커·날짜·길이)으로
     키를 만들면 look-ahead 감사의 두 패스가 캐시를 공유해 위반이 사라져 보인다.
  2. 돌파 거래량 비율의 손계산 값. 세 전략이 같은 함수를 쓰므로 정의가 하나여야 한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from config import DEFAULT_CONFIG
from core.context import build_context
from core.types import BarMeta, MarketRegime, SessionState, Stage
from strategies.base import breakout_volume_ratio, decay_score, memoize_per_context, scaled_score


def frame(n: int = 60) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=n)
    closes = [100.0 + i for i in range(n)]
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


def context(df: pd.DataFrame):
    return build_context(
        "TEST",
        df,
        DEFAULT_CONFIG,
        regime=MarketRegime.RISK_ON,
        stage=Stage.STAGE_2,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED,
            is_bar_complete=True,
            bars_available=len(df),
            volume_judgements_reliable=True,
        ),
    )


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    @memoize_per_context
    def detect(self, ctx):
        self.calls += 1
        return len(ctx.ohlcv)


# ---------------------------------------------------------------------------
# memoize_per_context
# ---------------------------------------------------------------------------


def test_same_context_is_computed_once():
    counter, ctx = Counter(), context(frame())
    assert counter.detect(ctx) == counter.detect(ctx)
    assert counter.calls == 1


def test_equal_but_distinct_contexts_are_recomputed():
    """감사는 같은 시점을 두 번 평가해 비교한다. 두 평가가 캐시를 공유하면 감사가 죽는다."""
    counter = Counter()
    df = frame()
    counter.detect(context(df))
    counter.detect(context(df))
    assert counter.calls == 2


def test_cache_does_not_leak_between_instances():
    first, second = Counter(), Counter()
    ctx = context(frame())
    first.detect(ctx)
    second.detect(ctx)
    assert first.calls == second.calls == 1


# ---------------------------------------------------------------------------
# 돌파 거래량 — 손계산
# ---------------------------------------------------------------------------


def test_breakout_volume_ratio_is_the_recent_maximum_over_the_average():
    volumes = pd.Series([100.0, 900.0, 300.0, 200.0])
    # 최근 3봉 최대 = 900, 평균 = 300 -> 3.0
    assert breakout_volume_ratio(volumes, 300.0, 3) == pytest.approx(3.0)


def test_breakout_volume_ratio_ignores_bars_outside_the_span():
    volumes = pd.Series([9_000.0, 100.0, 200.0, 300.0])
    # span=2 -> 최근 2봉(200, 300)의 최대 300 / 평균 300 = 1.0. 맨 앞 급증은 보지 않는다
    assert breakout_volume_ratio(volumes, 300.0, 2) == pytest.approx(1.0)


def test_breakout_volume_ratio_is_none_without_an_average():
    """평균 거래량이 없으면 0.0이 아니라 None이다 — 전략은 이를 '확인 실패'로 다룬다."""
    assert breakout_volume_ratio(pd.Series([1.0, 2.0]), None, 2) is None
    assert breakout_volume_ratio(pd.Series([1.0, 2.0]), 0.0, 2) is None
    assert breakout_volume_ratio(pd.Series(dtype=float), 100.0, 2) is None


# ---------------------------------------------------------------------------
# 정규화 어휘 — 전략마다 복사하지 않는다
# ---------------------------------------------------------------------------


def test_decay_score_is_linear_between_ideal_and_worst():
    assert decay_score(5.0, 5.0, 15.0) == pytest.approx(1.0)
    assert decay_score(10.0, 5.0, 15.0) == pytest.approx(0.5)
    assert decay_score(20.0, 5.0, 15.0) == pytest.approx(0.0)


def test_decay_score_degenerates_to_a_step_when_worst_is_not_worse():
    assert decay_score(5.0, 5.0, 5.0) == 1.0
    assert decay_score(6.0, 5.0, 5.0) == 0.0


def test_scaled_score_is_bounded():
    assert scaled_score(2.0, 1.0, lower_is_better=False) == pytest.approx(1.0)
    assert scaled_score(0.5, 1.0, lower_is_better=False) == pytest.approx(0.5)
    assert scaled_score(0.5, 0.5, lower_is_better=True) == pytest.approx(1.0)
    assert scaled_score(1.0, 0.5, lower_is_better=True) == pytest.approx(0.0)
