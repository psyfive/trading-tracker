"""StrategyVerdict 목록 -> DiagnosisReport 조립.

## 왜 별도 모듈인가

`ConsensusSummary`의 필드는 전부 `strategy_verdicts`의 **중복 저장**이고, 값이 어긋나면
DiagnosisReport validator가 죽는다. 그 파생 규칙(집계·일치·agreement)을 호출부마다
다시 쓰면 CLI·API·목업 생성기가 각자 조금씩 다르게 조립하게 되고, 그때부터
'계약 위반'이 조립 코드의 버그로 흩어진다. 파생은 여기 한 곳에만 있다.

렌더러가 아니다 — 여기서 임계값을 비교하거나 판정을 내리지 않는다. 판정은 전략의 몫이고
이 모듈은 이미 내려진 판정을 세고 옮긴다. 유일한 예외처럼 보이는 경고 두 개
(INCOMPLETE_BAR / REGIME_RISK_OFF)도 판정이 아니라 **이미 확정된 상태의 기계적 반영**이다.

## risk_plan은 아직 None이다

`risk/planner.py`가 구현되지 않았다. 계산 주체가 없는 값을 조립 코드가 지어내면
그것이 곧 '판정 로직 누수'이므로, 구현되기 전까지는 호출부가 넘긴 값을 그대로 싣고
기본값은 None이다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.context import StockContext
from core.types import (
    Agreement,
    ConsensusSummary,
    DiagnosisReport,
    DiagnosticWarning,
    GateProgress,
    RiskPlan,
    Severity,
    StrategyVerdict,
    Verdict,
    WarningCode,
)


def build_consensus(verdicts: list[StrategyVerdict]) -> ConsensusSummary:
    """판정 목록에서 컨센서스를 파생한다. **평균은 없다** — 개수만 센다.

    agreement의 분모는 전체 전략 수다. 게이트 탈락도 '비-BUY 의견'으로 센다
    (Agreement enum docstring의 정의이며 DiagnosisReport validator가 강제한다).
    """
    counts: dict[Verdict, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1

    buys = [v.strategy_name for v in verdicts if v.verdict is Verdict.BUY]
    total = len(verdicts)
    if not buys:
        agreement = Agreement.NONE
    elif len(buys) == total:
        agreement = Agreement.UNANIMOUS_BUY
    elif len(buys) * 2 > total:
        agreement = Agreement.MAJORITY_BUY
    else:
        agreement = Agreement.SPLIT

    return ConsensusSummary(
        total_strategies=total,
        verdict_counts=counts,
        buy_strategies=buys,
        gate_passed_strategies=[v.strategy_name for v in verdicts if v.gate.passed],
        gate_progress=[
            GateProgress(
                strategy=v.strategy_name,
                passed=v.gate.passed,
                pass_count=v.gate.pass_count,
                total=v.gate.total,
                unavailable_count=v.gate.unavailable_count,
                progress_ratio=round(v.gate.pass_count / v.gate.total, 4) if v.gate.total else 0.0,
            )
            for v in verdicts
        ],
        agreement=agreement,
    )


def _mandatory_warnings(ctx: StockContext) -> list[DiagnosticWarning]:
    """상태에서 기계적으로 파생되는 경고. 판정이 아니다.

    INCOMPLETE_BAR는 계약이 **강제**한다 (없으면 DiagnosisReport 생성 자체가 실패).
    REGIME_RISK_OFF는 국면 모듈이 약속한 것으로, 국면이 개별 전략 점수를 깎지 않는 대신
    리포트 상단에 사실로 표시된다.
    """
    warnings: list[DiagnosticWarning] = []
    if not ctx.bar_meta.is_bar_complete:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.INCOMPLETE_BAR,
                severity=Severity.WARN,
                message=(
                    "장중 실행 — 당일 봉이 미완성이다. 거래량 기반 조건은 판정하지 않았고 "
                    "종가 기반 조건도 확정이 아니다. 장 마감 후 재평가가 필요하다"
                ),
                field="bar_meta.is_bar_complete",
            )
        )
    if ctx.regime.value == "RISK_OFF":
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.REGIME_RISK_OFF,
                severity=Severity.WARN,
                message=(
                    "시장 국면이 RISK_OFF다 — 지수가 200일선 아래다. 개별 종목 판정과 "
                    "무관하게 역풍이며, 이 사실이 전략 점수를 깎지는 않는다"
                ),
                field="regime",
            )
        )
    return warnings


def build_report(
    ctx: StockContext,
    verdicts: list[StrategyVerdict],
    *,
    generated_at: datetime | None = None,
    risk_plan: RiskPlan | None = None,
    warnings: tuple[DiagnosticWarning, ...] = (),
) -> DiagnosisReport:
    """컨텍스트 + 판정들 -> 리포트. CLI·API·목업 생성기가 공유하는 유일한 조립 경로다."""
    codes = {w.code for w in (*ctx.warnings, *warnings)}
    derived = [w for w in _mandatory_warnings(ctx) if w.code not in codes]

    return DiagnosisReport(
        ticker=ctx.ticker,
        as_of=ctx.as_of,
        generated_at=generated_at or datetime.now(UTC),
        price=ctx.price,
        is_bar_complete=ctx.bar_meta.is_bar_complete,
        bar_meta=ctx.bar_meta,
        regime=ctx.regime,
        stage=ctx.stage,
        indicators=ctx.indicators,
        strategy_verdicts=verdicts,
        consensus=build_consensus(verdicts),
        risk_plan=risk_plan,
        warnings=[*ctx.warnings, *warnings, *derived],
    )
