"""손절 / 포지션 사이징 / R-multiple.

## 이 모듈은 판정하지 않는다

`RiskPlan`은 전략 판정과 **독립**이다. 전략이 '어디가 진입 지점인가'(피벗)를 정하면,
여기서 계좌 파라미터를 얹어 손절가·주수·R 배수 목표가를 계산한다. 반대로 여기서
"점수가 낮으니 작게 사라" 같은 조정을 하면 판정 로직이 새는 것이다.

## 왜 전략마다 플랜이 다른가

세 전략의 피벗이 서로 다르므로 진입가가 다르고, 진입가가 다르면 손절가·주수·목표가가
전부 달라진다. 그래서 리포트는 `risk_plans: dict[전략명, RiskPlan]`을 싣는다.
하나로 합치려면 '어느 방법론을 따를 것인가'를 골라야 하는데, 그 선택은 사용자의 몫이지
조립 코드의 몫이 아니다.

## 플랜이 붙는 판정

**피벗이 정의된 BUY/WATCH 판정에만** 붙는다.

- AVOID/HOLD/REJECTED_BY_GATE: 그 방법론이 사지 않기로 한 것이므로 플랜이 없다.
- 피벗이 없는 판정(NO_SETUP): 진입 지점 자체가 정의되지 않았으므로 계획도 없다.
  0이나 현재가로 채우면 '근거 없는 숫자'가 화면에 뜬다.

## 알려진 한계

손절은 **ATR 배수와 최대 손절폭 중 타이트한 쪽**이며, 방법론별 손절 규칙
(미너비니의 베이스 저점 아래, Qullamaggie의 당일 저가·10일선)은 아직 반영하지 않는다.
그 값들은 계약에 없고 전략마다 정의가 다르므로, 넣으려면 `SetupMetrics`에 전략이
제시하는 손절 후보를 싣는 계약 변경이 먼저다.
"""

from __future__ import annotations

import math

from config import RiskConfig
from core.context import StockContext
from core.types import RiskPlan, RLevel, StrategyVerdict, Verdict

# 플랜을 만들 판정. '진입 의사가 있는' 판정이며 DiagnosisReport validator가 같은 집합을 강제한다.
ACTIONABLE_VERDICTS = (Verdict.BUY, Verdict.WATCH)


def entry_price(ctx: StockContext, verdict: StrategyVerdict) -> float | None:
    """진입가. 피벗이 아직 위면 **피벗**, 이미 넘었으면 **현재가**.

    피벗 위에서 사는 것이 세 방법론의 공통 전제다. 아직 돌파 전(PIVOT_READY)이면
    체결은 피벗에서 일어나므로 그 가격으로 계획을 세워야 손절폭이 실제와 맞는다.
    현재가로 계산하면 손절폭이 실제보다 넓게 나온다.

    피벗이 없으면 None이다 — 진입 지점이 정의되지 않았다는 뜻이고, 추측하지 않는다.
    """
    pivot = verdict.setup_metrics.pivot_price
    if pivot is None or pivot <= 0.0:
        return None
    price = ctx.price
    return pivot if pivot > price else price


def compute_stop(entry: float, atr14: float | None, config: RiskConfig) -> float | None:
    """ATR 배수 손절과 max_stop_pct 중 **더 타이트한 쪽**(= 진입가에 가까운 쪽).

    더 타이트한 쪽 = 더 높은 손절가이므로 후보들의 최댓값이다.
    ATR을 산출할 수 없으면 최대 손절폭만으로 계산한다 — 손절은 없앨 수 없는 값이라
    None으로 두지 않는다. 진입가가 0 이하이면 계산 자체가 무의미하므로 None이다.
    """
    if entry <= 0.0:
        return None

    candidates = [entry * (1.0 - config.max_stop_pct / 100.0)]
    if atr14 is not None and atr14 > 0.0:
        candidates.append(entry - config.stop_atr_multiple * atr14)

    stop = max(candidates)
    return stop if 0.0 < stop < entry else None


