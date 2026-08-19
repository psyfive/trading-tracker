"""데이터 계약 불변식 테스트.

구현 테스트가 아니라 스키마 테스트다. core/types.py가 약속한 것들이
실제로 강제되는지 잠근다. 이 테스트가 깨지면 프론트엔드 계약이 깨진 것이다.

참고: 여기서는 model_dump_json을 직접 호출한다. 프로덕션 코드에서는
render/json_out.py의 함수만 써야 하지만(CLAUDE.md), 이 테스트의 대상이
직렬화 형태 그 자체이므로 예외다.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from core.types import (
    Agreement,
    BarMeta,
    CheckStatus,
    Comparator,
    ConsensusSummary,
    Contract,
    DiagnosisReport,
    DiagnosticWarning,
    GateCheck,
    GateProgress,
    GateResult,
    IndicatorSnapshot,
    MarketRegime,
    MinerviniSetup,
    QullamaggieSetup,
    RiskPlan,
    RLevel,
    ScoreComponent,
    SessionState,
    SetupMetrics,
    Severity,
    Stage,
    StrategyVerdict,
    Verdict,
    WarningCode,
    WeinsteinSetup,
)

# ---------------------------------------------------------------------------
# 빌더 헬퍼
# ---------------------------------------------------------------------------


def make_check(*, status: CheckStatus = CheckStatus.PASS, cid: str = "c1") -> GateCheck:
    return GateCheck(
        id=cid,
        label="주가 > 200일선",
        status=status,
        actual=182.4,
        threshold=164.1,
        comparator=Comparator.GT,
        unit="$",
        reason="주가 182.4 > 200일선 164.1",
    )


def make_gate(*, passed: bool = True, strategy: str = "minervini") -> GateResult:
    checks = [make_check(cid="c1")] if passed else [make_check(status=CheckStatus.FAIL, cid="c1")]
    return GateResult(
        strategy=strategy,
        passed=passed,
        checks=checks,
        pass_count=sum(c.status is CheckStatus.PASS for c in checks),
        total=len(checks),
        unavailable_count=sum(c.status is CheckStatus.UNAVAILABLE for c in checks),
    )


def make_verdict(*, gate_passed: bool = True, name: str = "minervini") -> StrategyVerdict:
    gate = make_gate(passed=gate_passed, strategy=name)
    if not gate_passed:
        return StrategyVerdict(
            strategy_name=name,
            gate=gate,
            verdict=Verdict.REJECTED_BY_GATE,
        )
    return StrategyVerdict(
        strategy_name=name,
        gate=gate,
        score=72.0,
        max_score=100.0,
        components=[
            ScoreComponent(id="vcp", label="VCP 수축", earned=18.0, max=25.0, detail="3회 수축")
        ],
        setup_metrics=SetupMetrics(
            pivot_price=185.0,
            to_pivot_pct=1.4,
            detail=MinerviniSetup(contraction_ratio=0.38),
        ),
        verdict=Verdict.BUY,
    )


def progress_from(verdicts: list[StrategyVerdict]) -> list[GateProgress]:
    return [
        GateProgress(
            strategy=v.strategy_name,
            passed=v.gate.passed,
            pass_count=v.gate.pass_count,
            total=v.gate.total,
            unavailable_count=v.gate.unavailable_count,
            progress_ratio=v.gate.pass_count / v.gate.total,
        )
        for v in verdicts
    ]


def derive_agreement(verdicts: list[StrategyVerdict]) -> Agreement:
    """Agreement enum docstring의 정의 그대로. validator가 이 파생을 강제한다."""
    total = len(verdicts)
    n_buy = sum(v.verdict is Verdict.BUY for v in verdicts)
    if n_buy == 0:
        return Agreement.NONE
    if n_buy == total:
        return Agreement.UNANIMOUS_BUY
    if n_buy * 2 > total:
        return Agreement.MAJORITY_BUY
    return Agreement.SPLIT


def consensus_from(verdicts: list[StrategyVerdict]) -> ConsensusSummary:
    counts: dict[Verdict, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return ConsensusSummary(
        total_strategies=len(verdicts),
        verdict_counts=counts,
        buy_strategies=[v.strategy_name for v in verdicts if v.verdict is Verdict.BUY],
        gate_passed_strategies=[v.strategy_name for v in verdicts if v.gate.passed],
        gate_progress=progress_from(verdicts),
        agreement=derive_agreement(verdicts),
    )


def make_report(
    *,
    is_bar_complete: bool = True,
    verdicts: list[StrategyVerdict] | None = None,
    warnings: list[DiagnosticWarning] | None = None,
    consensus: ConsensusSummary | None = None,
) -> DiagnosisReport:
    verdicts = [make_verdict()] if verdicts is None else verdicts
    warnings = [] if warnings is None else warnings
    return DiagnosisReport(
        ticker="AAPL",
        as_of=date(2026, 8, 17),
        generated_at=datetime(2026, 8, 18, 13, 0, tzinfo=UTC),
        price=182.4,
        is_bar_complete=is_bar_complete,
        bar_meta=BarMeta(
            last_bar_date=date(2026, 8, 17),
            session_state=SessionState.CLOSED if is_bar_complete else SessionState.OPEN,
            is_bar_complete=is_bar_complete,
            bars_available=756,
            volume_judgements_reliable=is_bar_complete,
        ),
        regime=MarketRegime.RISK_ON,
        stage=Stage.STAGE_2,
        indicators=IndicatorSnapshot(sma200=164.1, rsi14=61.2, rs_percentile=88.0),
        strategy_verdicts=verdicts,
        consensus=consensus_from(verdicts) if consensus is None else consensus,
        warnings=warnings,
    )


def incomplete_bar_warning() -> DiagnosticWarning:
    return DiagnosticWarning(
        code=WarningCode.INCOMPLETE_BAR,
        severity=Severity.WARN,
        message="장중 실행 — 당일 봉이 미완성이므로 거래량 판정은 신뢰할 수 없다",
        field="indicators.volume_ratio",
    )


# ---------------------------------------------------------------------------
# 직렬화 왕복
# ---------------------------------------------------------------------------


def test_report_json_round_trip():
    report = make_report()
    restored = DiagnosisReport.model_validate_json(report.model_dump_json())
    assert restored == report


def test_report_is_json_serializable():
    payload = json.loads(make_report().model_dump_json())
    assert payload["ticker"] == "AAPL"
    assert payload["as_of"] == "2026-08-17"


def test_schema_version_is_an_actual_field():
    """상수가 아니라 리포트에 실려 나가야 프론트가 계약 버전을 읽는다."""
    from core.types import SCHEMA_VERSION

    assert "schema_version" in DiagnosisReport.model_fields
    assert json.loads(make_report().model_dump_json())["schema_version"] == SCHEMA_VERSION


def test_no_field_uses_an_alias():
    """별칭을 쓰지 않기로 했다. by_alias 인자 없이도 키가 정확해야 한다."""
    for model in Contract.__subclasses__():
        for name, field in model.model_fields.items():
            assert field.alias is None, f"{model.__name__}.{name}에 별칭이 남아있다"


def test_enum_values_are_json_strings():
    payload = json.loads(make_report().model_dump_json())
    assert payload["regime"] == "RISK_ON"
    assert payload["strategy_verdicts"][0]["verdict"] == "BUY"


@pytest.mark.parametrize(
    "enum_cls",
    [
        MarketRegime,
        Stage,
        Verdict,
        CheckStatus,
        Severity,
        WarningCode,
        SessionState,
        Agreement,
        Comparator,
    ],
)
def test_enum_name_equals_value(enum_cls):
    for member in enum_cls:
        assert member.name == member.value


# ---------------------------------------------------------------------------
# GATE -> SCORE 불변식 (원칙 1)
# ---------------------------------------------------------------------------


def test_gate_rejected_verdict_cannot_carry_score():
    with pytest.raises(ValidationError, match="채점하지 않는다"):
        StrategyVerdict(
            strategy_name="minervini",
            gate=make_gate(passed=False),
            score=0.0,
            verdict=Verdict.REJECTED_BY_GATE,
        )


def test_gate_rejected_verdict_cannot_carry_components():
    with pytest.raises(ValidationError, match="채점하지 않는다"):
        StrategyVerdict(
            strategy_name="minervini",
            gate=make_gate(passed=False),
            components=[ScoreComponent(id="x", label="x", earned=1.0, max=2.0, detail="d")],
            verdict=Verdict.REJECTED_BY_GATE,
        )


def test_gate_failed_must_be_rejected_verdict():
    with pytest.raises(ValidationError, match="REJECTED_BY_GATE"):
        StrategyVerdict(
            strategy_name="minervini", gate=make_gate(passed=False), verdict=Verdict.BUY
        )


def test_gate_passed_cannot_be_rejected_verdict():
    with pytest.raises(ValidationError, match="REJECTED_BY_GATE"):
        StrategyVerdict(
            strategy_name="minervini",
            gate=make_gate(passed=True),
            verdict=Verdict.REJECTED_BY_GATE,
        )


def test_rejected_verdict_score_is_none_not_zero():
    verdict = make_verdict(gate_passed=False)
    assert verdict.score is None
    assert verdict.score_pct is None
    assert json.loads(verdict.model_dump_json())["score"] is None


# ---------------------------------------------------------------------------
# CheckStatus 3-state — bool 미러링 금지
# ---------------------------------------------------------------------------


def test_gate_check_has_no_passed_bool():
    """3상태를 bool로 접으면 UNAVAILABLE이 FAIL로 둔갑한다."""
    assert "passed" not in GateCheck.model_fields
    with pytest.raises(ValidationError):
        GateCheck(id="c", label="l", status=CheckStatus.PASS, passed=True, reason="r")


def test_unavailable_is_distinguishable_from_fail_in_json():
    unavailable = json.loads(make_check(status=CheckStatus.UNAVAILABLE).model_dump_json())
    failed = json.loads(make_check(status=CheckStatus.FAIL).model_dump_json())
    assert unavailable["status"] == "UNAVAILABLE"
    assert failed["status"] == "FAIL"


def test_gate_result_counts_must_match_checks():
    with pytest.raises(ValidationError, match="pass_count"):
        GateResult(
            strategy="s",
            passed=True,
            checks=[make_check(), make_check(status=CheckStatus.FAIL, cid="c2")],
            pass_count=2,
            total=2,
        )


def test_gate_result_total_must_match_checks_length():
    with pytest.raises(ValidationError, match="total"):
        GateResult(strategy="s", passed=True, checks=[make_check()], pass_count=1, total=3)


def test_gate_result_unavailable_count_must_match():
    with pytest.raises(ValidationError, match="unavailable_count"):
        GateResult(
            strategy="s",
            passed=False,
            checks=[make_check(status=CheckStatus.UNAVAILABLE)],
            pass_count=0,
            total=1,
            unavailable_count=0,
        )


def test_gate_result_helper_properties():
    gate = GateResult(
        strategy="s",
        passed=False,
        checks=[
            make_check(cid="ok"),
            make_check(status=CheckStatus.FAIL, cid="bad"),
            make_check(status=CheckStatus.UNAVAILABLE, cid="na"),
        ],
        pass_count=1,
        total=3,
        unavailable_count=1,
    )
    assert [c.id for c in gate.failed_checks] == ["bad"]
    assert [c.id for c in gate.unavailable_checks] == ["na"]


# ---------------------------------------------------------------------------
# 게이트 근접도 — 워치리스트 정렬용
# ---------------------------------------------------------------------------


def test_gate_progress_is_reachable_without_deep_nesting():
    """report.consensus.gate_progress 한 단계로 정렬 키에 닿아야 한다."""
    report = make_report(verdicts=[make_verdict(), make_verdict(gate_passed=False, name="canslim")])
    keyed = {g.strategy: g.progress_ratio for g in report.consensus.gate_progress}
    assert keyed == {"minervini": 1.0, "canslim": 0.0}
    assert report.consensus.best_gate_progress == 1.0


def test_gate_progress_must_match_underlying_gates():
    """중복 저장된 값이 원본과 어긋나면 거부한다."""
    verdicts = [make_verdict()]
    with pytest.raises(ValidationError, match="gate_progress"):
        DiagnosisReport(
            ticker="AAPL",
            as_of=date(2026, 8, 17),
            generated_at=datetime(2026, 8, 18, 13, 0, tzinfo=UTC),
            price=182.4,
            is_bar_complete=True,
            bar_meta=BarMeta(
                last_bar_date=date(2026, 8, 17),
                is_bar_complete=True,
                bars_available=756,
                volume_judgements_reliable=True,
            ),
            regime=MarketRegime.RISK_ON,
            stage=Stage.STAGE_2,
            indicators=IndicatorSnapshot(),
            strategy_verdicts=verdicts,
            consensus=ConsensusSummary(
                total_strategies=1,
                gate_progress=[
                    GateProgress(
                        strategy="minervini",
                        passed=True,
                        pass_count=99,
                        total=99,
                        progress_ratio=1.0,
                    )
                ],
            ),
        )


# ---------------------------------------------------------------------------
# is_bar_complete
# ---------------------------------------------------------------------------


def test_incomplete_bar_requires_warning():
    with pytest.raises(ValidationError, match="INCOMPLETE_BAR"):
        make_report(is_bar_complete=False, warnings=[])


def test_incomplete_bar_with_warning_is_valid():
    report = make_report(is_bar_complete=False, warnings=[incomplete_bar_warning()])
    assert report.is_bar_complete is False
    assert report.bar_meta.volume_judgements_reliable is False


def test_is_bar_complete_must_mirror_bar_meta():
    with pytest.raises(ValidationError, match="bar_meta와 불일치"):
        DiagnosisReport(
            ticker="AAPL",
            as_of=date(2026, 8, 17),
            generated_at=datetime(2026, 8, 18, 13, 0, tzinfo=UTC),
            price=182.4,
            is_bar_complete=True,
            bar_meta=BarMeta(
                last_bar_date=date(2026, 8, 17),
                is_bar_complete=False,
                bars_available=756,
                volume_judgements_reliable=False,
            ),
            regime=MarketRegime.RISK_ON,
            stage=Stage.STAGE_2,
            indicators=IndicatorSnapshot(),
            strategy_verdicts=[],
            consensus=ConsensusSummary(total_strategies=0),
        )


# ---------------------------------------------------------------------------
# 컨센서스 — 평균 없음
# ---------------------------------------------------------------------------


def test_consensus_has_no_aggregate_score_field():
    forbidden = {"average_score", "avg_score", "total_score", "combined_score", "overall_score"}
    assert forbidden.isdisjoint(ConsensusSummary.model_fields)


def test_consensus_total_must_match_verdicts():
    with pytest.raises(ValidationError, match="total_strategies"):
        DiagnosisReport(
            ticker="AAPL",
            as_of=date(2026, 8, 17),
            generated_at=datetime(2026, 8, 18, 13, 0, tzinfo=UTC),
            price=182.4,
            is_bar_complete=True,
            bar_meta=BarMeta(
                last_bar_date=date(2026, 8, 17),
                is_bar_complete=True,
                bars_available=756,
                volume_judgements_reliable=True,
            ),
            regime=MarketRegime.RISK_ON,
            stage=Stage.STAGE_2,
            indicators=IndicatorSnapshot(),
            strategy_verdicts=[make_verdict()],
            consensus=ConsensusSummary(total_strategies=99),
        )


def test_empty_gate_progress_cannot_bypass_validation():
    """전략이 있는데 gate_progress가 비어 있으면 조립 코드가 채우는 것을 잊은 것이다."""
    verdicts = [make_verdict()]
    consensus = consensus_from(verdicts).model_copy(update={"gate_progress": []})
    with pytest.raises(ValidationError, match="gate_progress"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_progress_ratio_must_match_counts():
    """정렬 키로 쓰는 바로 그 값이므로 pass_count/total과 어긋나면 거부한다."""
    verdicts = [make_verdict()]
    progress = progress_from(verdicts)
    tampered = [progress[0].model_copy(update={"progress_ratio": 0.5})]
    consensus = consensus_from(verdicts).model_copy(update={"gate_progress": tampered})
    with pytest.raises(ValidationError, match="progress_ratio"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_progress_ratio_tolerates_rounding():
    """목업처럼 소수 4자리로 반올림된 값(2/3 -> 0.6667)은 통과해야 한다."""
    verdicts = [make_verdict()]
    progress = [progress_from(verdicts)[0].model_copy(update={"progress_ratio": 1.0001})]
    consensus = consensus_from(verdicts).model_copy(update={"gate_progress": progress})
    make_report(verdicts=verdicts, consensus=consensus)  # ValidationError가 없어야 한다


def test_verdict_counts_must_match_verdicts():
    verdicts = [make_verdict()]
    consensus = consensus_from(verdicts).model_copy(
        update={"verdict_counts": {Verdict.BUY: 3}}
    )
    with pytest.raises(ValidationError, match="verdict_counts"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_buy_strategies_must_match_verdicts():
    """BUY 전략 누락은 프론트에서 '매수 의견 없음'으로 위장된다. 조립 시점에 죽어야 한다."""
    verdicts = [make_verdict()]
    consensus = consensus_from(verdicts).model_copy(update={"buy_strategies": []})
    with pytest.raises(ValidationError, match="buy_strategies"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_gate_passed_strategies_must_match_verdicts():
    verdicts = [make_verdict()]
    consensus = consensus_from(verdicts).model_copy(
        update={"gate_passed_strategies": ["minervini", "ghost"]}
    )
    with pytest.raises(ValidationError, match="gate_passed_strategies"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_agreement_is_derived_not_free():
    """1 BUY / 3 전략은 SPLIT이다. UNANIMOUS_BUY라고 주장하면 거부한다."""
    verdicts = [
        make_verdict(name="weinstein"),
        make_verdict(gate_passed=False, name="minervini"),
        make_verdict(gate_passed=False, name="canslim"),
    ]
    assert derive_agreement(verdicts) is Agreement.SPLIT
    consensus = consensus_from(verdicts).model_copy(
        update={"agreement": Agreement.UNANIMOUS_BUY}
    )
    with pytest.raises(ValidationError, match="agreement"):
        make_report(verdicts=verdicts, consensus=consensus)


def test_agreement_counts_rejected_as_non_buy():
    """분모는 전체 전략 수다. 게이트 탈락도 '비-BUY 의견'으로 센다 (examples와 일치)."""
    two_of_three = [
        make_verdict(name="a"),
        make_verdict(name="b"),
        make_verdict(gate_passed=False, name="c"),
    ]
    assert derive_agreement(two_of_three) is Agreement.MAJORITY_BUY
    report = make_report(verdicts=two_of_three)
    assert report.consensus.agreement is Agreement.MAJORITY_BUY


def test_verdicts_stay_separate():
    verdicts = [make_verdict(name="minervini"), make_verdict(gate_passed=False, name="canslim")]
    payload = json.loads(make_report(verdicts=verdicts).model_dump_json())
    assert [v["strategy_name"] for v in payload["strategy_verdicts"]] == ["minervini", "canslim"]
    assert payload["strategy_verdicts"][0]["verdict"] == "BUY"
    assert payload["strategy_verdicts"][1]["verdict"] == "REJECTED_BY_GATE"


# ---------------------------------------------------------------------------
# 지표 vs 셋업 수치 분리
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["pivot_price", "to_pivot_pct", "base_length_days", "base_depth_pct"],
)
def test_strategy_dependent_fields_are_not_indicators(field_name):
    """두 전략이 다르게 계산할 수 있으면 지표가 아니다."""
    assert field_name not in IndicatorSnapshot.model_fields
    assert field_name in SetupMetrics.model_fields


@pytest.mark.parametrize("field_name", ["contraction_ratio", "volume_dryup_ratio"])
def test_vcp_vocabulary_lives_in_the_strategy_detail(field_name):
    """VCP 어휘는 공통 코어가 아니라 미너비니 detail에 있어야 한다.

    공통 코어에 두면 와인스타인·CANSLIM이 채울 수 없는 필드를 이고 다니게 된다.
    """
    assert field_name not in SetupMetrics.model_fields
    assert field_name in MinerviniSetup.model_fields


def test_setup_metrics_live_on_strategy_verdict():
    verdict = make_verdict()
    assert verdict.setup_metrics.pivot_price == 185.0
    assert "setup_metrics" in json.loads(verdict.model_dump_json())


def test_setup_metrics_default_to_empty():
    """미제공 시 전 필드 None. 0으로 채우지 않는다."""
    metrics = make_verdict(gate_passed=False).setup_metrics
    assert metrics.pivot_price is None
    assert metrics.detail is None


def test_each_strategy_keeps_its_own_pivot():
    """전략마다 피벗 정의가 달라도 각자 보존되어야 한다."""
    a = StrategyVerdict(
        strategy_name="minervini",
        gate=make_gate(),
        score=70.0,
        max_score=100.0,
        setup_metrics=SetupMetrics(pivot_price=185.0),
        verdict=Verdict.BUY,
    )
    b = StrategyVerdict(
        strategy_name="qullamaggie",
        gate=make_gate(strategy="qullamaggie"),
        score=55.0,
        max_score=100.0,
        setup_metrics=SetupMetrics(pivot_price=190.5),
        verdict=Verdict.WATCH,
    )
    assert a.setup_metrics.pivot_price != b.setup_metrics.pivot_price


# ---------------------------------------------------------------------------
# 숫자 필드 접미사 규약
# ---------------------------------------------------------------------------


def test_scale_words_are_suffixes_not_prefixes():
    """pct_from_52w_high 같은 접두사형 이름이 다시 생기는 것을 막는다."""
    offenders = []
    for model in Contract.__subclasses__():
        for name in model.model_fields:
            for token in ("pct", "ratio", "percentile"):
                if token in name and not name.endswith(token):
                    offenders.append(f"{model.__name__}.{name}")
    assert offenders == [], f"접미사 규약 위반: {offenders}"


@pytest.mark.parametrize(
    ("model", "field_name"),
    [
        (IndicatorSnapshot, "sma200_slope_20d_pct"),
        (IndicatorSnapshot, "from_52w_high_pct"),
        (IndicatorSnapshot, "above_52w_low_pct"),
        (IndicatorSnapshot, "volume_ratio"),
        (IndicatorSnapshot, "rs_percentile"),
        (MinerviniSetup, "contraction_ratio"),
        (MinerviniSetup, "volume_dryup_ratio"),
        (SetupMetrics, "base_depth_pct"),
    ],
)
def test_scaled_fields_carry_expected_suffix(model, field_name):
    assert field_name in model.model_fields


# ---------------------------------------------------------------------------
# RiskPlan
# ---------------------------------------------------------------------------


def test_r_levels_serialize_as_a_plain_list():
    """별칭 없는 평범한 리스트. by_alias 불필요."""
    plan = RiskPlan(
        entry=100.0,
        stop=92.0,
        stop_pct=8.0,
        r_per_share=8.0,
        r_levels=[
            RLevel(multiple=1.0, price=108.0, suggested_action="손절을 본전으로 이동"),
            RLevel(multiple=2.0, price=116.0, suggested_action="1/3 익절"),
            RLevel(multiple=3.0, price=124.0, suggested_action="추격 손절로 전환"),
        ],
    )
    payload = json.loads(plan.model_dump_json())
    assert [level["multiple"] for level in payload["r_levels"]] == [1.0, 2.0, 3.0]
    assert payload["r_levels"][0]["suggested_action"] == "손절을 본전으로 이동"


def test_r_levels_must_be_ascending():
    with pytest.raises(ValidationError, match="오름차순"):
        RiskPlan(
            entry=100.0,
            stop=92.0,
            stop_pct=8.0,
            r_per_share=8.0,
            r_levels=[
                RLevel(multiple=2.0, price=116.0, suggested_action="a"),
                RLevel(multiple=1.0, price=108.0, suggested_action="b"),
            ],
        )


def test_r_levels_count_is_not_fixed_at_three():
    """목표 배수 개수는 RiskConfig에 달렸다. 스키마가 3개로 못 박지 않는다."""
    plan = RiskPlan(
        entry=100.0,
        stop=92.0,
        stop_pct=8.0,
        r_per_share=8.0,
        r_levels=[RLevel(multiple=1.5, price=112.0, suggested_action="부분 익절")],
    )
    assert len(plan.r_levels) == 1


def test_risk_plan_rejects_stop_at_or_above_entry():
    with pytest.raises(ValidationError, match="손절가는 진입가보다 낮아야 한다"):
        RiskPlan(entry=100.0, stop=100.0, stop_pct=0.0, r_per_share=0.0)


def test_risk_plan_without_equity_has_none_shares():
    plan = RiskPlan(entry=100.0, stop=92.0, stop_pct=8.0, r_per_share=8.0)
    assert plan.shares is None
    assert plan.risk_amount is None


# ---------------------------------------------------------------------------
# 미확정 필드
# ---------------------------------------------------------------------------


def test_position_state_is_not_in_the_contract():
    """보유 추적 미구현 + 소속 위치 미확정. 확정 Phase에서 SCHEMA_VERSION과 함께 추가한다."""
    assert "position_state" not in DiagnosisReport.model_fields
    assert "position_state" not in StrategyVerdict.model_fields


# ---------------------------------------------------------------------------
# 계약 위생
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        IndicatorSnapshot(sma2000=1.0)


def test_models_are_frozen():
    report = make_report()
    with pytest.raises(ValidationError):
        report.ticker = "MSFT"


def test_indicators_default_to_none_not_zero():
    snapshot = IndicatorSnapshot()
    assert snapshot.sma200 is None
    assert snapshot.rs_percentile is None
    assert snapshot.volume_ratio is None


def test_all_contract_models_forbid_extra():
    for model in Contract.__subclasses__():
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_json_schema_generates():
    schema = DiagnosisReport.model_json_schema()
    assert schema["title"] == "DiagnosisReport"
    assert "strategy_verdicts" in schema["properties"]


# ---------------------------------------------------------------------------
# SetupDetail 판별 유니온 (Phase 4에서 3종으로 늘었다)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "kind"),
    [
        (MinerviniSetup, "minervini"),
        (WeinsteinSetup, "weinstein"),
        (QullamaggieSetup, "qullamaggie"),
    ],
)
def test_setup_detail_variants_round_trip_by_kind(model, kind):
    """kind로 분기하는 판별 유니온이므로, 직렬화 후 다시 읽으면 같은 타입이어야 한다."""
    metrics = SetupMetrics(pivot_price=100.0, detail=model())
    payload = json.loads(metrics.model_dump_json())

    assert payload["detail"]["kind"] == kind
    assert type(SetupMetrics.model_validate(payload).detail) is model


def test_unknown_setup_detail_kind_is_rejected():
    """모르는 kind를 조용히 통과시키면 프론트가 빈 detail을 그리게 된다."""
    with pytest.raises(ValidationError):
        SetupMetrics.model_validate({"detail": {"kind": "canslim"}})


@pytest.mark.parametrize(
    ("model", "field_name"),
    [
        (WeinsteinSetup, "stage2_age_days"),
        (WeinsteinSetup, "distance_from_ma30w_pct"),
        (QullamaggieSetup, "prior_move_pct"),
        (QullamaggieSetup, "ma_distance_pct"),
    ],
)
def test_strategy_vocabulary_stays_out_of_the_shared_core(model, field_name):
    """전략 고유 어휘는 SetupMetrics 공통 코어나 IndicatorSnapshot에 올라가면 안 된다.

    올리는 순간 다른 전략들이 채울 수 없는 필드를 이고 다니게 되고,
    '와인스타인의 Stage 나이'가 마치 모든 방법론의 공통 개념인 것처럼 보인다.
    """
    assert field_name in model.model_fields
    assert field_name not in SetupMetrics.model_fields
    assert field_name not in IndicatorSnapshot.model_fields
