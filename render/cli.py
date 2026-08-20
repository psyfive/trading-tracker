"""rich 기반 터미널 렌더러.

`render/json_out.py`와 동일한 `DiagnosisReport`를 소비한다. **판정 로직은 여기 없다.**
`if score > 70:` 같은 코드가 등장하면 전략으로 되돌린다.

여기서 하는 일은 두 가지뿐이다:
  1. 이미 내려진 판정(enum)을 색으로 옮긴다
  2. 이미 계산된 숫자(actual/threshold/comparator/unit)를 문구로 조립한다

특히 `comparator`가 존재하는 이유가 2번이다 — 렌더러가 '>= 70' 같은 문구를 만들 때
방향을 직접 해석하지 않도록 계약이 방향을 실어 보낸다.

전략별 판정은 **나란히** 표시한다. 하나로 합치거나 평균내지 않는다. 만점 척도가
전략마다 다르므로 점수는 항상 `earned / max`로만 그린다.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.types import (
    CheckStatus,
    Comparator,
    DiagnosisReport,
    GateResult,
    RiskPlan,
    Severity,
    StrategyVerdict,
    Verdict,
    WarningCode,
)

console = Console()

VERDICT_STYLE = {
    Verdict.BUY: "bold green",
    Verdict.WATCH: "yellow",
    Verdict.HOLD: "cyan",
    Verdict.AVOID: "red",
    Verdict.REJECTED_BY_GATE: "dim",
}

# UNAVAILABLE은 FAIL과 **다른 색**이어야 한다. 같은 색으로 그리면 '데이터 없음'이
# '조건 미달'로 보이고, 사용자는 신규 상장주가 왜 탈락했는지 영원히 알 수 없다.
STATUS_STYLE = {
    CheckStatus.PASS: "green",
    CheckStatus.FAIL: "red",
    CheckStatus.UNAVAILABLE: "yellow",
}

STATUS_MARK = {
    CheckStatus.PASS: "PASS",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.UNAVAILABLE: "N/A",
}

SEVERITY_STYLE = {
    Severity.INFO: "dim",
    Severity.WARN: "yellow",
    Severity.CRITICAL: "bold red",
}

COMPARATOR_SYMBOL = {
    Comparator.GTE: ">=",
    Comparator.GT: ">",
    Comparator.LTE: "<=",
    Comparator.LT: "<",
    Comparator.EQ: "==",
    Comparator.BETWEEN: "~",
    Comparator.BOOL: "",
}


def format_number(value: float | None, unit: str | None) -> str:
    """단위에 맞춘 표시 문자열. 값이 없으면 n/a — 0으로 채우지 않는다."""
    if value is None:
        return "n/a"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "$":
        return f"{value:,.0f}"
    if unit == "x":
        return f"{value:,.2f}x"
    if unit == "days":
        return f"{value:,.0f}일"
    return f"{value:,.2f}"


def _criterion(check) -> str:
    """'>= 70.00' 같은 기준 문구. BOOL 조건은 비교값이 없다."""
    if check.comparator is Comparator.BOOL or check.threshold is None:
        return "—"
    return f"{COMPARATOR_SYMBOL[check.comparator]} {format_number(check.threshold, check.unit)}"


def render_header(report: DiagnosisReport) -> None:
    """티커 / 가격 / as_of / 국면 / Stage. 미완성 봉이면 눈에 띄게 표시."""
    bar = (
        "[green]확정[/green]"
        if report.is_bar_complete
        else "[bold yellow]미완성 (장중)[/bold yellow]"
    )
    body = (
        f"[bold]{report.ticker}[/bold]  {report.price:,.2f}\n"
        f"기준일 {report.as_of}  ·  봉 {bar}  ·  세션 {report.bar_meta.session_state.value}\n"
        f"시장 국면 [bold]{report.regime.value}[/bold]  ·  "
        f"Stage [bold]{report.stage.value}[/bold]  ·  "
        f"봉 {report.bar_meta.bars_available}개"
    )
    console.print(Panel(body, border_style="cyan", padding=(0, 2)))


def render_warnings(report: DiagnosisReport) -> None:
    """severity별 색상으로 경고 출력. INCOMPLETE_BAR는 상단에 고정."""
    if not report.warnings:
        return

    ordered = sorted(
        report.warnings, key=lambda w: w.code is not WarningCode.INCOMPLETE_BAR
    )
    lines = [
        f"[{SEVERITY_STYLE[w.severity]}]● {w.code.value}[/{SEVERITY_STYLE[w.severity]}] "
        f"{w.message}"
        for w in ordered
    ]
    console.print(Panel("\n\n".join(lines), title="경고", border_style="yellow", padding=(0, 2)))


def render_gate_table(gate: GateResult) -> None:
    """조건별 PASS / FAIL / UNAVAILABLE 을 actual vs threshold와 함께.

    UNAVAILABLE은 FAIL과 다른 색으로 표시한다. 데이터 부족을 조건 미달로 보이게 하면 안 된다.
    """
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("", width=4)
    table.add_column("조건")
    table.add_column("측정값", justify="right")
    table.add_column("기준", justify="right")
    table.add_column("미달", justify="right")

    for check in gate.checks:
        style = STATUS_STYLE[check.status]
        table.add_row(
            f"[{style}]{STATUS_MARK[check.status]}[/{style}]",
            check.label,
            format_number(check.actual, check.unit),
            _criterion(check),
            "—" if check.shortfall_pct is None else f"{check.shortfall_pct:,.1f}%",
        )
    console.print(table)


def _score_bar(earned: float, maximum: float, width: int = 16) -> str:
    """비율 막대. 만점 척도가 전략마다 다르므로 절대 점수가 아니라 비율로만 그린다."""
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, earned / maximum))
    filled = round(ratio * width)
    return "█" * filled + "·" * (width - filled)


def render_strategy_verdict(verdict: StrategyVerdict, plan: RiskPlan | None = None) -> None:
    """전략 하나의 게이트 + 점수 + 셋업 + 판정."""
    style = VERDICT_STYLE[verdict.verdict]
    gate = verdict.gate
    unavailable = (
        f"  ([yellow]데이터 없음 {gate.unavailable_count}[/yellow])"
        if gate.unavailable_count
        else ""
    )
    title = (
        f"[bold]{verdict.strategy_name}[/bold] v{verdict.strategy_version}  "
        f"[{style}]{verdict.verdict.value}[/{style}]  ·  "
        f"게이트 {gate.pass_count}/{gate.total}{unavailable}  ·  "
        f"셋업 {verdict.setup_state.value}"
    )
    console.print(Text.from_markup(title))
    render_gate_table(gate)

    if verdict.score is None:
        # '채점 안 함'과 '0점'은 다르다. 막대를 그리지 않는 것으로 그 차이를 보인다.
        console.print("  [dim]게이트 탈락 — 채점하지 않았다 (0점이 아니다)[/dim]")
    else:
        console.print(
            f"  [bold]타이밍 점수 {verdict.score:,.1f} / {verdict.max_score:,.0f}[/bold]"
        )
        for component in verdict.components:
            console.print(
                f"    {_score_bar(component.earned, component.max)} "
                f"{component.label} {component.earned:,.1f}/{component.max:,.0f}  "
                f"[dim]{component.detail}[/dim]"
            )

    metrics = verdict.setup_metrics
    if metrics.pivot_price is not None:
        console.print(
            f"  [dim]피벗 {metrics.pivot_price:,.2f}"
            + (
                f" ({metrics.to_pivot_pct:+.2f}%)"
                if metrics.to_pivot_pct is not None
                else ""
            )
            + (
                f"  ·  베이스 {metrics.base_length_days}일"
                if metrics.base_length_days is not None
                else ""
            )
            + (
                f" 깊이 {metrics.base_depth_pct:.1f}%"
                if metrics.base_depth_pct is not None
                else ""
            )
            + "[/dim]"
        )

    for note in verdict.notes:
        console.print(f"  [dim]· {note}[/dim]")

    if plan is not None:
        render_risk_plan(verdict.strategy_name, plan)
    console.print()


def render_consensus(report: DiagnosisReport) -> None:
    """판정 개수 집계. 평균 점수는 존재하지 않으므로 출력하지 않는다."""
    consensus = report.consensus
    counts = "  ".join(
        f"[{VERDICT_STYLE[verdict]}]{verdict.value} {count}[/{VERDICT_STYLE[verdict]}]"
        for verdict, count in consensus.verdict_counts.items()
        if count
    )
    buys = ", ".join(consensus.buy_strategies) or "없음"
    body = (
        f"전략 {consensus.total_strategies}종  ·  {counts}\n"
        f"일치도 [bold]{consensus.agreement.value}[/bold]  ·  BUY: {buys}\n"
        "[dim]점수는 방법론마다 척도가 다르므로 평균내지 않는다 — 판정은 끝까지 분리 보존한다[/dim]"
    )
    console.print(Panel(body, title="컨센서스", border_style="cyan", padding=(0, 2)))


def render_risk_plan(strategy_name: str, plan: RiskPlan) -> None:
    """진입 / 손절 / 주수 / R 목표가 / 청산 규칙."""
    sizing = (
        f"주수 {plan.shares:,}주 · 포지션 {plan.position_value:,.0f} · "
        f"리스크 {plan.risk_amount:,.0f} ({plan.risk_pct:.2f}%)"
        if plan.shares is not None
        else "[dim]주수 n/a — 계좌 평가금액(--equity)이 없으면 사이징하지 않는다[/dim]"
    )
    targets = "  ".join(
        f"{level.multiple:g}R {level.price:,.2f}" for level in plan.r_levels
    )
    lines = [
        f"진입 [bold]{plan.entry:,.2f}[/bold]  ·  손절 [bold]{plan.stop:,.2f}[/bold] "
        f"(-{plan.stop_pct:.2f}%)  ·  1R {plan.r_per_share:,.2f}",
        sizing,
        f"목표 {targets}",
        *(f"[dim]· {rule}[/dim]" for rule in plan.exit_rules),
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title=f"{strategy_name} 리스크 플랜",
            border_style="magenta",
            padding=(0, 2),
        )
    )


def render_report(report: DiagnosisReport) -> None:
    """진단 전체를 터미널에 출력."""
    console.print()
    render_header(report)
    render_warnings(report)
    console.print()

    for verdict in report.strategy_verdicts:
        render_strategy_verdict(verdict, report.risk_plans.get(verdict.strategy_name))

    render_consensus(report)
    console.print(
        "[dim]이 출력은 진단과 근거이지 매매 권유가 아니다. "
        "판정은 방법론별로 독립이며 서로 합산되지 않는다.[/dim]\n"
    )