def size_position(
    entry: float, stop: float, config: RiskConfig
) -> tuple[int | None, float | None, float | None]:
    """반환: (shares, position_value, risk_amount). equity 없으면 전부 None.

    주수는 두 제약의 **작은 쪽**이다:
      - 리스크 예산: 계좌 * risk_pct_per_trade / 1R
      - 포지션 상한: 계좌 * max_position_pct / 진입가

    risk_amount는 예산이 아니라 **체결 기준 실제 손실액**(주수 * 1R)이다.
    포지션 상한에 걸려 주수가 깎이면 실제 리스크도 예산보다 작아지는데, 예산을 그대로
    실으면 화면의 숫자가 거짓이 된다.
    """
    equity = config.account_equity
    r_per_share = entry - stop
    if equity is None or equity <= 0.0 or r_per_share <= 0.0 or entry <= 0.0:
        return None, None, None

    by_risk = math.floor(equity * config.risk_pct_per_trade / 100.0 / r_per_share)
    by_cap = math.floor(equity * config.max_position_pct / 100.0 / entry)
    shares = max(0, min(by_risk, by_cap))

    return shares, shares * entry, shares * r_per_share


def r_multiple_prices(entry: float, r_per_share: float, targets: tuple[float, ...]) -> list[float]:
    """entry + n * r_per_share."""
    return [entry + multiple * r_per_share for multiple in targets]


def _suggested_action(index: int, total: int) -> str:
    """R 목표 지점에서의 행동 규칙.

    조언이 아니라 **규칙 서술**이다. 목표 배수는 config에서 오므로 개수가 달라질 수 있어
    값이 아니라 순서로 분기한다 (1.0/2.0/3.0을 리터럴로 박으면 config가 거짓말이 된다).
    """
    if index == 0:
        return "손절을 진입가(본전)로 올린다"
    if index == total - 1:
        return "추격 손절로 전환한다"
    return "포지션 일부를 정리해 남은 리스크를 줄인다"


def build_risk_plan(
    ctx: StockContext,
    verdict: StrategyVerdict,
    config: RiskConfig,
) -> RiskPlan | None:
    """판정 하나의 리스크 플랜. 진입 의사가 없거나 피벗이 없으면 None."""
    if verdict.verdict not in ACTIONABLE_VERDICTS:
        return None

    entry = entry_price(ctx, verdict)
    if entry is None:
        return None

    stop = compute_stop(entry, ctx.indicators.atr14, config)
    if stop is None:
        return None

    r_per_share = entry - stop
    shares, position_value, risk_amount = size_position(entry, stop, config)
    equity = config.account_equity

    targets = config.r_targets
    levels = [
        RLevel(
            multiple=multiple,
            price=round(price, 4),
            suggested_action=_suggested_action(index, len(targets)),
        )
        for index, (multiple, price) in enumerate(
            zip(targets, r_multiple_prices(entry, r_per_share, targets), strict=True)
        )
    ]

    exit_rules = [
        f"손절 {stop:.2f} 이탈 시 청산 — 1R = {r_per_share:.2f} "
        f"({r_per_share / entry * 100.0:.1f}%)",
        "손절은 위로만 옮긴다. 아래로 내리는 것은 계획 변경이 아니라 계획 폐기다",
    ]
    if verdict.setup_metrics.pivot_price is not None and entry > ctx.price:
        exit_rules.append(
            f"피벗 {entry:.2f}을 넘지 못하면 진입 자체가 없다 — 현재가 {ctx.price:.2f}"
        )
    if not ctx.bar_meta.is_bar_complete:
        exit_rules.append("당일 봉이 미완성이라 진입가·손절가는 아직 확정이 아니다")

    return RiskPlan(
        entry=round(entry, 4),
        stop=round(stop, 4),
        stop_pct=round(r_per_share / entry * 100.0, 4),
        r_per_share=round(r_per_share, 4),
        shares=shares,
        position_value=None if position_value is None else round(position_value, 4),
        account_equity=equity,
        risk_pct=(
            None
            if risk_amount is None or not equity
            else round(risk_amount / equity * 100.0, 4)
        ),
        risk_amount=None if risk_amount is None else round(risk_amount, 4),
        r_levels=levels,
        exit_rules=exit_rules,
    )


def build_risk_plans(
    ctx: StockContext,
    verdicts: list[StrategyVerdict],
    config: RiskConfig,
) -> dict[str, RiskPlan]:
    """전략별 리스크 플랜. 리포트 조립과 목업 생성이 공유하는 유일한 진입점이다."""
    plans: dict[str, RiskPlan] = {}
    for verdict in verdicts:
        plan = build_risk_plan(ctx, verdict, config)
        if plan is not None:
            plans[verdict.strategy_name] = plan
    return plans
