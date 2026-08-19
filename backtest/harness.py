"""백테스트 검증 러너 — '전략이 좋은지'를 재는 자.

자가 없으면 전략이 좋은지 영원히 모른다. 그래서 전략보다 자를 먼저 만든다.

## look-ahead 방지 (이 모듈의 존재 이유)

두 겹으로 막는다.

1. **구조적 차단**: 시점 t에서 전략에 넘기는 StockContext는 `df.iloc[:t+1]`로 만든다.
   미래 봉은 객체 안에 물리적으로 존재하지 않는다. 전체 df에 닿는 경로를 만들지 않는다.
   매 호출 직전 `_assert_point_in_time()`이 이를 재확인한다 — 하네스 자신의 버그를 잡는다.

2. **행위 감사**: 미래를 보려면 하네스가 명시적으로 주입해야만 한다
   (`requires_full_history` + `inject_full_history`). 이 뒷문을 쓰는 전략은
   `LookaheadAudit`이 적발한다. 원리는 시점별 재현(point-in-time replay)이다 —
   전체 데이터로 평가한 시점 t의 판정과, t+1에서 잘라낸 데이터로 평가한 같은 시점의
   판정을 비교한다. 정직한 전략은 두 값이 같아야 한다. 다르면 미래를 쓴 것이다.

## 진입 시점

시그널은 봉 t의 **종가 확정 후** 나온다. 따라서 진입은 t+1 봉의 **시가**다.
시그널이 난 봉의 종가로 사는 것은 그 종가를 미리 아는 것이므로 look-ahead다.

## 진입 기준은 verdict다 — 게이트가 아니다

진입은 **verdict == BUY**일 때만 발생한다. 게이트를 통과하고도 WATCH/HOLD/AVOID를
낸 시점은 '전략이 사지 않기로 한 날'이므로 매수로 집계하면 측정 대상이
'전략 성과'가 아니라 '게이트 성과'로 바뀐다. 세 집단을 분리 집계한다:
진입(BUY) / 통과-미진입(WATCH 등) / 게이트 탈락.

## 시점별 컨텍스트 주입 (stage / regime / RS)

stage·regime·rs_percentile은 단일 종목 OHLCV만으로는 계산할 수 없어 호출부가
주입한다. `*_by_date` 매핑을 넘기면 각 평가 시점의 날짜로 조회해 컨텍스트에 넣는다.
**주입되는 시리즈는 호출부가 시점별(point-in-time)로 계산한 것이어야 한다** —
날짜 t의 값이 t 이하 데이터로만 산출됐어야 한다는 뜻이다. look-ahead 감사는 같은
시리즈를 양쪽 평가에 똑같이 쓰므로, 시리즈 자체에 스며든 미래 참조는 적발하지
못한다. 시리즈 산출 코드(regime/market.py 등)가 스스로 지켜야 하는 책임이다.

## as_of 개념 (Phase 4 재무 데이터 대비)

지금은 가격만 다루므로 '봉 날짜 <= t'로 충분하다. 그러나 분기 실적 같은 재무 데이터는
**발표일(as_of)** 이후에만 알 수 있다. 2026Q1 실적이 2026-05-10에 발표됐다면
2026-04-01 시점 판정에 그 숫자를 쓰면 안 된다 — 분기 종료일이 아니라 발표일이 기준이다.
Phase 4에서 재무 데이터를 붙일 때 각 레코드에 발표일을 달고, 여기서
`row.announced_at <= as_of` 필터를 추가해야 한다. 지금 구현할 필요는 없다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from config import AppConfig, BacktestConfig
from core.context import StockContext, build_context
from core.types import BarMeta, MarketRegime, SessionState, Stage, Verdict
from strategies.base import Strategy

# 시점별 컨텍스트 주입용 매핑. 호출부가 point-in-time으로 계산해 넘긴다 (모듈 docstring 참조).
RegimeByDate = Mapping[date, MarketRegime]
StageByDate = Mapping[date, Stage]
RsByDate = Mapping[date, float]


class LookaheadError(RuntimeError):
    """전략이 미래 데이터를 참조했거나, 하네스가 미래 데이터를 넘겼다."""


class InsufficientBacktestDataError(RuntimeError):
    """워밍업 + 최대 보유기간을 빼고 나면 평가할 봉이 남지 않는다.

    조용히 빈 결과를 돌려주면 '시그널 0건'이 정상 결과처럼 보인다.
    설정을 잘못 잡은 것이므로 시끄럽게 죽는다.
    """


def _evaluation_range(df: pd.DataFrame, backtest: BacktestConfig) -> range:
    """평가 가능한 봉 위치 범위. 남는 봉이 없으면 예외."""
    start = backtest.warmup_bars
    end = len(df) - max(backtest.horizons) - backtest.entry_offset_bars
    if end <= start:
        needed = start + max(backtest.horizons) + backtest.entry_offset_bars + 1
        raise InsufficientBacktestDataError(
            f"평가할 봉이 없다: {len(df)}봉 보유, 최소 {needed}봉 필요 "
            f"(워밍업 {start} + 최대 보유 {max(backtest.horizons)} + "
            f"진입 지연 {backtest.entry_offset_bars})"
        )
    return range(start, end)


# ---------------------------------------------------------------------------
# 결과 자료구조 (프론트엔드 계약이 아니다 — core/types.py에 넣지 않는다)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """한 시점의 평가 결과. 게이트 탈락도 기록한다 (탈락군 집계를 위해).

    entry_date/entry_price는 **verdict == BUY일 때만** 채워진다. 게이트를 통과해도
    전략이 사지 않기로 했다면(WATCH 등) 진입이 아니다.
    """

    as_of: date
    gate_passed: bool
    verdict: Verdict
    score: float | None
    entry_date: date | None
    entry_price: float | None

    @property
    def entered(self) -> bool:
        """전략이 실제로 진입한 시점인지."""
        return self.verdict is Verdict.BUY


@dataclass(frozen=True)
class Outcome:
    """시그널 하나의 forward 성과."""

    as_of: date
    score: float | None
    return_pct: float
    max_adverse_pct: float


@dataclass(frozen=True)
class GroupStats:
    """한 집단의 성과 요약. n을 항상 함께 들고 다닌다."""

    label: str
    n: int
    mean_return_pct: float | None
    median_return_pct: float | None
    stdev_return_pct: float | None
    win_rate_pct: float | None
    mean_max_adverse_pct: float | None
    worst_max_adverse_pct: float | None
    min_sample_size: int

    @property
    def is_underpowered(self) -> bool:
        """표본이 부족해 숫자를 믿을 수 없는 구간."""
        return self.n < self.min_sample_size

    @property
    def stderr_return_pct(self) -> float | None:
        """평균의 표준오차. '초과수익이 노이즈인가'를 판정하는 기준이 된다."""
        if self.stdev_return_pct is None or self.n < 2:
            return None
        return self.stdev_return_pct / math.sqrt(self.n)


@dataclass(frozen=True)
class LookaheadAudit:
    """시점별 재현 감사 결과."""

    checked_points: int
    violations: tuple[str, ...]
    declared_full_history: bool

    @property
    def clean(self) -> bool:
        """위반이 없고 **실제로 검사가 돌았을 때만** 깨끗하다.

        checked_points=0을 clean으로 처리하면 감사가 건너뛰어졌을 때
        전략이 결백한 것처럼 보인다.
        """
        return self.checked_points > 0 and not self.violations


@dataclass(frozen=True)
class BacktestResult:
    """전략 하나 × 티커 하나 × 기간 하나의 성과. 전략끼리 합산하지 않는다.

    signals는 **진입(BUY) 집단**이다. 게이트를 통과했지만 진입하지 않은 시점
    (WATCH/HOLD/AVOID)은 gate_passed_not_entered로 분리 집계한다 — 이 집단을
    signals에 섞으면 '전략 성과'가 아니라 '게이트 성과'를 재는 것이 된다.
    """

    strategy_name: str
    ticker: str
    horizon: int
    bars_evaluated: int
    signals: GroupStats
    gate_passed_not_entered: GroupStats
    gate_rejected: GroupStats
    benchmark: GroupStats
    by_score_bucket: tuple[GroupStats, ...]
    lookahead: LookaheadAudit

    @property
    def excess_return_pct(self) -> float | None:
        """벤치마크(무조건부 매수) 대비 평균 초과수익."""
        if self.signals.mean_return_pct is None or self.benchmark.mean_return_pct is None:
            return None
        return self.signals.mean_return_pct - self.benchmark.mean_return_pct


BIAS_WARNINGS = (
    "생존편향: 현재 상장 중인 종목만으로 백테스트했다. 상장폐지·인수합병된 종목이 "
    "빠져 있으므로 결과는 낙관 쪽으로 편향된다. 이 시스템은 그 편향을 제거하지 못한다.",
    "표본 부족: 픽스처 2종목으로는 통계적 결론을 낼 수 없다. 이 수치는 하네스가 "
    "제대로 도는지 확인하는 용도이지 전략 우열의 근거가 아니다.",
    "기간 편향: 3년 데이터는 특정 시장 국면에 치우쳐 있을 수 있다. 다른 국면에서 "
    "같은 결과가 나온다는 보장은 없다.",
)


# ---------------------------------------------------------------------------
# look-ahead 가드
# ---------------------------------------------------------------------------


def _assert_point_in_time(ctx: StockContext, expected_last: pd.Timestamp, position: int) -> None:
    """전략에 넘기기 직전 컨텍스트를 재확인한다.

    하네스 자신이 슬라이싱을 잘못하면 여기서 터진다. 조용히 틀린 백테스트가
    나오는 것보다 시끄럽게 죽는 편이 낫다.
    """
    last = ctx.ohlcv.index[-1]
    if last != expected_last:
        raise LookaheadError(
            f"컨텍스트 마지막 봉이 {last.date()}로 기대값 {expected_last.date()}과 다르다"
        )
    if len(ctx.ohlcv) != position + 1:
        raise LookaheadError(
            f"컨텍스트에 {len(ctx.ohlcv)}봉이 들어 있다 — {position + 1}봉이어야 한다"
        )


def _make_bar_meta(window: pd.DataFrame) -> BarMeta:
    """과거 시점 재현이므로 그 봉은 이미 확정된 것으로 취급한다."""
    return BarMeta(
        last_bar_date=window.index[-1].date(),
        session_state=SessionState.CLOSED,
        is_bar_complete=True,
        bars_available=len(window),
        volume_judgements_reliable=True,
    )


def _evaluate_at(
    strategy: Strategy,
    ticker: str,
    df: pd.DataFrame,
    position: int,
    config: AppConfig,
    *,
    regime: MarketRegime,
    regime_by_date: RegimeByDate | None = None,
    stage_by_date: StageByDate | None = None,
    rs_percentile_by_date: RsByDate | None = None,
    visible_history: pd.DataFrame | None = None,
):
    """시점 position에서 전략을 평가한다.

    visible_history: 뒷문(requires_full_history)으로 주입할 데이터.
    감사에서 '얼마나 보여줄지'를 바꿔가며 부르기 위해 인자로 뺐다.

    *_by_date: 시점별 컨텍스트 주입 (모듈 docstring 참조). 해당 날짜가 매핑에 없으면
    regime은 상수 인자로, stage는 UNDEFINED로, rs_percentile은 None으로 떨어진다 —
    '모름'을 그럴듯한 값으로 채우지 않는다.
    """
    window = df.iloc[: position + 1]
    as_of = df.index[position].date()

    if getattr(strategy, "requires_full_history", False):
        strategy.inject_full_history(df if visible_history is None else visible_history)

    ctx = build_context(
        ticker,
        window,
        config,
        regime=regime_by_date.get(as_of, regime) if regime_by_date is not None else regime,
        bar_meta=_make_bar_meta(window),
        stage=(
            stage_by_date.get(as_of, Stage.UNDEFINED)
            if stage_by_date is not None
            else Stage.UNDEFINED
        ),
        rs_percentile=(
            rs_percentile_by_date.get(as_of) if rs_percentile_by_date is not None else None
        ),
    )
    _assert_point_in_time(ctx, df.index[position], position)
    return strategy.evaluate(ctx)


def audit_lookahead(
    strategy: Strategy,
    ticker: str,
    df: pd.DataFrame,
    config: AppConfig,
    *,
    regime: MarketRegime = MarketRegime.CAUTION,
    regime_by_date: RegimeByDate | None = None,
    stage_by_date: StageByDate | None = None,
    rs_percentile_by_date: RsByDate | None = None,
) -> LookaheadAudit:
    """시점별 재현 감사.

    같은 시점 t를 두 번 평가한다:
      A. 전체 데이터가 존재하는 상태
      B. t 이후가 물리적으로 존재하지 않는 상태 (df를 t+1에서 잘라 전달)

    정직한 전략은 t 이하만 쓰므로 두 판정이 같아야 한다.
    다르면 t 이후 데이터가 판정에 영향을 준 것이고, 그것이 곧 look-ahead다.

    비교는 **StrategyVerdict 전체**다 (verdict/score만이 아니다). setup_state나
    setup_metrics로만 새는 미래 참조도 리포트와 프론트에 실리므로 똑같이 위반이다.

    한계 (docstring에 명시해 두는 이유는 이 감사를 증명으로 오독하지 않기 위함이다):
      - 표본 시점(lookahead_audit_samples)에서만 검사한다. 드물게만 미래를 쓰는
        전략은 표본을 비켜갈 수 있다.
      - 호출 간 상태를 쌓는 전략은 검사 자체를 무력화한다 (Strategy Protocol의
        결정론 요구사항 참조).
      - 주입된 *_by_date 시리즈 자체의 look-ahead는 잡지 못한다 (모듈 docstring).
    """
    backtest = config.backtest
    declared = bool(getattr(strategy, "requires_full_history", False))

    span = _evaluation_range(df, backtest)
    step = max(1, len(span) // backtest.lookahead_audit_samples)
    positions = list(range(span.start, span.stop, step))[: backtest.lookahead_audit_samples]

    injected = {
        "regime_by_date": regime_by_date,
        "stage_by_date": stage_by_date,
        "rs_percentile_by_date": rs_percentile_by_date,
    }
    violations: list[str] = []
    for position in positions:
        truncated = df.iloc[: position + 1]
        with_future = _evaluate_at(
            strategy, ticker, df, position, config, regime=regime,
            visible_history=df, **injected,
        )
        without_future = _evaluate_at(
            strategy, ticker, truncated, position, config, regime=regime,
            visible_history=truncated, **injected,
        )

        if with_future != without_future:
            violations.append(
                f"{df.index[position].date()}: 미래 유무에 따라 판정이 다르다 — "
                f"있을 때 {with_future.verdict.value}(score={with_future.score}), "
                f"없을 때 {without_future.verdict.value}(score={without_future.score})"
            )

    return LookaheadAudit(len(positions), tuple(violations), declared)


# ---------------------------------------------------------------------------
# forward return
# ---------------------------------------------------------------------------


def forward_outcome(
    df: pd.DataFrame, entry_position: int, horizon: int, *, score: float | None, as_of: date
) -> Outcome | None:
    """진입 시점부터 horizon봉 뒤까지의 성과.

    진입가는 entry_position 봉의 **시가**, 청산가는 horizon봉 뒤의 종가다.
    max_adverse_pct는 보유 구간 저가 기준 최대 역행폭이다 (최대낙폭 대용).
    """
    exit_position = entry_position + horizon - 1
    if exit_position >= len(df):
        return None

    entry = float(df["open"].iloc[entry_position])
    if entry <= 0.0:
        return None

    exit_price = float(df["close"].iloc[exit_position])
    trough = float(df["low"].iloc[entry_position : exit_position + 1].min())

    return Outcome(
        as_of=as_of,
        score=score,
        return_pct=(exit_price - entry) / entry * 100.0,
        max_adverse_pct=(trough - entry) / entry * 100.0,
    )


def summarize(label: str, outcomes: list[Outcome], config: BacktestConfig) -> GroupStats:
    """집단 요약. 표본이 없으면 숫자를 지어내지 않고 None을 둔다."""
    if not outcomes:
        return GroupStats(label, 0, None, None, None, None, None, None, config.min_sample_size)

    returns = [o.return_pct for o in outcomes]
    adverse = [o.max_adverse_pct for o in outcomes]
    return GroupStats(
        label=label,
        n=len(outcomes),
        mean_return_pct=statistics.fmean(returns),
        median_return_pct=statistics.median(returns),
        stdev_return_pct=statistics.stdev(returns) if len(returns) > 1 else None,
        win_rate_pct=100.0 * sum(r > 0.0 for r in returns) / len(returns),
        mean_max_adverse_pct=statistics.fmean(adverse),
        worst_max_adverse_pct=min(adverse),
        min_sample_size=config.min_sample_size,
    )


def _bucket_label(low: float, high: float) -> str:
    return f"{low:.0f}-{high:.0f}"


def bucket_outcomes(
    outcomes: list[Outcome], config: BacktestConfig
) -> tuple[GroupStats, ...]:
    """점수 구간별 집계. 점수가 없는 시그널은 어느 버킷에도 넣지 않는다."""
    groups: list[GroupStats] = []
    for low, high in config.score_buckets:
        members = [
            o
            for o in outcomes
            if o.score is not None
            and (low <= o.score < high or (high >= 100.0 and o.score == high))
        ]
        groups.append(summarize(_bucket_label(low, high), members, config))
    return tuple(groups)


# ---------------------------------------------------------------------------
# 러너
# ---------------------------------------------------------------------------


def replay(
    strategy: Strategy,
    ticker: str,
    df: pd.DataFrame,
    config: AppConfig,
    *,
    regime: MarketRegime = MarketRegime.CAUTION,
    regime_by_date: RegimeByDate | None = None,
    stage_by_date: StageByDate | None = None,
    rs_percentile_by_date: RsByDate | None = None,
) -> list[Signal]:
    """워밍업 이후 모든 봉에서 전략을 평가한다. 게이트 탈락도 기록한다.

    각 시점에서 전략이 보는 것은 df.iloc[:t+1]뿐이다.
    진입(entry_date/entry_price)은 verdict가 BUY일 때만 기록한다 —
    게이트 통과는 진입이 아니다.
    """
    backtest = config.backtest
    offset = backtest.entry_offset_bars
    signals: list[Signal] = []

    for position in _evaluation_range(df, backtest):
        verdict = _evaluate_at(
            strategy, ticker, df, position, config, regime=regime,
            regime_by_date=regime_by_date, stage_by_date=stage_by_date,
            rs_percentile_by_date=rs_percentile_by_date,
        )
        entry_position = position + offset
        entered = verdict.verdict is Verdict.BUY

        signals.append(
            Signal(
                as_of=df.index[position].date(),
                gate_passed=verdict.gate.passed,
                verdict=verdict.verdict,
                score=verdict.score,
                entry_date=df.index[entry_position].date() if entered else None,
                entry_price=float(df["open"].iloc[entry_position]) if entered else None,
            )
        )
    return signals


def evaluate_results(
    strategy: Strategy,
    ticker: str,
    df: pd.DataFrame,
    config: AppConfig,
    *,
    regime: MarketRegime = MarketRegime.CAUTION,
    regime_by_date: RegimeByDate | None = None,
    stage_by_date: StageByDate | None = None,
    rs_percentile_by_date: RsByDate | None = None,
    run_audit: bool = True,
) -> list[BacktestResult]:
    """기간별 BacktestResult 목록. 전략끼리 합산하지 않는다.

    집계는 verdict 기준 3집단이다: 진입(BUY) / 통과-미진입 / 게이트 탈락.
    벤치마크는 **같은 종목의 무조건부 매수**다 (모든 봉에서 진입한 경우의 분포).
    이것이 'AlwaysBuy는 buy-and-hold와 같아야 한다'는 예측의 기준선이 된다.
    지수(SPY/KOSPI) 대비 비교는 유니버스 데이터가 필요하므로 Phase 3.5다.
    """
    backtest = config.backtest
    offset = backtest.entry_offset_bars
    injected = {
        "regime_by_date": regime_by_date,
        "stage_by_date": stage_by_date,
        "rs_percentile_by_date": rs_percentile_by_date,
    }
    signals = replay(strategy, ticker, df, config, regime=regime, **injected)

    audit = (
        audit_lookahead(strategy, ticker, df, config, regime=regime, **injected)
        if run_audit
        else LookaheadAudit(0, (), bool(getattr(strategy, "requires_full_history", False)))
    )
    if audit.violations and backtest.strict_lookahead:
        raise LookaheadError(
            f"{strategy.name}: look-ahead 감사에서 {len(audit.violations)}건 적발\n  - "
            + "\n  - ".join(audit.violations[:5])
        )

    positions = {df.index[i].date(): i for i in range(len(df))}
    results: list[BacktestResult] = []

    for horizon in backtest.horizons:
        entered: list[Outcome] = []
        not_entered: list[Outcome] = []
        rejected: list[Outcome] = []
        benchmark: list[Outcome] = []

        for signal in signals:
            entry_position = positions[signal.as_of] + offset
            outcome = forward_outcome(
                df, entry_position, horizon, score=signal.score, as_of=signal.as_of
            )
            if outcome is None:
                continue
            benchmark.append(
                Outcome(signal.as_of, None, outcome.return_pct, outcome.max_adverse_pct)
            )
            if not signal.gate_passed:
                rejected.append(outcome)
            elif signal.entered:
                entered.append(outcome)
            else:
                not_entered.append(outcome)

        results.append(
            BacktestResult(
                strategy_name=strategy.name,
                ticker=ticker,
                horizon=horizon,
                bars_evaluated=len(signals),
                signals=summarize("진입(BUY)", entered, backtest),
                gate_passed_not_entered=summarize("통과-미진입", not_entered, backtest),
                gate_rejected=summarize("게이트 탈락", rejected, backtest),
                benchmark=summarize("무조건부 매수(벤치마크)", benchmark, backtest),
                by_score_bucket=bucket_outcomes(entered, backtest),
                lookahead=audit,
            )
        )
    return results


def sweep(*args, **kwargs):
    """dataclasses.replace()로 설정 변형본을 만들어 파라미터 스윕. Phase 3 이후."""
    raise NotImplementedError("파라미터 스윕은 진짜 전략이 생긴 뒤에 의미가 있다 (Phase 3+)")
