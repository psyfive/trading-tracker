"""파라미터 스윕 — **학습 구간에서 고르고 홀드아웃으로 확인한다.**

## 이 모듈이 조심스러운 이유

스윕은 '결과를 좋아 보이게 만드는' 가장 쉬운 도구다. 3년 한 표본에서 임계값 후보 k개를
돌려 최고를 고르면, 그 최고값은 **진짜 우위와 표본 노이즈의 합**이고 노이즈 부분은
다음 기간에 재현되지 않는다. 후보가 많을수록 부풀림도 커진다 (다중비교).

그래서 이 모듈은 두 가지를 구조로 강제한다:

1. **선택은 학습 구간에서만 한다.** `SweepResult.best_train`은 학습 성과만 본다.
   홀드아웃 수치는 고른 뒤에 붙는 참고값이며, 그것을 보고 다시 고르면 그 순간
   홀드아웃이 아니게 된다 (사람이 지켜야 하는 규율이라 코드가 막을 수는 없다 —
   대신 결과 객체에 경고 문구가 항상 함께 다닌다).
2. **학습과 홀드아웃 사이를 비운다(embargo).** 보유기간이 겹치면 학습 구간 막바지
   시그널의 성과가 홀드아웃 봉으로 측정되어 두 구간이 섞인다. 기본 embargo는
   `max(horizons) + entry_offset_bars`다.

## 무엇을 알 수 있고 무엇을 알 수 없는가

- 알 수 있는 것: 학습에서 고른 값이 홀드아웃에서 **무너지는가**. 무너지면 그 파라미터의
  '개선'은 과적합이었다는 뜻이고, 이것은 유용한 부정 정보다.
- 알 수 없는 것: 홀드아웃에서도 좋았다고 해서 그 값이 옳다는 것. 홀드아웃은 한 구간이고
  표본은 여전히 작다. 스윕은 **후보를 기각하는 도구**이지 최적값을 찾는 도구가 아니다.

전략은 여기서 만들지 않는다 — 호출부가 `make_strategy(config)` 팩토리를 넘긴다.
백테스트 레이어가 전략 목록을 알면 전략 추가가 코어 수정을 요구하게 된다 (원칙 2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import pandas as pd

from backtest.harness import (
    EvalWindow,
    PanelResult,
    evaluate_panel,
)
from config import AppConfig, BacktestConfig
from strategies.base import Strategy

SWEEP_WARNINGS = (
    "다중비교: 후보 k개를 돌려 최고를 고르면 그 값에는 표본 노이즈가 섞여 들어간다. "
    "후보가 많을수록 학습 구간 성과는 부풀고, 부풀린 만큼은 다음 기간에 재현되지 않는다.",
    "홀드아웃은 한 구간이다: 학습에서 고른 값이 홀드아웃에서 살아남아도 '옳다'는 증거가 "
    "아니다. 무너지면 과적합이라는 증거는 되지만, 살아남는 것은 그저 기각되지 않았다는 뜻이다.",
    "표본 중첩: 보유기간이 겹치는 시그널들의 수익률은 서로 독립이 아니다. embargo는 학습과 "
    "홀드아웃 **사이**만 비울 뿐, 구간 내부의 자기상관은 그대로다.",
)


class SweepError(RuntimeError):
    """스윕을 돌릴 수 없는 상태. 조용히 빈 결과를 돌려주지 않는다."""


# ---------------------------------------------------------------------------
# 학습 / 홀드아웃 분할
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """기간 분할 결과. 숫자와 함께 '어느 구간이었나'가 항상 따라다녀야 한다."""

    train: EvalWindow
    holdout: EvalWindow
    embargo_bars: int
    total_days: int
    train_days: int
    holdout_days: int

    def describe(self) -> str:
        return (
            f"학습 ~{self.train.end} ({self.train_days}거래일) / "
            f"홀드아웃 {self.holdout.start}~ ({self.holdout_days}거래일) / "
            f"embargo {self.embargo_bars}봉"
        )


def build_split(frames: Mapping[str, pd.DataFrame], backtest: BacktestConfig) -> Split:
    """패널의 거래일을 모아 앞을 학습, 뒤를 홀드아웃으로 나눈다.

    티커별 비율이 아니라 **달력 기준**으로 나누는 이유: 같은 기간을 모든 종목이 공유해야
    '학습 구간의 시장 국면'과 '홀드아웃의 시장 국면'을 구분해 말할 수 있다. 종목마다
    다른 날짜로 나누면 두 구간이 같은 국면을 섞어 담는다.
    """
    days: list[date] = sorted(
        {timestamp.date() for df in frames.values() for timestamp in df.index}
    )
    if not days:
        raise SweepError("패널에 봉이 없다")

    fraction = backtest.holdout_fraction
    if not 0.0 < fraction < 1.0:
        raise SweepError(f"holdout_fraction은 0과 1 사이여야 한다: {fraction}")

    embargo = backtest.embargo_bars
    if embargo is None:
        embargo = max(backtest.horizons) + backtest.entry_offset_bars

    split_index = int(len(days) * (1.0 - fraction))
    train_end_index = split_index - embargo
    if train_end_index <= 0:
        raise SweepError(
            f"학습 구간이 남지 않는다: 거래일 {len(days)}일, 홀드아웃 {fraction:.0%}, "
            f"embargo {embargo}봉"
        )

    train_end, holdout_start = days[train_end_index], days[split_index]
    return Split(
        train=EvalWindow(end=train_end, label="학습"),
        holdout=EvalWindow(start=holdout_start, label="홀드아웃"),
        embargo_bars=embargo,
        total_days=len(days),
        train_days=train_end_index,
        holdout_days=len(days) - split_index,
    )


# ---------------------------------------------------------------------------
# 파라미터 변형
# ---------------------------------------------------------------------------


def replace_field(config: AppConfig, section: str, field: str, value: Any) -> AppConfig:
    """`AppConfig.<section>.<field>`만 바꾼 변형본. frozen이므로 replace로 만든다.

    `AppConfig.__post_init__`이 전략 간 일치를 강제하므로, 어긋나는 조합은 여기서
    ValueError로 죽는다. 스윕이 만들어서는 안 되는 조합을 조용히 돌리지 않기 위함이다.
    """
    section_obj = getattr(config, section, None)
    if section_obj is None:
        raise SweepError(f"AppConfig에 '{section}' 섹션이 없다")
    if not hasattr(section_obj, field):
        raise SweepError(f"'{section}'에 '{field}' 필드가 없다")
    return replace(config, **{section: replace(section_obj, **{field: value})})


@dataclass(frozen=True)
class Parameter:
    """스윕할 파라미터 하나. 값 목록과 함께 '어디의 무엇인가'를 들고 다닌다."""

    section: str
    field: str
    values: Sequence[Any]

    @property
    def path(self) -> str:
        return f"{self.section}.{self.field}"

    def label_for(self, value: Any) -> str:
        return f"{self.field}={value}"


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepPoint:
    """파라미터 값 하나의 학습·홀드아웃 성과."""

    label: str
    value: Any
    config: AppConfig
    train: PanelResult
    holdout: PanelResult

    @property
    def is_selectable(self) -> bool:
        """학습 표본이 기준을 넘어야 '고를 근거가 있다'고 본다."""
        return (
            not self.train.signals.is_underpowered
            and self.train.excess_return_pct is not None
        )

    @property
    def decay_pct(self) -> float | None:
        """홀드아웃 초과수익 - 학습 초과수익. 음수가 클수록 과적합 신호다."""
        if self.train.excess_return_pct is None or self.holdout.excess_return_pct is None:
            return None
        return self.holdout.excess_return_pct - self.train.excess_return_pct


@dataclass(frozen=True)
class SweepResult:
    """파라미터 하나에 대한 스윕 전체."""

    strategy_name: str
    parameter: Parameter
    horizon: int
    split: Split
    points: tuple[SweepPoint, ...]
    baseline_value: Any

    @property
    def best_train(self) -> SweepPoint | None:
        """**학습 구간 성과만으로** 고른 점. 고를 근거가 없으면 None.

        홀드아웃을 보지 않는다는 것이 이 속성의 존재 이유다.
        """
        candidates = [point for point in self.points if point.is_selectable]
        if not candidates:
            return None
        return max(candidates, key=lambda point: point.train.excess_return_pct)

    @property
    def baseline(self) -> SweepPoint | None:
        return next((p for p in self.points if p.value == self.baseline_value), None)

    @property
    def train_margin_pct(self) -> float | None:
        """학습 최고와 2위의 초과수익 차이. 후보가 하나뿐이면 None."""
        excesses = sorted(
            (p.train.excess_return_pct for p in self.points if p.is_selectable), reverse=True
        )
        return None if len(excesses) < 2 else excesses[0] - excesses[1]

    @property
    def best_is_within_noise(self) -> bool:
        """최고와 2위의 차이가 최고 점의 2 표준오차보다 작은가.

        argmax는 차이가 0.03%p여도 승자를 뽑는다. 그 차이가 노이즈 폭 안이면 '최고'는
        표본이 뽑은 것이지 파라미터가 만든 것이 아니다 — 그 사실이 보여야 한다.
        """
        best, margin = self.best_train, self.train_margin_pct
        if best is None or margin is None:
            return False
        stderr = best.train.signals.stderr_return_pct
        return stderr is not None and margin < 2.0 * stderr

    @property
    def verdict(self) -> str:
        """스윕이 실제로 말해 주는 것 한 줄. 판정이 아니라 요약이다."""
        best = self.best_train
        if best is None:
            return "학습 표본이 기준에 못 미쳐 고를 근거가 없다"
        if self.best_is_within_noise:
            return (
                f"학습 최고는 {best.label}이나 2위와의 차이"
                f"({self.train_margin_pct:+.2f}%p)가 표준오차 안이라 구분되지 않는다"
            )
        if best.value == self.baseline_value:
            return "학습 구간에서도 기본값이 최고였다 — 바꿀 이유가 없다"
        if best.decay_pct is None:
            return f"학습 최고는 {best.label}이나 홀드아웃 표본이 없어 확인할 수 없다"
        if best.holdout.excess_return_pct is not None and best.holdout.excess_return_pct <= 0:
            return (
                f"학습 최고 {best.label}의 홀드아웃 초과수익이 "
                f"{best.holdout.excess_return_pct:+.2f}%p — 개선이 재현되지 않았다"
            )
        return (
            f"학습 최고 {best.label}이 홀드아웃에서도 "
            f"{best.holdout.excess_return_pct:+.2f}%p로 살아남았다 (기각되지 않았을 뿐이다)"
        )


# ---------------------------------------------------------------------------
# 러너
# ---------------------------------------------------------------------------


def sweep(
    make_strategy: Callable[[AppConfig], Strategy],
    parameter: Parameter,
    frames: Mapping[str, pd.DataFrame],
    base: AppConfig,
    *,
    injections: Mapping[str, Mapping[str, object]] | None = None,
    warmups: Mapping[str, int] | None = None,
    split: Split | None = None,
    horizon: int | None = None,
) -> SweepResult:
    """파라미터 값마다 학습·홀드아웃 패널을 돌린다.

    look-ahead 감사는 돌리지 않는다(`audit_tickers=0`). 감사는 시점 정합성을 보는
    장치이고 그것은 임계값이 아니라 전략 구현의 성질이라 `tests/test_strategies_lookahead.py`가
    이미 CI에서 잠근다. 스윕에서까지 돌리면 변형 수만큼 비용이 곱해진다.
    """
    if not parameter.values:
        raise SweepError(f"{parameter.path}: 스윕할 값이 없다")

    split = split or build_split(frames, base.backtest)
    horizon = horizon if horizon is not None else base.backtest.horizons[0]
    if horizon not in base.backtest.horizons:
        raise SweepError(f"horizon {horizon}은 backtest.horizons에 없다")

    shared = {"injections": injections, "warmups": warmups, "audit_tickers": 0}
    points: list[SweepPoint] = []

    for value in parameter.values:
        config = replace_field(base, parameter.section, parameter.field, value)

        def run(window: EvalWindow, config: AppConfig = config) -> PanelResult:
            results = evaluate_panel(
                lambda: make_strategy(config), frames, config, window=window, **shared
            )
            return next(result for result in results if result.horizon == horizon)

        points.append(
            SweepPoint(
                label=parameter.label_for(value),
                value=value,
                config=config,
                train=run(split.train),
                holdout=run(split.holdout),
            )
        )

    return SweepResult(
        strategy_name=make_strategy(base).name,
        parameter=parameter,
        horizon=horizon,
        split=split,
        points=tuple(points),
        baseline_value=getattr(getattr(base, parameter.section), parameter.field),
    )
