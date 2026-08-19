"""Strategy Protocol + GATE -> SCORE 순서를 구조적으로 강제하는 베이스.

CLAUDE.md 원칙 1은 주석으로만 지켜지지 않는다. StrategyBase.evaluate()가 템플릿 메서드로
고정되어 있어서, 게이트 탈락 시 build_score()가 호출조차 되지 않는다.
하위 전략은 evaluate()를 오버라이드하지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from core.context import StockContext
from core.types import (
    CheckStatus,
    Comparator,
    GateCheck,
    GateResult,
    ScoreComponent,
    SetupMetrics,
    SetupState,
    StrategyVerdict,
    Verdict,
)


def build_gate_check(
    *,
    id: str,
    label: str,
    status: CheckStatus,
    reason: str,
    actual: float | None = None,
    threshold: float | None = None,
    comparator: Comparator = Comparator.GTE,
    unit: str | None = None,
) -> GateCheck:
    """GateCheck 생성의 단일 진입점. shortfall_pct를 여기서만 계산한다.

    미달 폭 계산을 전략마다 하면 comparator 방향 해석과 정규화 기준이 갈라지고,
    결국 프론트가 자기 방식으로 다시 계산하게 된다 (원칙 4 누수).
    계산 주체를 백엔드 한 곳으로 고정하는 것이 이 함수의 존재 이유다.

    shortfall_pct = |actual - threshold| / |threshold| * 100, 단 FAIL일 때만.
    threshold가 0이면 정규화가 불가능하므로 None이다 (기울기 > 0 같은 조건).
    이 경우 근접도 정렬에 기여하지 못하는 것은 알려진 한계다.
    """
    shortfall: float | None = None
    if (
        status is CheckStatus.FAIL
        and comparator is not Comparator.BOOL
        and actual is not None
        and threshold is not None
        and threshold != 0.0
    ):
        shortfall = abs(actual - threshold) / abs(threshold) * 100.0

    return GateCheck(
        id=id,
        label=label,
        status=status,
        actual=actual,
        threshold=threshold,
        comparator=comparator,
        unit=unit,
        shortfall_pct=shortfall,
        reason=reason,
    )


def build_gate_result(
    strategy: str,
    checks: list[GateCheck],
    *,
    required_count: int | None = None,
) -> GateResult:
    """게이트 통과 판정의 단일 규약. 모든 전략은 이 함수로 GateResult를 만든다.

    UNAVAILABLE 정책 (프로젝트 공통 — 전략마다 제각기 정하지 않는다):
      - UNAVAILABLE은 PASS로 세지 않는다. '확인하지 못함'은 '충족'이 아니다.
      - 따라서 AND 게이트(required_count=None)는 UNAVAILABLE 하나로도 통과가 막힌다.
        보수적 선택이다 — 확인 안 된 조건을 통과로 치면 데이터가 부족한 종목일수록
        게이트가 후해지는 역전이 생긴다.
      - required_count 게이트에서도 분모/기준은 그대로다. UNAVAILABLE은 pass_count에
        포함되지 않을 뿐, required_count를 낮춰 주지 않는다.
      - FAIL과의 구분은 unavailable_count로 계약에 보존된다. 렌더러와 백테스트 집계는
        이 값으로 '조건 미달 탈락'과 '데이터 없어 보류'를 구분해서 보여줘야 한다.
        (verdict enum 차원의 구분은 계약 변경이 필요하므로 Phase 3 스키마 개정 대상.)
    """
    pass_count = sum(c.status is CheckStatus.PASS for c in checks)
    needed = len(checks) if required_count is None else required_count
    return GateResult(
        strategy=strategy,
        passed=pass_count >= needed,
        checks=checks,
        pass_count=pass_count,
        total=len(checks),
        unavailable_count=sum(c.status is CheckStatus.UNAVAILABLE for c in checks),
        required_count=required_count,
    )


@runtime_checkable
class Strategy(Protocol):
    """전략 플러그인 인터페이스.

    이 Protocol만 구현하면 코어 수정 없이 전략이 추가된다.
    전략끼리 서로를 import 하지 않는다.

    **결정론 요구사항**: 같은 StockContext에 대해 evaluate()는 항상 같은 판정을
    내야 한다. look-ahead 감사(backtest/harness.py)가 같은 시점을 두 번 평가해
    비교하는 방식이므로, 호출 순서나 호출 간 누적 상태에 따라 판정이 달라지는
    전략은 감사를 통과할 수 없거나(억울한 적발) 감사를 무력화한다(양쪽이 같은
    오염 상태를 공유). 캐시를 쓰려면 키를 ctx 내용에서만 유도할 것.
    """

    name: str
    version: str

    def build_gate(self, ctx: StockContext) -> GateResult:
        """이진 필터. 추세 조건은 전부 여기 들어간다. 가산점 항목이 아니다."""
        ...

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        """진입 타이밍 채점. 반환: (score, max_score, components).

        게이트를 통과한 종목에 대해서만 호출된다.
        """
        ...

    def detect_setup(self, ctx: StockContext) -> SetupState:
        """차트 셋업 단계 판정."""
        ...

    def evaluate(self, ctx: StockContext) -> StrategyVerdict:
        """게이트 -> 점수 순으로 평가한 최종 판정."""
        ...


class StrategyBase(ABC):
    """모든 전략의 공통 베이스. evaluate()의 순서를 고정한다."""

    name: str = "unnamed"
    version: str = "0.1.0"

    @abstractmethod
    def build_gate(self, ctx: StockContext) -> GateResult: ...

    @abstractmethod
    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]: ...

    @abstractmethod
    def detect_setup(self, ctx: StockContext) -> SetupState: ...

    @abstractmethod
    def decide(
        self,
        ctx: StockContext,
        score: float,
        max_score: float,
        components: list[ScoreComponent],
        setup: SetupState,
    ) -> tuple[Verdict, list[str]]:
        """게이트 통과 종목의 최종 판정과 근거 메모. 반환: (verdict, notes)."""
        ...

    def build_setup_metrics(self, ctx: StockContext) -> SetupMetrics:
        """전략별 셋업 수치. 기본은 비어 있다.

        피벗/베이스 정의가 전략마다 다르므로 IndicatorSnapshot이 아니라 여기서 채운다.
        """
        return SetupMetrics()

    def evaluate(self, ctx: StockContext) -> StrategyVerdict:
        """템플릿 메서드. **오버라이드 금지.**

        게이트 탈락 시 build_score()를 호출하지 않고 즉시 REJECTED_BY_GATE를 반환한다.
        score=None이지 0.0이 아니다 ('채점 안 함' != '낮은 점수').

        셋업 판정(detect_setup / build_setup_metrics)은 채점이 아니므로 **탈락 종목에도
        수행한다**. 게이트 근소 탈락 종목(7/8)이야말로 '피벗까지 얼마나 남았나'를 보고
        싶은 대상인데, NO_SETUP으로 채워 버리면 '판정 안 함'이 '셋업 없음'으로 위장된다.
        """
        gate = self.build_gate(ctx)

        if gate.strategy != self.name:
            raise ValueError(
                f"GateResult.strategy({gate.strategy!r})가 전략명({self.name!r})과 다르다"
            )

        if not gate.passed:
            notes = [f"게이트 {gate.pass_count}/{gate.total} 통과 — 채점하지 않았다"]
            if gate.unavailable_count:
                notes.append(
                    f"{gate.unavailable_count}개 조건은 데이터 없음(UNAVAILABLE) — "
                    "조건 미달과 다르다. 데이터가 갖춰지면 판정이 바뀔 수 있다"
                )
            return StrategyVerdict(
                strategy_name=self.name,
                strategy_version=self.version,
                gate=gate,
                verdict=Verdict.REJECTED_BY_GATE,
                setup_state=self.detect_setup(ctx),
                setup_metrics=self.build_setup_metrics(ctx),
                notes=notes,
            )

        score, max_score, components = self.build_score(ctx)
        setup = self.detect_setup(ctx)
        verdict, notes = self.decide(ctx, score, max_score, components, setup)

        return StrategyVerdict(
            strategy_name=self.name,
            strategy_version=self.version,
            gate=gate,
            score=score,
            max_score=max_score,
            components=components,
            setup_state=setup,
            setup_metrics=self.build_setup_metrics(ctx),
            verdict=verdict,
            notes=notes,
        )
