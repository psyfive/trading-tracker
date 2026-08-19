"""Qullamaggie 브레이크아웃 — 급등 후 컨솔리데이션(GATE) + 돌파 타이밍(SCORE).

## 이 방법론의 게이트는 '추세'가 아니라 '직전 급등'이다

미너비니·와인스타인은 "추세가 서 있는가"를 묻는다. Qullamaggie는 다르게 묻는다:
**"이미 크게 오른 종목이 지금 쉬고 있는가."** 급등 이력이 없는 종목은 추세가 아무리
곱게 정렬돼 있어도 대상이 아니다. 그래서 게이트 5조건이 이렇게 구성된다:

  직전 급등(prior move) / 변동성(ADR) / 유동성(거래대금) / RS / 20일선 위

ADR과 거래대금이 게이트에 있는 것이 이 전략의 특징이다. 변동성이 없으면 목표 수익이
나올 수 없고, 거래대금이 없으면 그 수익을 실현할 수 없다 — 둘 다 가산점이 아니라
자격 요건이다.

## prior move의 정의

컨솔리데이션 **시작 이전** 구간의 최저가에서 컨솔 고점까지의 상승률이다.
컨솔 시작점을 경계로 삼기 때문에, 컨솔 안에서 가격이 어떻게 움직이든 값이 바뀌지 않는다.
측정 창(prior_move_lookback_days)만큼의 과거가 없으면 **None이고 게이트는 UNAVAILABLE**이다 —
짧은 데이터로 계산한 작은 상승률을 '급등 없음(FAIL)'으로 단정하면 신규 상장주가
조용히 탈락한다.

## 미너비니와 피벗 정의가 다르다

여기서 피벗은 **컨솔리데이션 상단**이다. 미너비니의 '마지막 수축 고점'과 값이 다르며,
그래서 두 값 모두 각자의 SetupMetrics에 보존된다 (IndicatorSnapshot에 두면 한쪽이
다른 쪽의 정의를 강요당한다).

## 결정론

상태를 들고 있지 않으며 모든 계산이 ctx에서만 유도된다. look-ahead 감사의 전제다.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import QullamaggieConfig
from core.context import StockContext
from core.types import (
    CheckStatus,
    Comparator,
    GateResult,
    QullamaggieSetup,
    ScoreComponent,
    SetupMetrics,
    SetupState,
    Verdict,
)
from indicators.core import swing_positions
from strategies.base import StrategyBase, build_gate_check, build_gate_result


@dataclass(frozen=True)
class Consolidation:
    """탐지된 컨솔리데이션. 전부 t 이하 봉에서만 유도된다.

    pivot_price는 **Qullamaggie의 피벗**이다 — 컨솔리데이션 상단(마지막 스윙 고점).
    prior_move_pct는 이 컨솔 **직전**의 상승률이며, 컨솔 내부 움직임과 무관하다.
    """

    start_position: int
    length_days: int
    pivot_price: float
    high_price: float
    low_price: float
    depth_pct: float
    prior_move_pct: float | None
    consolidation_avg_volume: float | None
    recent_avg_volume: float | None

    @property
    def volume_dryup_ratio(self) -> float | None:
        if not self.consolidation_avg_volume or self.recent_avg_volume is None:
            return None
        return self.recent_avg_volume / self.consolidation_avg_volume


def detect_consolidation(ctx: StockContext, config: QullamaggieConfig) -> Consolidation | None:
    """컨솔리데이션과 그 직전 급등폭을 잡는다. 데이터가 모자라면 None.

    앵커(컨솔 시작점)를 최근 consolidation_min_days 봉에서 제외하는 이유는 미너비니의
    베이스 탐지와 같다 — 돌파하는 순간 앵커가 오늘로 점프하면 컨솔이 소멸해서,
    돌파를 탐지해야 할 시점에 셋업이 사라진다.
    """
    df = ctx.ohlcv
    if len(df) < config.consolidation_max_days:
        return None

    window = df.iloc[-config.consolidation_max_days :]
    searchable = window.iloc[: -config.consolidation_min_days]
    if len(searchable) == 0:
        return None

    anchor_offset = int(searchable["high"].to_numpy().argmax())
    consolidation = window.iloc[anchor_offset:]
    start_position = len(df) - len(consolidation)

    # 상단도 '확립된' 값이어야 한다 (오늘 고가를 포함하면 영원히 돌파 불가).
    established = consolidation["high"].iloc[: -config.swing_fractal_k]
    if len(established) == 0:
        return None

    high_price = float(established.max())
    low_price = float(consolidation["low"].min())
    if high_price <= 0:
        return None

    swings = swing_positions(consolidation["high"], config.swing_fractal_k, is_high=True)
    highs = consolidation["high"].to_numpy()
    pivot = float(highs[swings[-1]]) if swings else high_price

    # 직전 급등: 컨솔 시작 이전 구간의 최저가 -> 컨솔 고점.
    # 측정 창이 확보되지 않으면 값을 지어내지 않고 None이다 (게이트는 UNAVAILABLE).
    prior_start = start_position - config.prior_move_lookback_days
    prior_move_pct: float | None = None
    if prior_start >= 0:
        prior_low = float(df["low"].iloc[prior_start : start_position + 1].min())
        if prior_low > 0:
            prior_move_pct = (high_price - prior_low) / prior_low * 100.0

    recent_span = min(len(consolidation), config.swing_fractal_k * 2)
    return Consolidation(
        start_position=start_position,
        length_days=len(consolidation),
        pivot_price=pivot,
        high_price=high_price,
        low_price=low_price,
        depth_pct=(high_price - low_price) / high_price * 100.0,
        prior_move_pct=prior_move_pct,
        consolidation_avg_volume=float(consolidation["volume"].mean()),
        recent_avg_volume=float(consolidation["volume"].iloc[-recent_span:].mean()),
    )


def _decay(value: float, ideal: float, worst: float) -> float:
    """ideal 이하면 1.0, worst 이상이면 0.0, 사이는 선형. 작을수록 좋은 값에 쓴다."""
    if worst <= ideal:
        return 1.0 if value <= ideal else 0.0
    return max(0.0, min(1.0, (worst - value) / (worst - ideal)))


class QullamaggieStrategy(StrategyBase):
    """급등 후 컨솔리데이션 돌파."""

    name = "qullamaggie"
    version = "1.0.0"

    def __init__(self, config: QullamaggieConfig) -> None:
        self.config = config

    # -----------------------------------------------------------------
    # GATE — 자격 요건. 전부 이진 필터다.
    # -----------------------------------------------------------------

    def build_gate(self, ctx: StockContext) -> GateResult:
        cfg = self.config
        ind = ctx.indicators
        price = ctx.price
        consolidation = detect_consolidation(ctx, cfg)

        def threshold_check(
            check_id: str,
            label: str,
            actual: float | None,
            threshold: float,
            *,
            unit: str | None,
            reason_pass: str,
            reason_fail: str,
            reason_missing: str,
        ):
            if actual is None:
                return build_gate_check(
                    id=check_id,
                    label=label,
                    status=CheckStatus.UNAVAILABLE,
                    threshold=threshold,
                    comparator=Comparator.GTE,
                    unit=unit,
                    reason=reason_missing,
                )
            passed = actual >= threshold
            return build_gate_check(
                id=check_id,
                label=label,
                status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                actual=round(actual, 4),
                threshold=threshold,
                comparator=Comparator.GTE,
                unit=unit,
                reason=reason_pass if passed else reason_fail,
            )

        adr = ind.adr20_pct
        dollar_volume = ind.dollar_volume_50
        prior_move = consolidation.prior_move_pct if consolidation is not None else None
        rs = ind.rs_percentile

        checks = [
            # 1. 직전 급등 — 이 방법론의 첫 번째 자격 요건
            threshold_check(
                "prior_move",
                f"직전 급등 +{cfg.min_prior_move_pct:.0f}% 이상",
                prior_move,
                cfg.min_prior_move_pct,
                unit="%",
                reason_pass=(
                    f"컨솔 직전 상승 {prior_move:+.1f}%" if prior_move is not None else ""
                ),
                reason_fail=(
                    f"컨솔 직전 상승 {prior_move:+.1f}% — 기준 {cfg.min_prior_move_pct:.0f}%에 "
                    f"{cfg.min_prior_move_pct - prior_move:.1f}%p 미달. 쉴 만큼 오르지 않았다"
                    if prior_move is not None
                    else ""
                ),
                reason_missing=(
                    "컨솔리데이션 또는 직전 상승 측정 구간이 없다 — "
                    f"{cfg.prior_move_lookback_days}봉 이상의 과거가 필요하다"
                ),
            ),
            # 2. 변동성 — 움직이지 않는 종목에서는 목표 수익이 나오지 않는다
            threshold_check(
                "adr_pct",
                f"ADR {cfg.min_adr_pct:.0f}% 이상",
                adr,
                cfg.min_adr_pct,
                unit="%",
                reason_pass=f"ADR(20) {adr:.2f}%" if adr is not None else "",
                reason_fail=(
                    f"ADR(20) {adr:.2f}% — 기준 {cfg.min_adr_pct:.0f}% 미달. "
                    "변동성이 없어 목표 수익까지 가지 못한다"
                    if adr is not None
                    else ""
                ),
                reason_missing="ADR 미산출 — 20봉 이상 필요",
            ),
            # 3. 유동성 — 수익을 실현할 수 있는가
            threshold_check(
                "dollar_volume",
                "50일 평균 거래대금",
                dollar_volume,
                cfg.min_dollar_volume,
                unit="$",
                reason_pass=(
                    f"50일 평균 거래대금 {dollar_volume:,.0f}"
                    if dollar_volume is not None
                    else ""
                ),
                reason_fail=(
                    f"50일 평균 거래대금 {dollar_volume:,.0f} — "
                    f"기준 {cfg.min_dollar_volume:,.0f} 미달"
                    if dollar_volume is not None
                    else ""
                ),
                reason_missing="50일 평균 거래대금 미산출 — 50봉 이상 필요",
            ),
            # 4. RS 백분위 — 주도주만 본다 (미너비니 기준보다 높다)
            threshold_check(
                "rs_percentile",
                f"RS 백분위 {cfg.min_rs_percentile:.0f} 이상",
                rs,
                cfg.min_rs_percentile,
                unit=None,
                reason_pass=f"RS 백분위 {rs:.1f}" if rs is not None else "",
                reason_fail=(
                    f"RS 백분위 {rs:.1f} — 기준 {cfg.min_rs_percentile:.0f}에 "
                    f"{cfg.min_rs_percentile - rs:.1f} 미달"
                    if rs is not None
                    else ""
                ),
                reason_missing="RS 백분위 미산출 — 유니버스 데이터가 없다",
            ),
        ]

        # 5. 20일선 위 — 컨솔 중에도 추세가 살아 있어야 한다
        ema = ind.ema21
        if ema is None:
            checks.append(
                build_gate_check(
                    id="price_above_ema21",
                    label="주가 > 20일선(EMA21)",
                    status=CheckStatus.UNAVAILABLE,
                    comparator=Comparator.GT,
                    reason="EMA21 미산출 — 상장 기간이 짧다",
                )
            )
        else:
            passed = price > ema
            checks.append(
                build_gate_check(
                    id="price_above_ema21",
                    label="주가 > 20일선(EMA21)",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    actual=round(price, 4),
                    threshold=round(ema, 4),
                    comparator=Comparator.GT,
                    reason=(
                        f"주가 {price:.2f} > EMA21 {ema:.2f}"
                        if passed
                        else f"주가 {price:.2f} <= EMA21 {ema:.2f} — 컨솔이 무너지고 있다"
                    ),
                )
            )

        return build_gate_result(self.name, checks)

    # -----------------------------------------------------------------
    # SCORE — 게이트 통과 종목의 진입 타이밍 품질만
    # -----------------------------------------------------------------

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        cfg = self.config
        ind = ctx.indicators
        consolidation = detect_consolidation(ctx, cfg)
        components: list[ScoreComponent] = []

        # 1. 컨솔 타이트함 — 얕게 조일수록 손절이 가까워진다
        if consolidation is not None:
            quality = _decay(
                consolidation.depth_pct,
                cfg.ideal_consolidation_depth_pct,
                cfg.max_consolidation_depth_pct,
            )
            components.append(
                ScoreComponent(
                    id="consolidation_tightness",
                    label="컨솔 타이트함",
                    earned=round(cfg.weight_tightness * quality, 2),
                    max=cfg.weight_tightness,
                    detail=(
                        f"{consolidation.length_days}거래일 컨솔, 깊이 "
                        f"{consolidation.depth_pct:.1f}% (이상 "
                        f"{cfg.ideal_consolidation_depth_pct:.0f}% 이내)"
                    ),
                )
            )

        # 2. 10일선 근접도 — Qullamaggie는 이동평균에 붙은 자리에서 산다
        ema_fast = ind.ema10
        if ema_fast:
            distance_pct = abs(ctx.price - ema_fast) / ema_fast * 100.0
            quality = _decay(distance_pct, cfg.ideal_ma_distance_pct, cfg.max_ma_distance_pct)
            components.append(
                ScoreComponent(
                    id="ma_proximity",
                    label="10일선 근접도",
                    earned=round(cfg.weight_ma_proximity * quality, 2),
                    max=cfg.weight_ma_proximity,
                    detail=f"10일선 {ema_fast:.2f}에서 {distance_pct:.1f}% 떨어져 있다",
                )
            )

        # 3. 피벗 근접도
        if consolidation is not None and consolidation.pivot_price > 0:
            to_pivot_pct = (consolidation.pivot_price - ctx.price) / ctx.price * 100.0
            closeness = max(
                0.0, (cfg.pivot_proximity_pct - abs(to_pivot_pct)) / cfg.pivot_proximity_pct
            )
            components.append(
                ScoreComponent(
                    id="pivot_proximity",
                    label="피벗 근접도",
                    earned=round(cfg.weight_pivot_proximity * min(1.0, closeness), 2),
                    max=cfg.weight_pivot_proximity,
                    detail=f"컨솔 상단 {consolidation.pivot_price:.2f}까지 {to_pivot_pct:+.2f}%",
                )
            )

        # 4. 직전 급등 강도 — 게이트를 넘긴 뒤 '얼마나 더 크게 올랐나'
        prior_move = consolidation.prior_move_pct if consolidation is not None else None
        if prior_move is not None:
            headroom = max(0.0, cfg.ideal_prior_move_pct - cfg.min_prior_move_pct)
            quality = (
                max(0.0, min(1.0, (prior_move - cfg.min_prior_move_pct) / headroom))
                if headroom
                else 0.0
            )
            components.append(
                ScoreComponent(
                    id="prior_move",
                    label="직전 급등 강도",
                    earned=round(cfg.weight_prior_move * quality, 2),
                    max=cfg.weight_prior_move,
                    detail=(
                        f"컨솔 직전 {prior_move:+.0f}% 상승 "
                        f"(이상 {cfg.ideal_prior_move_pct:.0f}%)"
                    ),
                )
            )

        # 5. 거래량 건조 — 미완성 봉이면 채점하지 않는다 (0점이 아니라 만점에서 제외)
        dryup = consolidation.volume_dryup_ratio if consolidation is not None else None
        if ctx.volume_reliable and dryup is not None:
            quality = _decay(dryup, cfg.ideal_volume_dryup_ratio, 1.0)
            components.append(
                ScoreComponent(
                    id="volume_dryup",
                    label="거래량 건조",
                    earned=round(cfg.weight_volume_dryup * quality, 2),
                    max=cfg.weight_volume_dryup,
                    detail=f"컨솔 후반 거래량이 컨솔 평균의 {dryup:.2f}배",
                )
            )

        score = round(sum(c.earned for c in components), 2)
        return score, sum(c.max for c in components), components

    # -----------------------------------------------------------------
    # SETUP — 채점이 아니므로 게이트 탈락 종목에도 수행한다
    # -----------------------------------------------------------------

    def detect_setup(self, ctx: StockContext) -> SetupState:
        cfg = self.config
        consolidation = detect_consolidation(ctx, cfg)
        if consolidation is None or consolidation.pivot_price <= 0:
            return SetupState.NO_SETUP

        price, pivot = ctx.price, consolidation.pivot_price
        above_pct = (price - pivot) / pivot * 100.0

        if above_pct > cfg.extended_pct_above_pivot:
            return SetupState.EXTENDED
        if above_pct > 0.0:
            return SetupState.BREAKOUT

        recent = ctx.ohlcv["close"].iloc[-cfg.swing_fractal_k * 2 :]
        if len(recent) and float(recent.max()) > pivot:
            return SetupState.FAILED_BREAKOUT

        if abs(above_pct) <= cfg.pivot_proximity_pct:
            return SetupState.PIVOT_READY
        if consolidation.depth_pct <= cfg.max_consolidation_depth_pct:
            return SetupState.CONTRACTING
        return SetupState.BASE_FORMING

    def build_setup_metrics(self, ctx: StockContext) -> SetupMetrics:
        consolidation = detect_consolidation(ctx, self.config)
        ema_fast = ctx.indicators.ema10
        ma_distance_pct = (
            round((ctx.price - ema_fast) / ema_fast * 100.0, 4) if ema_fast else None
        )

        if consolidation is None:
            return SetupMetrics(detail=QullamaggieSetup(ma_distance_pct=ma_distance_pct))

        price, pivot = ctx.price, consolidation.pivot_price
        return SetupMetrics(
            pivot_price=round(pivot, 4),
            to_pivot_ratio=round(price / pivot, 6) if pivot else None,
            to_pivot_pct=round((pivot - price) / price * 100.0, 4) if price else None,
            base_length_days=consolidation.length_days,
            base_depth_pct=round(consolidation.depth_pct, 4),
            detail=QullamaggieSetup(
                prior_move_pct=(
                    round(consolidation.prior_move_pct, 4)
                    if consolidation.prior_move_pct is not None
                    else None
                ),
                ma_distance_pct=ma_distance_pct,
                volume_dryup_ratio=(
                    round(consolidation.volume_dryup_ratio, 4)
                    if consolidation.volume_dryup_ratio is not None
                    else None
                ),
            ),
        )

    # -----------------------------------------------------------------
    # DECIDE
    # -----------------------------------------------------------------

    def decide(
        self,
        ctx: StockContext,
        score: float,
        max_score: float,
        components: list[ScoreComponent],
        setup: SetupState,
    ) -> tuple[Verdict, list[str]]:
        cfg = self.config
        notes: list[str] = ["급등·변동성·유동성·RS 자격 요건 통과"]
        score_pct = 100.0 * score / max_score if max_score else 0.0

        if not ctx.volume_reliable:
            notes.append(
                "미완성 봉 — 거래량 채점 항목을 만점에서 제외했다 "
                f"(만점 {max_score:.0f}). 종가 확정 후 재평가 필요"
            )

        if setup is SetupState.EXTENDED:
            return Verdict.AVOID, [
                *notes,
                f"컨솔 상단 대비 +{cfg.extended_pct_above_pivot:.0f}% 이상 확장 — "
                "손절 자리가 멀어져 리스크·리워드가 무너진다",
            ]
        if setup is SetupState.FAILED_BREAKOUT:
            return Verdict.AVOID, [*notes, "컨솔 상단 돌파 후 되돌림 — 실패한 돌파"]
        if setup is SetupState.NO_SETUP:
            return Verdict.WATCH, [*notes, "컨솔리데이션이 없다 — 진입 지점이 없다"]

        if setup in (SetupState.PIVOT_READY, SetupState.BREAKOUT):
            if not ctx.volume_reliable:
                return Verdict.WATCH, [
                    *notes,
                    "거래량 확인 없이 BUY를 내지 않는다 — 장 마감 후 재평가",
                ]
            if score_pct >= cfg.buy_min_score_pct:
                return Verdict.BUY, [
                    *notes,
                    f"{setup.value} — 타이밍 점수 {score:.1f}/{max_score:.0f} "
                    f"({score_pct:.0f}%)로 기준 {cfg.buy_min_score_pct:.0f}% 이상",
                ]
            return Verdict.WATCH, [
                *notes,
                f"셋업은 갖췄으나 타이밍 점수 {score_pct:.0f}%가 "
                f"기준 {cfg.buy_min_score_pct:.0f}% 미만",
            ]

        return Verdict.WATCH, [*notes, f"{setup.value} — 컨솔 상단까지 거리가 남았다"]
