"""rich 기반 터미널 렌더러.

render/json_out.py와 동일한 DiagnosisReport를 소비한다. 판정 로직은 여기 없다.
if score > 70: 같은 코드가 등장하면 전략으로 되돌린다.

전략별 판정은 나란히 표시한다. 하나로 합치거나 평균내지 않는다.
"""

from __future__ import annotations

from core.types import DiagnosisReport, GateResult, StrategyVerdict


def render_report(report: DiagnosisReport) -> None:
    """진단 전체를 터미널에 출력."""
    raise NotImplementedError


def render_header(report: DiagnosisReport) -> None:
    """티커 / 가격 / as_of / 국면 / Stage. 미완성 봉이면 눈에 띄게 표시."""
    raise NotImplementedError


def render_warnings(report: DiagnosisReport) -> None:
    """severity별 색상으로 경고 출력. INCOMPLETE_BAR는 상단에 고정."""
    raise NotImplementedError


def render_gate_table(gate: GateResult) -> None:
    """조건별 PASS / FAIL / UNAVAILABLE 을 actual vs threshold와 함께.

    UNAVAILABLE은 FAIL과 다른 색으로 표시한다. 데이터 부족을 조건 미달로 보이게 하면 안 된다.
    """
    raise NotImplementedError


def render_strategy_verdict(verdict: StrategyVerdict) -> None:
    """전략 하나의 게이트 + 점수 + 셋업 + 판정."""
    raise NotImplementedError


def render_consensus(report: DiagnosisReport) -> None:
    """판정 개수 집계. 평균 점수는 존재하지 않으므로 출력하지 않는다."""
    raise NotImplementedError


def render_risk_plan(report: DiagnosisReport) -> None:
    """진입 / 손절 / 주수 / R 목표가 / 청산 규칙."""
    raise NotImplementedError
