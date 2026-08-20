"""examples/ 예시 JSON — 계약 준수 + **구현과의 일치**를 검증한다.

이 테스트의 목적은 세 가지다:

  1. 프론트엔드에 건네는 예시가 스키마와 어긋나지 않게 잠근다.
  2. 계약을 바꿨을 때 조기 경보를 울린다 — 예시가 먼저 깨진다.
  3. 예시가 **현재 구현의 실제 출력**인지 확인한다.

3번이 Phase 4 리뷰에서 추가된 이유: 1~2번은 **형태**만 본다. 손으로 채운 목업은
스키마를 통과하면서도 존재하지 않는 게이트 체크 id, 구현이 금지한 게이트 구성,
발생 불가능한 시나리오를 담을 수 있었고 실제로 그렇게 됐다. 이제 예시는
`scripts/make_examples.py`가 실제 전략으로 생성하며, 여기서 같은 (티커, as_of)를
다시 평가해 파일과 대조한다. 어긋나면 재생성하라고 말해 준다.

값 자체(가격·점수)는 픽스처에서 나온 실측이므로 '시세가 맞는가'는 검증 대상이 아니다.
검증하는 것은 '이 파일이 계약상 유효하고, 의도한 상태를 담고 있으며, 구현의 출력과
같은가'다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.types import (
    SCHEMA_VERSION,
    CheckStatus,
    DiagnosisReport,
    SessionState,
    Verdict,
    WarningCode,
    WatchlistReport,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "examples"
SAMPLE_FILES = ["sample_buy.json", "sample_gate_reject.json", "sample_incomplete_bar.json"]

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


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


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_every_sample_covers_all_three_strategies(name):
    """예시는 프론트가 보고 개발하는 문서다. 구현된 전략이 전부 등장해야 한다."""
    names = [v.strategy_name for v in load(name).strategy_verdicts]
    assert names == ["minervini", "weinstein", "qullamaggie"]


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_every_sample_carries_the_survivorship_warning(name):
    """RS 백분위를 실은 리포트는 생존편향 경고를 함께 실어야 한다."""
    codes = {w.code for w in load(name).warnings}
    assert WarningCode.RS_UNIVERSE_MISSING in codes


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_risk_plans_only_ride_on_actionable_verdicts(name):
    """플랜은 진입 의사가 있는 판정(BUY/WATCH)에만 붙는다.

    AVOID나 게이트 탈락에 진입가·주수가 실리면 '사지 않기로 한 방법론'이 매수 계획을
    내놓는 화면이 된다.
    """
    report = load(name)
    actionable = {
        v.strategy_name
        for v in report.strategy_verdicts
        if v.verdict in (Verdict.BUY, Verdict.WATCH)
    }
    assert set(report.risk_plans) <= actionable


def test_buy_sample_carries_a_sized_risk_plan():
    """참조 문서로 쓰이므로 주수까지 채워진 형태가 하나는 있어야 한다."""
    report = load("sample_buy.json")
    plan = report.risk_plans[report.consensus.buy_strategies[0]]
    assert plan.stop < plan.entry
    assert plan.shares is not None and plan.shares > 0
    assert plan.account_equity is not None
    assert [level.multiple for level in plan.r_levels] == sorted(
        level.multiple for level in plan.r_levels
    )


def test_risk_plans_differ_when_pivots_differ():
    """전략마다 피벗이 다르면 진입가·손절가도 달라야 한다.

    하나로 합치면 '어느 방법론을 따를 것인가'라는 선택을 조립 코드가 대신 하게 된다.
    """
    report = load("sample_buy.json")
    pivots = {
        v.strategy_name: v.setup_metrics.pivot_price
        for v in report.strategy_verdicts
        if v.strategy_name in report.risk_plans
    }
    for name_a, pivot_a in pivots.items():
        for name_b, pivot_b in pivots.items():
            if pivot_a != pivot_b:
                assert report.risk_plans[name_a].entry != report.risk_plans[name_b].entry


# ---------------------------------------------------------------------------
# 예시가 현재 구현의 출력인가 (드리프트 차단)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def market():
    import make_examples

    return make_examples.load_market()


@pytest.mark.parametrize(
    ("name", "complete"),
    [
        ("sample_buy.json", True),
        ("sample_gate_reject.json", True),
        ("sample_incomplete_bar.json", False),
    ],
)
def test_sample_matches_the_current_implementation_output(name, complete, market):
    """같은 (티커, as_of)를 지금 구현으로 다시 평가하면 파일과 한 글자도 다르지 않아야 한다.

    시나리오 탐색(패널 전수 스캔)은 여기서 하지 않는다 — 파일에 적힌 시점을 재현하는
    것만으로 '내용이 구현과 어긋났는가'는 충분히 잡힌다.
    """
    import make_examples

    report = load(name)
    payload = make_examples.payload_for_date(
        market, report.ticker, report.as_of, complete=complete
    )
    assert payload == (EXAMPLES_DIR / name).read_text(encoding="utf-8"), (
        f"{name}이 구현 출력과 다르다 — python scripts/make_examples.py 로 재생성할 것"
    )


def test_gate_check_ids_in_samples_exist_in_the_implementation(market):
    """예시의 게이트 체크 id가 실제 구현이 내는 id와 같아야 한다 (죽은 id 회귀 방지)."""
    import make_examples

    report = load("sample_buy.json")
    position = int(
        market["frames"][report.ticker].index.get_indexer([str(report.as_of)])[0]
    )
    df = market["frames"][report.ticker]
    from config import DEFAULT_CONFIG
    from data.universe import rs_percentile_series
    from regime.market import stage_series

    ctx = make_examples.context_at(
        report.ticker,
        df,
        position,
        regimes=market["regimes"],
        stages=stage_series(df, DEFAULT_CONFIG.regime),
        rs=rs_percentile_series(report.ticker, market["percentiles"]),
    )
    live = {v.strategy_name: [c.id for c in v.gate.checks] for v in make_examples.evaluate(ctx)}
    sampled = {v.strategy_name: [c.id for c in v.gate.checks] for v in report.strategy_verdicts}
    assert sampled == live


# ---------------------------------------------------------------------------
# sample_buy.json — 세 전략이 모두 살 만하다고 본 시점
# ---------------------------------------------------------------------------


def test_buy_sample_actually_contains_a_buy():
    report = load("sample_buy.json")
    assert report.consensus.buy_strategies
    assert report.consensus.agreement.value != "NONE"


def test_buy_sample_gate_passers_have_scores():
    for verdict in load("sample_buy.json").strategy_verdicts:
        if verdict.gate.passed:
            assert verdict.score is not None
            assert verdict.max_score is not None


def test_buy_sample_strategies_use_different_max_scores():
    """서로 다른 척도임을 예시가 직접 보여준다 — 평균내면 안 되는 이유."""
    max_scores = {v.strategy_name: v.max_score for v in load("sample_buy.json").strategy_verdicts}
    assert len(set(max_scores.values())) > 1


def test_buy_sample_strategies_read_the_same_chart_differently():
    """같은 차트를 세 방법론이 서로 다른 베이스 구조로 읽는다.

    피벗 값은 우연히 같을 수 있다(같은 스윙 고점을 골랐을 때). 정의가 다르다는 사실은
    베이스 길이·깊이에서 드러난다 — 그래서 이 값들이 지표가 아니라 setup_metrics다.
    """
    lengths = {
        v.strategy_name: v.setup_metrics.base_length_days
        for v in load("sample_buy.json").strategy_verdicts
    }
    assert len(set(lengths.values())) == 3


def test_buy_sample_shows_every_setup_detail_variant():
    """스키마 1.2.0의 판별 유니온을 프론트가 어떻게 분기하는지 보여주는 참조 샘플."""
    kinds = {
        v.setup_metrics.detail.kind
        for v in load("sample_buy.json").strategy_verdicts
        if v.setup_metrics.detail is not None
    }
    assert kinds == {"minervini", "weinstein", "qullamaggie"}


def test_buy_sample_reports_the_breakout_volume_it_would_require():
    """돌파 거래량은 BUY의 필요조건이므로 그 수치가 근거로 실려야 한다."""
    for verdict in load("sample_buy.json").strategy_verdicts:
        detail = verdict.setup_metrics.detail
        assert detail is not None
        assert detail.breakout_volume_ratio is not None


# ---------------------------------------------------------------------------
# sample_gate_reject.json — 같은 종목, 전략마다 다른 자
# ---------------------------------------------------------------------------


def test_reject_sample_has_a_gate_rejection_without_a_score():
    rejected = [
        v
        for v in load("sample_gate_reject.json").strategy_verdicts
        if v.verdict is Verdict.REJECTED_BY_GATE
    ]
    assert rejected
    assert all(v.score is None and v.max_score is None for v in rejected)


def test_reject_sample_contains_a_near_miss():
    """한 조건만 모자란 종목이야말로 워치리스트에 올려야 하는 대상이다."""
    near = [
        v
        for v in load("sample_gate_reject.json").strategy_verdicts
        if not v.gate.passed and v.gate.pass_count == v.gate.total - 1
    ]
    assert near


def test_reject_sample_splits_the_strategies():
    """같은 차트를 한 전략은 통과시키고 다른 전략은 탈락시킨다 — 나란히 두는 이유."""
    report = load("sample_gate_reject.json")
    passed = {v.gate.passed for v in report.strategy_verdicts}
    assert passed == {True, False}


def test_reject_sample_gate_progress_is_sortable():
    """워치리스트를 근접도 순으로 정렬할 수 있어야 한다."""
    report = load("sample_gate_reject.json")
    ranked = sorted(
        report.consensus.gate_progress, key=lambda g: g.progress_ratio, reverse=True
    )
    assert ranked[0].progress_ratio >= ranked[-1].progress_ratio
    assert {g.strategy for g in ranked} == {
        v.strategy_name for v in report.strategy_verdicts
    }


def test_reject_sample_same_indicator_passes_one_strategy_and_fails_another():
    """RS 임계값이 전략별로 산다 (미너비니 70 / 와인스타인 50 / Qullamaggie 80)."""
    report = load("sample_gate_reject.json")
    statuses = {}
    actuals = set()
    for verdict in report.strategy_verdicts:
        check = next((c for c in verdict.gate.checks if c.id == "rs_percentile"), None)
        if check is not None and check.actual is not None:
            statuses[verdict.strategy_name] = check.status
            actuals.add(check.actual)
    assert len(actuals) == 1, "같은 지표값이어야 임계값 차이를 보여준다"
    assert set(statuses.values()) == {CheckStatus.PASS, CheckStatus.FAIL}


# ---------------------------------------------------------------------------
# sample_incomplete_bar.json — BUY 시점과 같은 봉, 미완성이라는 사실 하나만 다르다
# ---------------------------------------------------------------------------


def test_incomplete_sample_flags_the_bar():
    report = load("sample_incomplete_bar.json")
    assert report.is_bar_complete is False
    assert report.bar_meta.session_state is SessionState.OPEN
    assert report.bar_meta.volume_judgements_reliable is False


def test_incomplete_sample_carries_the_required_warning():
    report = load("sample_incomplete_bar.json")
    assert any(w.code is WarningCode.INCOMPLETE_BAR for w in report.warnings)


def test_incomplete_sample_is_the_same_bar_as_the_buy_sample():
    """두 파일이 같은 봉이어야 '봉 완성 여부만으로 판정이 바뀐다'를 보여줄 수 있다."""
    buy, incomplete = load("sample_buy.json"), load("sample_incomplete_bar.json")
    assert (buy.ticker, buy.as_of, buy.price) == (
        incomplete.ticker,
        incomplete.as_of,
        incomplete.price,
    )


def test_incomplete_sample_reduces_max_score_instead_of_scoring_zero():
    """채점 불가 항목은 0점 처리가 아니라 만점에서 제외한다."""
    buy, incomplete = load("sample_buy.json"), load("sample_incomplete_bar.json")
    for verdict in incomplete.strategy_verdicts:
        if verdict.max_score is None:
            continue
        complete_verdict = verdict_of(buy, verdict.strategy_name)
        assert verdict.max_score < complete_verdict.max_score
        assert sum(c.max for c in verdict.components) == verdict.max_score


def test_incomplete_sample_drops_only_the_volume_components():
    """빠진 항목이 거래량 항목이어야 한다 — 다른 항목이 사라지면 채점이 망가진 것이다."""
    buy, incomplete = load("sample_buy.json"), load("sample_incomplete_bar.json")
    for verdict in incomplete.strategy_verdicts:
        if verdict.max_score is None:
            continue
        dropped = {c.id for c in verdict_of(buy, verdict.strategy_name).components} - {
            c.id for c in verdict.components
        }
        assert dropped
        assert all("volume" in component_id for component_id in dropped)


def test_incomplete_sample_withholds_buy():
    """거래량 확인 없이 BUY를 내지 않는다 — 게이트를 전부 통과했더라도."""
    report = load("sample_incomplete_bar.json")
    assert report.consensus.buy_strategies == []
    assert all(v.verdict is not Verdict.BUY for v in report.strategy_verdicts)


def test_incomplete_sample_explains_itself_in_the_notes():
    """왜 BUY가 아닌지가 사용자에게 문장으로 남아야 한다."""
    for verdict in load("sample_incomplete_bar.json").strategy_verdicts:
        if verdict.gate.passed:
            assert any("거래량" in note for note in verdict.notes)


# ---------------------------------------------------------------------------
# 게이트 근접도 마진 (스키마 1.1.0)
# ---------------------------------------------------------------------------


def test_failed_checks_carry_a_normalized_shortfall():
    """프론트가 comparator 방향을 해석하지 않고도 '얼마나 모자랐나'를 알 수 있어야 한다."""
    failures = [
        check
        for name in SAMPLE_FILES
        for verdict in load(name).strategy_verdicts
        for check in verdict.gate.failed_checks
    ]
    assert failures, "탈락 조건이 하나도 없으면 이 성질을 보여주지 못한다"
    assert any(check.shortfall_pct is not None for check in failures)
    for check in failures:
        if check.shortfall_pct is not None:
            assert check.shortfall_pct > 0.0


def test_passing_checks_have_no_shortfall():
    for name in SAMPLE_FILES:
        for verdict in load(name).strategy_verdicts:
            for check in verdict.gate.checks:
                if check.status is not CheckStatus.FAIL:
                    assert check.shortfall_pct is None, f"{name}/{check.id}"


def test_unavailable_checks_never_fabricate_a_derived_threshold():
    """확인 못 한 조건에 기준값 0.0을 실으면 프론트가 '기준 > 0.00'을 그린다."""
    for name in SAMPLE_FILES:
        for verdict in load(name).strategy_verdicts:
            for check in verdict.gate.unavailable_checks:
                assert check.actual is None
                assert check.threshold != 0.0


# ---------------------------------------------------------------------------
# 워치리스트 예시 — 두 번째 루트 계약
# ---------------------------------------------------------------------------

WATCHLIST_FILE = "sample_watchlist.json"


def load_watchlist() -> WatchlistReport:
    return WatchlistReport.model_validate_json(
        (EXAMPLES_DIR / WATCHLIST_FILE).read_text(encoding="utf-8")
    )


def test_watchlist_sample_is_contract_valid():
    report = load_watchlist()
    assert report.schema_version == SCHEMA_VERSION
    assert report.requested == len(report.entries) + len(report.failed)
    assert report.entries, "빈 워치리스트는 참조 문서가 되지 못한다"


def test_watchlist_sample_is_sorted_as_the_contract_promises():
    keys = [(len(e.buy_strategies), e.best_gate_progress) for e in load_watchlist().entries]
    assert keys == sorted(keys, reverse=True)


def test_watchlist_row_expands_into_the_buy_sample():
    """워치리스트 한 줄을 펼치면 그 진단 리포트가 나온다는 관계.

    두 예시가 같은 (티커, 날짜)를 가리키므로 프론트는 '목록 -> 상세' 이동을 이 두
    파일만으로 만들 수 있다. 값이 어긋나면 목록과 상세가 갈라졌다는 뜻이다.
    """
    buy = load("sample_buy.json")
    row = next(e for e in load_watchlist().entries if e.ticker == buy.ticker)

    assert row.as_of == buy.as_of
    assert row.price == buy.price
    assert row.buy_strategies == buy.consensus.buy_strategies
    assert [s.verdict for s in row.strategies] == [
        v.verdict for v in buy.strategy_verdicts
    ]


def test_watchlist_sample_matches_the_current_implementation_output(market):
    """드리프트 차단 — 진단 예시와 같은 규율을 워치리스트에도 건다."""
    import make_examples

    expected = make_examples.watchlist_payload(market, load("sample_buy.json").as_of)
    actual = (EXAMPLES_DIR / WATCHLIST_FILE).read_text(encoding="utf-8")
    assert actual == expected, (
        f"{WATCHLIST_FILE}이 현재 구현의 출력과 다르다 — "
        "python scripts/make_examples.py 로 재생성할 것"
    )
