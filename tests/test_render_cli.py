"""CLI 렌더러 테스트.

렌더러에서 검증할 것은 '예쁜가'가 아니라 **판정을 왜곡하지 않는가**다:

  - UNAVAILABLE이 FAIL과 다르게 보이는가 ('데이터 없음'이 '조건 미달'로 둔갑하지 않는가)
  - 게이트 탈락이 '0점'이 아니라 '채점 안 함'으로 보이는가
  - 전략별 판정이 나란히 나오는가 (합치거나 평균내지 않는가)
  - 렌더러가 판정 쪽 코드를 끌어다 쓰지 않는가 (임계값이 렌더러로 새지 않는가)
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from core.types import (
    CheckStatus,
    Comparator,
    DiagnosisReport,
    GateCheck,
    GateResult,
    Verdict,
)
from render import cli as renderer

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
SAMPLE_FILES = ["sample_buy.json", "sample_gate_reject.json", "sample_incomplete_bar.json"]


def load(name: str) -> DiagnosisReport:
    return DiagnosisReport.model_validate(
        json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    )


@pytest.fixture
def output(monkeypatch):
    """렌더 결과를 문자열로 받는다. 색 코드는 끄고 텍스트만 본다."""
    buffer = StringIO()
    monkeypatch.setattr(
        renderer,
        "console",
        Console(file=buffer, width=120, no_color=True, force_terminal=False, legacy_windows=False),
    )
    return buffer


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_every_sample_renders_without_error(name, output):
    renderer.render_report(load(name))
    assert output.getvalue().strip()


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_every_strategy_appears_side_by_side(name, output):
    report = load(name)
    renderer.render_report(report)
    text = output.getvalue()
    for verdict in report.strategy_verdicts:
        assert verdict.strategy_name in text
        assert verdict.verdict.value in text


def _gate_with_all_statuses() -> GateResult:
    """PASS / FAIL / UNAVAILABLE 세 상태가 한 표에 같이 나오는 게이트."""
    checks = [
        GateCheck(
            id="passing",
            label="통과 조건",
            status=CheckStatus.PASS,
            actual=80.0,
            threshold=70.0,
            comparator=Comparator.GTE,
            reason="80 >= 70",
        ),
        GateCheck(
            id="failing",
            label="미달 조건",
            status=CheckStatus.FAIL,
            actual=35.0,
            threshold=70.0,
            comparator=Comparator.GTE,
            shortfall_pct=50.0,
            reason="35 < 70",
        ),
        GateCheck(
            id="unknown",
            label="확인 불가 조건",
            status=CheckStatus.UNAVAILABLE,
            threshold=70.0,
            comparator=Comparator.GTE,
            reason="데이터 없음",
        ),
    ]
    return GateResult(
        strategy="test", passed=False, checks=checks, pass_count=1, total=3, unavailable_count=1
    )


def test_unavailable_is_not_shown_as_fail(output):
    """계약이 3-state인 이유가 화면에서도 유지돼야 한다."""
    renderer.render_gate_table(_gate_with_all_statuses())
    text = output.getvalue()

    marks = renderer.STATUS_MARK
    assert marks[CheckStatus.UNAVAILABLE] in text
    assert marks[CheckStatus.FAIL] in text
    assert marks[CheckStatus.UNAVAILABLE] != marks[CheckStatus.FAIL]
    # 확인 불가 조건에는 측정값도 미달 폭도 없다 — 0으로 채우면 조건 미달로 보인다.
    assert "n/a" in text


def test_shortfall_is_shown_only_for_failing_checks(output):
    renderer.render_gate_table(_gate_with_all_statuses())
    assert "50.0%" in output.getvalue()


def test_rejected_strategy_shows_not_scored_instead_of_zero(output):
    report = load("sample_gate_reject.json")
    rejected = [v for v in report.strategy_verdicts if v.verdict is Verdict.REJECTED_BY_GATE]
    assert rejected, "이 예시는 게이트 탈락 판정을 담고 있어야 한다"

    renderer.render_report(report)
    text = output.getvalue()
    assert "채점하지 않았다" in text
    assert "0점이 아니다" in text


def test_score_is_always_shown_against_its_own_max(output):
    """만점 척도가 전략마다 다르므로 절대 점수만 크게 보이면 오독을 부른다."""
    report = load("sample_buy.json")
    renderer.render_report(report)
    text = output.getvalue()
    for verdict in report.strategy_verdicts:
        if verdict.score is None:
            continue
        assert f"{verdict.score:,.1f} / {verdict.max_score:,.0f}" in text


def test_risk_plan_is_rendered_per_strategy(output):
    report = load("sample_buy.json")
    assert report.risk_plans, "이 예시는 리스크 플랜을 담고 있어야 한다"

    renderer.render_report(report)
    text = output.getvalue()
    for name in report.risk_plans:
        assert f"{name} 리스크 플랜" in text


def test_missing_equity_says_so_instead_of_printing_zero_shares(output):
    report = load("sample_buy.json")
    plan = next(iter(report.risk_plans.values())).model_copy(
        update={"shares": None, "position_value": None, "risk_amount": None, "risk_pct": None}
    )
    renderer.render_risk_plan("test", plan)
    assert "n/a" in output.getvalue()


def test_incomplete_bar_is_visible_in_the_header(output):
    renderer.render_report(load("sample_incomplete_bar.json"))
    assert "미완성" in output.getvalue()


def test_consensus_has_no_average_score(output):
    """전략 점수를 평균낸 '종합 점수'는 이 프로젝트가 만들지 않는 값이다."""
    renderer.render_report(load("sample_buy.json"))
    text = output.getvalue()
    assert "평균내지 않는다" in text
    assert "종합 점수" not in text


def test_number_formatting_never_invents_a_value():
    """None을 0으로 그리면 '데이터 없음'이 '0'으로 둔갑한다."""
    assert renderer.format_number(None, "%") == "n/a"
    assert renderer.format_number(None, None) == "n/a"
    assert renderer.format_number(0.0, "%") == "0.00%"


def test_renderer_does_not_import_judgement_code():
    """구조적 잠금.

    렌더러가 config(임계값)나 strategies(판정)를 import하기 시작하면 그다음 수순은
    화면에서 판정을 다시 계산하는 것이다. 계약(core.types)과 rich만 알면 된다.
    """
    imports = [
        line
        for line in Path(renderer.__file__).read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]
    forbidden = [
        line
        for line in imports
        if any(module in line for module in ("config", "strategies", "indicators", "backtest"))
    ]
    assert forbidden == [], f"렌더러가 판정 쪽을 import 한다: {forbidden}"
    assert all("core.types" in line or "rich" in line or "__future__" in line for line in imports)
