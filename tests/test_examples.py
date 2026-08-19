"""examples/ 목업 JSON이 계약을 통과하는지 검증한다.

이 테스트의 목적은 두 가지다:
  1. 프론트엔드에 건네는 목업이 실제 스키마와 어긋나지 않게 잠근다.
  2. 계약을 바꿨을 때 조기 경보를 울린다 — 목업이 먼저 깨진다.

값 자체는 합성 데이터이므로 시세의 정확성은 검증하지 않는다.
검증하는 것은 '이 모양의 JSON이 계약상 유효한가'와 '각 파일이 의도한 상태를 실제로 담고 있는가'다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.types import (
    SCHEMA_VERSION,
    Agreement,
    CheckStatus,
    DiagnosisReport,
    SessionState,
    SetupState,
    Verdict,
    WarningCode,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
SAMPLE_FILES = ["sample_buy.json", "sample_gate_reject.json", "sample_incomplete_bar.json"]


def load(name: str) -> DiagnosisReport:
    return DiagnosisReport.model_validate_json(
        (EXAMPLES_DIR / name).read_text(encoding="utf-8")
    )


def verdict_of(report: DiagnosisReport, strategy: str):
    return next(v for v in report.strategy_verdicts if v.strategy_name == strategy)


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_validates_against_contract(name):
    report = load(name)
    assert report.ticker


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_round_trips(name):
    """검증 -> 재직렬화 -> 재검증이 동일해야 한다."""
    report = load(name)
    assert DiagnosisReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_file_is_valid_json_with_utf8(name):
    payload = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_rejected_verdicts_never_carry_a_score(name):
    for verdict in load(name).strategy_verdicts:
        if verdict.verdict is Verdict.REJECTED_BY_GATE:
            assert verdict.score is None
            assert verdict.components == []


# ---------------------------------------------------------------------------
# sample_buy.json
# ---------------------------------------------------------------------------


def test_buy_sample_has_full_minervini_gate():
    report = load("sample_buy.json")
    gate = verdict_of(report, "minervini").gate
    assert (gate.pass_count, gate.total) == (8, 8)
    assert gate.passed is True


def test_buy_sample_is_pivot_ready_and_risk_on():
    report = load("sample_buy.json")
    assert report.regime.value == "RISK_ON"
    assert verdict_of(report, "minervini").setup_state is SetupState.PIVOT_READY


def test_buy_sample_is_unanimous():
    report = load("sample_buy.json")
    assert report.consensus.agreement is Agreement.UNANIMOUS_BUY
    assert report.consensus.verdict_counts[Verdict.BUY] == 2


def test_buy_sample_strategies_use_different_max_scores():
    """서로 다른 척도임을 목업이 직접 보여준다 — 평균내면 안 되는 이유."""
    report = load("sample_buy.json")
    assert verdict_of(report, "minervini").max_score == 100.0
    assert verdict_of(report, "weinstein").max_score == 85.0


def test_buy_sample_strategies_disagree_on_pivot():
    """같은 차트를 두 방법론이 다른 피벗으로 읽는다. 그래서 지표가 아니라 setup_metrics다."""
    report = load("sample_buy.json")
    assert (
        verdict_of(report, "minervini").setup_metrics.pivot_price
        != verdict_of(report, "weinstein").setup_metrics.pivot_price
    )


def test_buy_sample_risk_plan_r_levels_are_ascending():
    plan = load("sample_buy.json").risk_plan
    assert plan is not None
    assert [level.multiple for level in plan.r_levels] == [1.0, 2.0, 3.0]
    assert plan.stop < plan.entry < plan.r_levels[0].price


# ---------------------------------------------------------------------------
# sample_gate_reject.json
# ---------------------------------------------------------------------------


def test_reject_sample_fails_exactly_one_check():
    report = load("sample_gate_reject.json")
    gate = verdict_of(report, "minervini").gate
    assert (gate.pass_count, gate.total) == (7, 8)
    assert [c.id for c in gate.failed_checks] == ["above_52w_low"]


def test_reject_sample_failure_is_the_52w_low_condition():
    report = load("sample_gate_reject.json")
    check = verdict_of(report, "minervini").gate.failed_checks[0]
    assert check.actual == 19.14
    assert check.threshold == 30.0
    assert check.status is CheckStatus.FAIL


def test_reject_sample_minervini_score_is_none():
    verdict = verdict_of(load("sample_gate_reject.json"), "minervini")
    assert verdict.verdict is Verdict.REJECTED_BY_GATE
    assert verdict.score is None
    assert verdict.max_score is None


def test_reject_sample_verdicts_are_split():
    report = load("sample_gate_reject.json")
    assert report.consensus.agreement is Agreement.SPLIT
    assert verdict_of(report, "weinstein").verdict is Verdict.BUY
    assert verdict_of(report, "canslim").verdict is Verdict.REJECTED_BY_GATE


def test_reject_sample_gate_progress_is_sortable():
    """워치리스트를 근접도 순으로 정렬할 수 있어야 한다."""
    report = load("sample_gate_reject.json")
    ranked = sorted(
        report.consensus.gate_progress, key=lambda g: g.progress_ratio, reverse=True
    )
    assert [g.strategy for g in ranked] == ["weinstein", "minervini", "canslim"]


def test_reject_sample_same_rs_passes_one_strategy_and_fails_another():
    """RS 74가 미너비니(70)는 통과, CANSLIM(80)은 탈락. 임계값이 전략별로 산다."""
    report = load("sample_gate_reject.json")
    minervini_rs = next(
        c for c in verdict_of(report, "minervini").gate.checks if c.id == "rs_percentile"
    )
    canslim_rs = next(
        c for c in verdict_of(report, "canslim").gate.checks if c.id == "rs_percentile"
    )
    assert minervini_rs.actual == canslim_rs.actual == 74.0
    assert minervini_rs.status is CheckStatus.PASS
    assert canslim_rs.status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# sample_incomplete_bar.json
# ---------------------------------------------------------------------------


def test_incomplete_sample_flags_the_bar():
    report = load("sample_incomplete_bar.json")
    assert report.is_bar_complete is False
    assert report.bar_meta.session_state is SessionState.OPEN
    assert report.bar_meta.volume_judgements_reliable is False


def test_incomplete_sample_carries_the_required_warning():
    report = load("sample_incomplete_bar.json")
    assert any(w.code is WarningCode.INCOMPLETE_BAR for w in report.warnings)


def test_incomplete_sample_marks_volume_check_unavailable_not_failed():
    """핵심: 데이터 없음이 조건 미달로 둔갑하지 않았는지."""
    gate = verdict_of(load("sample_incomplete_bar.json"), "qullamaggie").gate
    assert [c.id for c in gate.unavailable_checks] == ["breakout_volume_ratio"]
    assert gate.failed_checks == []
    assert gate.unavailable_count == 1
    assert gate.passed is False


def test_incomplete_sample_unavailable_check_has_no_actual_value():
    check = verdict_of(load("sample_incomplete_bar.json"), "qullamaggie").gate.unavailable_checks[0]
    assert check.actual is None
    assert check.threshold == 1.5


def test_incomplete_sample_reduces_max_score_instead_of_scoring_zero():
    """채점 불가 항목은 0점 처리가 아니라 만점에서 제외한다."""
    verdict = verdict_of(load("sample_incomplete_bar.json"), "minervini")
    assert verdict.max_score == 80.0
    assert sum(c.max for c in verdict.components) == 80.0
    assert all(c.id != "volume_dryup" for c in verdict.components)


def test_incomplete_sample_withholds_buy():
    """게이트 8/8이어도 거래량 확인 없이는 BUY를 내지 않는다."""
    report = load("sample_incomplete_bar.json")
    assert verdict_of(report, "minervini").verdict is Verdict.WATCH
    assert report.consensus.buy_strategies == []
    assert report.consensus.agreement is Agreement.NONE


def test_incomplete_sample_leaves_volume_dryup_ratio_none():
    metrics = verdict_of(load("sample_incomplete_bar.json"), "minervini").setup_metrics
    assert metrics.detail is not None
    assert metrics.detail.volume_dryup_ratio is None
    assert metrics.detail.contraction_ratio == 0.42


# ---------------------------------------------------------------------------
# 게이트 근접도 마진 (스키마 1.1.0)
# ---------------------------------------------------------------------------


def test_failed_checks_carry_a_normalized_shortfall():
    """프론트가 comparator 방향을 해석하지 않고도 '얼마나 모자랐나'를 알 수 있어야 한다."""
    report = load("sample_gate_reject.json")
    failed = verdict_of(report, "minervini").gate.failed_checks[0]
    assert failed.id == "above_52w_low"
    assert failed.shortfall_pct == pytest.approx(36.2, abs=0.1)


def test_shortfall_distinguishes_near_miss_from_far_miss():
    """근접도 정렬의 핵심 — 같은 7/8 탈락이라도 미달 폭이 다르면 구분돼야 한다."""
    report = load("sample_gate_reject.json")
    minervini_miss = verdict_of(report, "minervini").gate.failed_checks[0].shortfall_pct
    canslim_miss = verdict_of(report, "canslim").gate.failed_checks[0].shortfall_pct
    assert canslim_miss < minervini_miss, "CANSLIM RS 74 vs 80이 더 근소해야 한다"


def test_passing_checks_have_no_shortfall():
    for name in SAMPLE_FILES:
        for verdict in load(name).strategy_verdicts:
            for check in verdict.gate.checks:
                if check.status is not CheckStatus.FAIL:
                    assert check.shortfall_pct is None, f"{name}/{check.id}"


def test_setup_detail_is_tagged_by_strategy():
    """프론트는 detail.kind로 분기한다. 모르는 kind는 무시하면 된다."""
    detail = verdict_of(load("sample_buy.json"), "minervini").setup_metrics.detail
    assert detail is not None
    assert detail.kind == "minervini"
    assert detail.contraction_count == 3


def test_setup_detail_is_optional():
    """detail은 없어도 된다. 프론트는 null을 반드시 처리해야 한다.

    목업의 와인스타인 판정은 detail 없이 공통 코어(피벗/베이스)만 채운 예다 —
    전략이 자기 어휘를 아직 정하지 않았거나 채울 값이 없을 때의 모습이다.
    (구현된 `strategies/weinstein.py`는 WeinsteinSetup을 채운다. 이 목업은 계약의
    허용 범위를 보여주는 손으로 채운 예시이지 구현의 스냅샷이 아니다.)
    """
    weinstein = verdict_of(load("sample_buy.json"), "weinstein").setup_metrics
    assert weinstein.detail is None
    assert weinstein.pivot_price is not None, "공통 코어(피벗)는 채울 수 있어야 한다"
