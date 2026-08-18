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
    GateResult,
    ScoreComponent,
    SetupState,
    StrategyVerdict,
    Verdict,
)


@runtime_checkable
class Strategy(Protocol):
    """전략 플러그인 인터페이스.

    이 Protocol만 구현하면 코어 수정 없이 전략이 추가된다.
    전략끼리 서로를 import 하지 않는다.
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

    def evaluate(self, ctx: StockContext) -> StrategyVerdict:
        """템플릿 메서드. 오버라이드 금지.

        게이트 탈락 시 build_score()를 호출하지 않고 즉시 REJECTED_BY_GATE를 반환한다.
        score=None이지 0.0이 아니다 ('채점 안 함' != '낮은 점수').
        """
        raise NotImplementedError
