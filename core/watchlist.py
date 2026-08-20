"""DiagnosisReport 목록 -> WatchlistReport 조립.

## 왜 진단을 요약해서 만드는가

스캔은 종목마다 **완전한 진단을 돌린 뒤** 그 결과를 요약한다. 요약용으로 따로 계산하지
않는 이유는 하나다: 그렇게 하면 '스캔에서 BUY였는데 눌러 보니 WATCH'가 언젠가 반드시
생긴다. 판정 경로가 하나여야 목록과 상세가 같은 말을 한다.

비용은 문제가 되지 않는다. 무거운 것은 수집이고, 그것은 종목별 캐시로 이미 해결돼 있다.

## 정렬은 계약이다

`(BUY 전략 수, 게이트 진행률)` 내림차순이다. 이 순서를 조립에서 정하고 계약이
강제한다(`WatchlistReport` validator) — 렌더러가 다시 정렬하면 '무엇이 위에 오는가'가
화면마다 달라지고, 그 순간 정렬 기준이 판정의 일부라는 사실이 흐려진다.

게이트 진행률이 두 번째 키인 이유: 8개 중 7개를 통과한 종목이야말로 내일 조건을
채울 수 있는 후보다. `GateCheck.shortfall_pct`와 `GateProgress`가 Phase 0부터 계약에
있던 것은 이 정렬을 위해서였다.

이 모듈은 판정하지 않는다. 이미 내려진 판정을 세고, 줄이고, 줄 세운다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.types import (
    Agreement,
    DiagnosisReport,
    DiagnosticWarning,
    MarketRegime,
    ScanFailure,
    StrategySummary,
    StrategyVerdict,
    Verdict,
    WatchlistEntry,
    WatchlistReport,
)


def summarize_verdict(verdict: StrategyVerdict) -> StrategySummary:
    """전략 판정 하나를 표 한 줄 분량으로 줄인다.

    score_pct는 `StrategyVerdict.score_pct`(만점 대비 비율)를 그대로 쓴다. 절대 점수를
    실으면 만점이 다른 전략·시점끼리 나란히 놓였을 때 오독을 부른다.
    """
    gate = verdict.gate
    return StrategySummary(
        strategy_name=verdict.strategy_name,
        verdict=verdict.verdict,
        score_pct=None if verdict.score_pct is None else round(verdict.score_pct, 4),
        gate_pass_count=gate.pass_count,
        gate_total=gate.total,
        gate_unavailable_count=gate.unavailable_count,
        progress_ratio=round(gate.pass_count / gate.total, 6) if gate.total else 0.0,
        setup_state=verdict.setup_state,
        to_pivot_pct=verdict.setup_metrics.to_pivot_pct,
    )


def build_entry(report: DiagnosisReport) -> WatchlistEntry:
    """진단 리포트 하나 -> 워치리스트 한 행.

    파생 필드(buy_strategies / agreement / best_gate_progress)는 여기서 계산하지 않고
    이미 계산된 컨센서스에서 옮긴다. 같은 값을 두 번 계산하면 두 곳이 갈라진다.
    """
    summaries = [summarize_verdict(v) for v in report.strategy_verdicts]
    consensus = report.consensus
    return WatchlistEntry(
        ticker=report.ticker,
        price=report.price,
        as_of=report.as_of,
        is_bar_complete=report.is_bar_complete,
        stage=report.stage,
        rs_percentile=report.indicators.rs_percentile,
        strategies=summaries,
        buy_strategies=list(consensus.buy_strategies),
        agreement=consensus.agreement,
        best_gate_progress=round(max((s.progress_ratio for s in summaries), default=0.0), 6),
    )


def sort_entries(entries: list[WatchlistEntry]) -> list[WatchlistEntry]:
    """(BUY 수, 게이트 진행률) 내림차순. 동점이면 티커 알파벳순으로 안정화한다.

    동점 처리를 정해 두는 이유: 같은 데이터로 두 번 돌렸을 때 순서가 흔들리면
    '어제와 무엇이 달라졌나'를 눈으로 비교할 수 없다.
    """
    return sorted(
        entries,
        key=lambda e: (-len(e.buy_strategies), -e.best_gate_progress, e.ticker),
    )


def build_watchlist(
    universe: str,
    reports: list[DiagnosisReport],
    *,
    regime: MarketRegime,
    failed: list[ScanFailure] | None = None,
    warnings: tuple[DiagnosticWarning, ...] = (),
    generated_at: datetime | None = None,
) -> WatchlistReport:
    """스캔 결과 조립. CLI·API가 공유하는 유일한 경로다."""
    failed = failed or []
    entries = sort_entries([build_entry(report) for report in reports])
    return WatchlistReport(
        universe=universe,
        generated_at=generated_at or datetime.now(UTC),
        regime=regime,
        entries=entries,
        failed=failed,
        requested=len(entries) + len(failed),
        warnings=list(warnings),
    )


def summarize_counts(report: WatchlistReport) -> dict[Verdict, int]:
    """판정별 종목 수. '한 전략이라도 그 판정을 낸 종목'을 센다.

    전략 판정을 합산하지 않는다는 원칙은 그대로다 — 여기서 세는 것은 종목이고,
    한 종목이 BUY와 AVOID에 동시에 잡힐 수 있다 (서로 다른 방법론이므로 정상이다).
    """
    counts: dict[Verdict, int] = {}
    for entry in report.entries:
        for verdict in {s.verdict for s in entry.strategies}:
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def agreement_counts(report: WatchlistReport) -> dict[Agreement, int]:
    """일치도별 종목 수."""
    counts: dict[Agreement, int] = {}
    for entry in report.entries:
        counts[entry.agreement] = counts.get(entry.agreement, 0) + 1
    return counts
