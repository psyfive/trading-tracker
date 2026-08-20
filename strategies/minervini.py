"""미너비니 SEPA — 추세 템플릿(GATE) + VCP 타이밍(SCORE).

## 이 파일의 구조가 곧 프로젝트의 핵심 설계다

GATE(추세 템플릿 8조건)는 **이진 필터**다. 통과하지 못하면 점수를 계산하지 않는다.
"200일선 위" 같은 추세 조건을 가산점으로 옮기면, 하락 추세 종목이 다른 항목에서
점수를 벌어 상위에 올라온다. 그 순간 이 시스템은 추세추종 진단기가 아니게 된다.

SCORE는 게이트를 통과한 종목의 **진입 타이밍 품질**만 잰다.

## 지표가 None이면 UNAVAILABLE이다

상장 6개월 종목의 sma200, 유니버스 없는 rs_percentile은 '조건 미달'이 아니라
'확인 못 함'이다. FAIL로 처리하면 신규 상장주가 조용히 탈락하고 사용자는 이유를 모른다.
`build_gate_check`가 shortfall_pct를, `build_gate_result`가 UNAVAILABLE 정책을 담당한다.

## 돌파 거래량과 베이스 깊이는 BUY의 **필요조건**이다 (점수가 아니다)

미너비니는 거래량 없는 돌파를 사지 않고, 지나치게 깊은 베이스를 유효한 베이스로 보지
않는다. 두 조건은 가산점으로 두면 다른 항목의 점수로 상쇄되므로 `decide()`의 이진
조건으로 둔다. 게이트에 넣지 않는 이유는 게이트가 **추세 템플릿**이기 때문이다 —
셋업(베이스·돌파)에 관한 조건은 게이트 통과 이후의 타이밍 판단에 속한다.

돌파 거래량은 BREAKOUT 상태에서만 확인 가능하다. PIVOT_READY는 아직 돌파가 없어
확인할 대상 자체가 없으므로 notes로 "돌파 시 거래량 확인 필요"를 남긴다.

## 베이스 탐지 창은 65봉이다 (구조적 한계)

`base_lookback_days=65` 창 안에서만 앵커를 찾으므로 **수개월짜리 베이스는 표현되지
않는다**. 또 앵커 탐색이 실패하는 경로가 없어서 봉이 충분하면 detect_base()는 항상
무언가를 반환한다 — 강한 단조 상승 구간에서도 '짧은 베이스'가 잡힌다. 따라서
BASE_FORMING은 '베이스가 있다'가 아니라 '최근 65봉 안에 눌림 구조가 있다'로 읽어야
한다. 깊이 상한(max_base_depth_pct)이 그중 명백히 무효한 것을 BUY에서 걸러낸다.

## 미완성 봉에서는 거래량 항목을 채점하지 않는다

장중 실행 시 당일 누적 거래량으로 '거래량 건조'를 재면 항상 건조해 보인다.
해당 항목을 0점 처리하는 것도 틀렸다 — 만점에서 제외한다 (max_score가 줄어든다).

## 결정론

같은 StockContext에는 항상 같은 판정을 낸다. 상태를 들고 있지 않으며 모든 계산이
ctx.ohlcv에서만 유도된다. look-ahead 감사가 같은 시점을 두 번 평가해 비교하므로
이 성질이 깨지면 감사가 무력화된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import MinerviniConfig
from core.context import StockContext
from core.types import (
    CheckStatus,
    Comparator,
    GateResult,
    MinerviniSetup,
    ScoreComponent,
    SetupMetrics,
    SetupState,
    Verdict,
)
from indicators.core import swing_positions
from strategies.base import (
    StrategyBase,
    breakout_volume_ratio,
    build_gate_check,
    build_gate_result,
    memoize_per_context,
    scaled_score,
)


@dataclass(frozen=True)
class BaseStructure:
    """탐지된 베이스. 전부 t 이하 봉에서만 유도된다.

    pivot_price는 **미너비니의 피벗**이다 — 마지막 수축의 고점(스윙 고점).
    base_high(베이스 최고가)와 구분된다: 베이스 최고가는 깊이 계산용이고,
    피벗은 돌파 트리거다. Qullamaggie의 컨솔 상단과도 정의가 다르므로
    IndicatorSnapshot이 아니라 SetupMetrics에 실린다.
    """

    start_position: int
    length_days: int
    pivot_price: float
    base_high: float
    low_price: float
    depth_pct: float
    contraction_count: int
    contraction_ratio: float | None
    base_avg_volume: float | None
    recent_avg_volume: float | None
    breakout_volume_ratio: float | None

    @property
    def volume_dryup_ratio(self) -> float | None:
        if not self.base_avg_volume or self.recent_avg_volume is None:
            return None
        return self.recent_avg_volume / self.base_avg_volume


def _swing_points(highs: pd.Series, lows: pd.Series, k: int) -> tuple[list[int], list[int]]:
    """프랙탈 스윙 고점/저점의 위치 목록.

    정의는 `indicators/core.py`에 있다 — 계산식이 하나뿐이고 전략 3종이 각자 다른 k로
    같은 정의를 쓰므로 지표다. 여기서는 위치 목록 형태로만 받아 온다.
    """
    return (
        swing_positions(highs, k, is_high=True),
        swing_positions(lows, k, is_high=False),
    )


def _contractions(
    highs: pd.Series, lows: pd.Series, swing_highs: list[int], swing_lows: list[int]
) -> list[float]:
    """베이스 내 되돌림 폭(%)의 순서 목록.

    스윙 고점 -> 그 뒤 첫 스윙 저점까지의 하락률. VCP는 이 값들이 점점 작아지는 패턴이다.
    """
    high_values, low_values = highs.to_numpy(), lows.to_numpy()
    depths: list[float] = []
    for high_index in swing_highs:
        following = [j for j in swing_lows if j > high_index]
        if not following:
            continue
        low_index = following[0]
        peak = high_values[high_index]
        if peak <= 0:
            continue
        depths.append((peak - low_values[low_index]) / peak * 100.0)
    return depths


def detect_base(ctx: StockContext, config: MinerviniConfig) -> BaseStructure | None:
    """확립된 고점을 앵커로 베이스를 잡는다. 데이터가 모자라면 None.

    앵커 후보를 **최근 min_base_length_days 봉에서 제외**하는 것이 핵심이다.
    최근 구간까지 포함해 최고가를 찾으면, 종목이 신고가로 돌파하는 순간 앵커가
    오늘 봉으로 점프해 베이스가 길이 1로 붕괴하고 NO_SETUP이 된다 —
    즉 돌파를 탐지해야 할 바로 그 시점에 베이스가 사라진다.

    앵커를 과거로 고정하면 베이스 길이가 구조적으로 min_base_length_days 이상이 되고,
    오늘 돌파해도 베이스는 그대로 남는다.
    """
    df = ctx.ohlcv
    if len(df) < config.base_lookback_days:
        return None

    window = df.iloc[-config.base_lookback_days :]
    searchable = window.iloc[: -config.min_base_length_days]
    if len(searchable) == 0:
        return None

    anchor_offset = int(searchable["high"].to_numpy().argmax())
    base = window.iloc[anchor_offset:]

    # 베이스 최고가도 '확립된' 값이어야 한다 — 돌파 당일 고가로 깊이를 재면
    # 돌파할수록 베이스가 깊어 보이는 역전이 생긴다.
    established = base["high"].iloc[: -config.swing_fractal_k]
    base_high = float(established.max()) if len(established) else float(base["high"].max())
    low = float(base["low"].min())
    if base_high <= 0:
        return None

    swing_highs, swing_lows = _swing_points(base["high"], base["low"], config.swing_fractal_k)

    # 피벗은 베이스 최고가가 아니라 **마지막 수축의 고점**이다.
    # 베이스 최고가로 잡으면 오늘 봉의 고가가 포함되어 종가가 피벗을 넘는 것이
    # 수학적으로 불가능해지고, BREAKOUT/EXTENDED 판정이 영원히 나오지 않는다.
    # 스윙 고점은 프랙탈 정의상 최소 k봉 이전이므로 돌파 트리거로 쓸 수 있다.
    highs_array = base["high"].to_numpy()
    if swing_highs:
        pivot = float(highs_array[swing_highs[-1]])
    elif len(highs_array) > config.swing_fractal_k:
        pivot = float(highs_array[: -config.swing_fractal_k].max())
    else:
        return None

    depths = _contractions(base["high"], base["low"], swing_highs, swing_lows)

    recent_span = min(len(base), config.swing_fractal_k * 2)
    base_volume = float(base["volume"].mean()) if len(base) else None
    recent_volume = float(base["volume"].iloc[-recent_span:].mean()) if recent_span else None
    # 돌파 거래량은 50일 평균 대비다. 베이스 평균 대비로 재면 건조도(volume_dryup)와
    # 같은 기준을 두 번 쓰게 되어 '건조한 베이스일수록 돌파가 쉬워지는' 역전이 생긴다.
    breakout_volume = breakout_volume_ratio(
        base["volume"], ctx.indicators.avg_volume_50, recent_span
    )

    return BaseStructure(
        start_position=len(df) - len(base),
        length_days=len(base),
        pivot_price=pivot,
        base_high=base_high,
        low_price=low,
        depth_pct=(base_high - low) / base_high * 100.0,
        contraction_count=len(depths),
        contraction_ratio=(depths[-1] / depths[0] if len(depths) >= 2 and depths[0] > 0 else None),
        base_avg_volume=base_volume,
        recent_avg_volume=recent_volume,
        breakout_volume_ratio=breakout_volume,
    )


class MinerviniStrategy(StrategyBase):
    """SEPA 추세 템플릿 + VCP."""

    name = "minervini"
    version = "1.1.0"

    def __init__(self, config: MinerviniConfig) -> None:
        self.config = config

    @memoize_per_context
    def _base(self, ctx: StockContext) -> BaseStructure | None:
        """이 ctx의 베이스. evaluate() 한 번에 네 번 탐지하던 것을 한 번으로 줄인다."""
        return detect_base(ctx, self.config)

    # -----------------------------------------------------------------
    # GATE — 추세 템플릿 8조건. 전부 이진 필터다.
    # -----------------------------------------------------------------

    def build_gate(self, ctx: StockContext) -> GateResult:
        cfg = self.config
        ind = ctx.indicators
        price = ctx.price

        def numeric(
            check_id: str,
            label: str,
            actual: float | None,
            threshold: float | None,
            *,
            passed: bool | None,
            comparator: Comparator,
            unit: str | None,
            reason_pass: str,
            reason_fail: str,
            reason_missing: str,
        ):
            """threshold는 UNAVAILABLE일 때 None일 수 있다.

            기준값이 config 상수인 조건(기울기·52주·RS)은 확인 못 해도 기준이 그대로
            존재하므로 싣는다. 반면 기준값이 **파생값**인 조건(이동평균)은 산출되지
            않았으므로 실을 값이 없다 — 0.0으로 채우면 프론트가 "기준 > $0.00"을
            그리게 되고, 그것은 '계산 불가를 0.0으로 채우지 말 것' 위반이다.
            """
            if actual is None or passed is None:
                return build_gate_check(
                    id=check_id,
                    label=label,
                    status=CheckStatus.UNAVAILABLE,
                    threshold=threshold,
                    comparator=comparator,
                    unit=unit,
                    reason=reason_missing,
                )
            return build_gate_check(
                id=check_id,
                label=label,
                status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                actual=round(actual, 4),
                threshold=threshold,
                comparator=comparator,
                unit=unit,
                reason=reason_pass if passed else reason_fail,
            )

        checks = []

        # 1. 주가 > 150일선 & 200일선
        have = ind.sma150 is not None and ind.sma200 is not None
        checks.append(
            numeric(
                "price_above_sma150_200",
                "주가 > 150일선 & 200일선",
                price if have else None,
                max(ind.sma150, ind.sma200) if have else None,
                passed=(price > ind.sma150 and price > ind.sma200) if have else None,
                comparator=Comparator.GT,
                unit=None,
                reason_pass=(
                    f"주가 {price:.2f} > 150일선 {ind.sma150:.2f}, 200일선 {ind.sma200:.2f}"
                    if have
                    else ""
                ),
                reason_fail=(
                    f"주가 {price:.2f}가 150일선 {ind.sma150:.2f} 또는 "
                    f"200일선 {ind.sma200:.2f} 아래"
                    if have
                    else ""
                ),
                reason_missing="150일선 또는 200일선 미산출 — 상장 기간이 짧다",
            )
        )

        # 2. 150일선 > 200일선
        have = ind.sma150 is not None and ind.sma200 is not None
        checks.append(
            numeric(
                "sma150_above_sma200",
                "150일선 > 200일선",
                ind.sma150 if have else None,
                ind.sma200 if have else None,
                passed=(ind.sma150 > ind.sma200) if have else None,
                comparator=Comparator.GT,
                unit=None,
                reason_pass=f"150일선 {ind.sma150:.2f} > 200일선 {ind.sma200:.2f}" if have else "",
                reason_fail=f"150일선 {ind.sma150:.2f} <= 200일선 {ind.sma200:.2f}" if have else "",
                reason_missing="150일선 또는 200일선 미산출",
            )
        )

        # 3. 200일선 상승 중
        slope = ind.sma200_slope_20d_pct
        checks.append(
            numeric(
                "sma200_trending_up",
                "200일선 상승 중 (20일)",
                slope,
                cfg.sma200_slope_min_pct,
                passed=(slope > cfg.sma200_slope_min_pct) if slope is not None else None,
                comparator=Comparator.GT,
                unit="%",
                reason_pass=f"200일선 20일 기울기 {slope:+.2f}%" if slope is not None else "",
                reason_fail=(
                    f"200일선 20일 기울기 {slope:+.2f}% — 상승 중이 아니다"
                    if slope is not None
                    else ""
                ),
                reason_missing="200일선 기울기 미산출 — 220봉 이상 필요",
            )
        )

        # 4. 50일선 > 150일선 & 200일선
        have = ind.sma50 is not None and ind.sma150 is not None and ind.sma200 is not None
        checks.append(
            numeric(
                "sma50_above_sma150_200",
                "50일선 > 150일선 & 200일선",
                ind.sma50 if have else None,
                max(ind.sma150, ind.sma200) if have else None,
                passed=(ind.sma50 > ind.sma150 and ind.sma50 > ind.sma200) if have else None,
                comparator=Comparator.GT,
                unit=None,
                reason_pass=(
                    f"50일선 {ind.sma50:.2f} > 150일선 {ind.sma150:.2f}, "
                    f"200일선 {ind.sma200:.2f}"
                    if have
                    else ""
                ),
                reason_fail=(
                    f"50일선 {ind.sma50:.2f}이 150일선 {ind.sma150:.2f} 또는 "
                    f"200일선 {ind.sma200:.2f} 아래"
                    if have
                    else ""
                ),
                reason_missing="50/150/200일선 중 미산출 항목이 있다",
            )
        )

        # 5. 주가 > 50일선
        checks.append(
            numeric(
                "price_above_sma50",
                "주가 > 50일선",
                price if ind.sma50 is not None else None,
                ind.sma50,
                passed=(price > ind.sma50) if ind.sma50 is not None else None,
                comparator=Comparator.GT,
                unit=None,
                reason_pass=f"주가 {price:.2f} > 50일선 {ind.sma50:.2f}"
                if ind.sma50 is not None
                else "",
                reason_fail=f"주가 {price:.2f} <= 50일선 {ind.sma50:.2f}"
                if ind.sma50 is not None
                else "",
                reason_missing="50일선 미산출",
            )
        )

        # 6. 52주 저점 대비 +30% 이상
        above_low = ind.above_52w_low_pct
        checks.append(
            numeric(
                "above_52w_low",
                f"52주 저점 대비 +{cfg.min_pct_above_52w_low:.0f}% 이상",
                above_low,
                cfg.min_pct_above_52w_low,
                passed=(above_low >= cfg.min_pct_above_52w_low) if above_low is not None else None,
                comparator=Comparator.GTE,
                unit="%",
                reason_pass=f"52주 저점 대비 {above_low:+.1f}%" if above_low is not None else "",
                reason_fail=(
                    f"52주 저점 대비 {above_low:+.1f}% — 기준 {cfg.min_pct_above_52w_low:.0f}%에 "
                    f"{cfg.min_pct_above_52w_low - above_low:.1f}%p 미달"
                    if above_low is not None
                    else ""
                ),
                reason_missing="52주 저점 미산출 — 252봉 이상 필요",
            )
        )

        # 7. 52주 고점 대비 -25% 이내
        from_high = ind.from_52w_high_pct
        floor = -cfg.max_pct_below_52w_high
        checks.append(
            numeric(
                "near_52w_high",
                f"52주 고점 대비 -{cfg.max_pct_below_52w_high:.0f}% 이내",
                from_high,
                floor,
                passed=(from_high >= floor) if from_high is not None else None,
                comparator=Comparator.GTE,
                unit="%",
                reason_pass=f"52주 고점 대비 {from_high:+.1f}%" if from_high is not None else "",
                reason_fail=(
                    f"52주 고점 대비 {from_high:+.1f}% — 기준 {floor:.0f}%보다 낮다"
                    if from_high is not None
                    else ""
                ),
                reason_missing="52주 고점 미산출 — 252봉 이상 필요",
            )
        )

        # 8. RS 백분위
        rs = ind.rs_percentile
        checks.append(
            numeric(
                "rs_percentile",
                f"RS 백분위 {cfg.min_rs_percentile:.0f} 이상",
                rs,
                cfg.min_rs_percentile,
                passed=(rs >= cfg.min_rs_percentile) if rs is not None else None,
                comparator=Comparator.GTE,
                unit=None,
                reason_pass=f"RS 백분위 {rs:.1f}" if rs is not None else "",
                reason_fail=(
                    f"RS 백분위 {rs:.1f} — 기준 {cfg.min_rs_percentile:.0f}에 "
                    f"{cfg.min_rs_percentile - rs:.1f} 미달"
                    if rs is not None
                    else ""
                ),
                reason_missing="RS 백분위 미산출 — 유니버스 또는 벤치마크 데이터가 없다",
            )
        )

        return build_gate_result(self.name, checks)

    # -----------------------------------------------------------------
    # SCORE — 게이트 통과 종목의 진입 타이밍 품질만
    # -----------------------------------------------------------------

    def build_score(self, ctx: StockContext) -> tuple[float, float, list[ScoreComponent]]:
        cfg = self.config
        base = self._base(ctx)
        components: list[ScoreComponent] = []

        # 1. VCP 수축 품질
        if base is not None and base.contraction_ratio is not None:
            quality = scaled_score(base.contraction_ratio, cfg.ideal_contraction_ratio,
                              lower_is_better=True)
            components.append(
                ScoreComponent(
                    id="vcp_contraction",
                    label="VCP 수축 품질",
                    earned=round(cfg.weight_contraction * quality, 2),
                    max=cfg.weight_contraction,
                    detail=(
                        f"{base.contraction_count}회 수축, 마지막 수축폭이 첫 수축폭의 "
                        f"{base.contraction_ratio:.2f}배"
                    ),
                )
            )

        # 2. 거래량 건조 — 미완성 봉이면 채점하지 않는다 (0점이 아니라 만점에서 제외)
        dryup = base.volume_dryup_ratio if base is not None else None
        if ctx.volume_reliable and dryup is not None:
            quality = scaled_score(dryup, cfg.ideal_volume_dryup_ratio, lower_is_better=True)
            components.append(
                ScoreComponent(
                    id="volume_dryup",
                    label="거래량 건조",
                    earned=round(cfg.weight_volume_dryup * quality, 2),
                    max=cfg.weight_volume_dryup,
                    detail=f"최근 평균 거래량이 베이스 평균의 {dryup:.2f}배",
                )
            )

        # 3. 피벗 근접도
        if base is not None and base.pivot_price > 0:
            to_pivot = (base.pivot_price - ctx.price) / ctx.price * 100.0
            closeness = scaled_score(
                max(0.0, cfg.pivot_proximity_pct - abs(to_pivot)) / cfg.pivot_proximity_pct,
                1.0,
                lower_is_better=False,
            )
            components.append(
                ScoreComponent(
                    id="pivot_proximity",
                    label="피벗 근접도",
                    earned=round(cfg.weight_pivot_proximity * closeness, 2),
                    max=cfg.weight_pivot_proximity,
                    detail=f"피벗 {base.pivot_price:.2f}까지 {to_pivot:+.2f}%",
                )
            )

        # 4. 베이스 성숙도
        if base is not None:
            maturity = scaled_score(
                base.length_days / cfg.ideal_base_length_days, 1.0, lower_is_better=False
            )
            components.append(
                ScoreComponent(
                    id="base_length",
                    label="베이스 성숙도",
                    earned=round(cfg.weight_base_length * maturity, 2),
                    max=cfg.weight_base_length,
                    detail=f"{base.length_days}거래일 베이스, 깊이 {base.depth_pct:.1f}%",
                )
            )

        # 5. 상대강도 — 게이트를 넘긴 뒤 '얼마나 더 강한가'
        rs = ctx.indicators.rs_percentile
        if rs is not None:
            headroom = max(0.0, 100.0 - cfg.min_rs_percentile)
            strength = scaled_score(
                (rs - cfg.min_rs_percentile) / headroom if headroom else 0.0,
                1.0,
                lower_is_better=False,
            )
            components.append(
                ScoreComponent(
                    id="rs_strength",
                    label="상대강도",
                    earned=round(cfg.weight_rs_strength * strength, 2),
                    max=cfg.weight_rs_strength,
                    detail=f"RS 백분위 {rs:.1f} (기준 {cfg.min_rs_percentile:.0f})",
                )
            )

        score = round(sum(c.earned for c in components), 2)
        max_score = sum(c.max for c in components)
        return score, max_score, components

    # -----------------------------------------------------------------
    # SETUP — 채점이 아니므로 게이트 탈락 종목에도 수행한다
    # -----------------------------------------------------------------

    def detect_setup(self, ctx: StockContext) -> SetupState:
        cfg = self.config
        base = self._base(ctx)
        if base is None:
            return SetupState.NO_SETUP

        price, pivot = ctx.price, base.pivot_price
        if pivot <= 0:
            return SetupState.NO_SETUP

        above_pivot_pct = (price - pivot) / pivot * 100.0
        if above_pivot_pct > cfg.extended_pct_above_pivot:
            return SetupState.EXTENDED
        if above_pivot_pct > 0.0:
            return SetupState.BREAKOUT

        # 최근에 피벗을 넘었다가 되돌아왔으면 돌파 실패다.
        recent = ctx.ohlcv["close"].iloc[-cfg.swing_fractal_k * 2 :]
        if len(recent) and float(recent.max()) > pivot:
            return SetupState.FAILED_BREAKOUT

        if abs(above_pivot_pct) <= cfg.pivot_proximity_pct:
            return SetupState.PIVOT_READY
        if (
            base.contraction_count >= cfg.min_contractions
            and base.contraction_ratio is not None
            and base.contraction_ratio < 1.0
        ):
            return SetupState.CONTRACTING
        return SetupState.BASE_FORMING

    def build_setup_metrics(self, ctx: StockContext) -> SetupMetrics:
        base = self._base(ctx)
        if base is None:
            return SetupMetrics()

        price = ctx.price
        return SetupMetrics(
            pivot_price=round(base.pivot_price, 4),
            to_pivot_ratio=round(price / base.pivot_price, 6) if base.pivot_price else None,
            to_pivot_pct=round((base.pivot_price - price) / price * 100.0, 4) if price else None,
            base_length_days=base.length_days,
            base_depth_pct=round(base.depth_pct, 4),
            detail=MinerviniSetup(
                contraction_count=base.contraction_count,
                contraction_ratio=(
                    round(base.contraction_ratio, 4)
                    if base.contraction_ratio is not None
                    else None
                ),
                volume_dryup_ratio=(
                    round(base.volume_dryup_ratio, 4)
                    if base.volume_dryup_ratio is not None
                    else None
                ),
                breakout_volume_ratio=(
                    round(base.breakout_volume_ratio, 4)
                    if base.breakout_volume_ratio is not None
                    else None
                ),
            ),
        )

    # -----------------------------------------------------------------
    # DECIDE
    # -----------------------------------------------------------------

    def _breakout_volume_note(self, base: BaseStructure | None) -> tuple[bool, str]:
        """돌파 거래량 확인 결과와 그 근거 한 줄. 반환: (확인됨, 메모).

        비율을 낼 수 없으면(50일 평균 미산출) **확인 실패**로 본다. 확인 못 한 조건을
        충족으로 치는 것은 게이트의 UNAVAILABLE 정책과 반대 방향이다.
        """
        required = self.config.breakout_volume_ratio
        ratio = base.breakout_volume_ratio if base is not None else None
        if ratio is None:
            return False, (
                "돌파 거래량을 확인할 수 없다 (50일 평균 거래량 미산출) — "
                "확인되지 않은 돌파는 사지 않는다"
            )
        if ratio < required:
            return False, (
                f"돌파 거래량이 50일 평균의 {ratio:.2f}배 — 기준 {required:.1f}배 미달. "
                "거래량 없는 돌파는 사지 않는다"
            )
        return True, f"돌파 거래량 {ratio:.2f}배 (기준 {required:.1f}배 이상) 확인"

    def decide(
        self,
        ctx: StockContext,
        score: float,
        max_score: float,
        components: list[ScoreComponent],
        setup: SetupState,
    ) -> tuple[Verdict, list[str]]:
        cfg = self.config
        notes: list[str] = ["추세 템플릿 8개 조건 전부 통과"]
        score_pct = 100.0 * score / max_score if max_score else 0.0

        if not ctx.volume_reliable:
            notes.append(
                "미완성 봉 — 거래량 채점 항목을 만점에서 제외했다 "
                f"(만점 {max_score:.0f}). 종가 확정 후 재평가 필요"
            )

        if setup is SetupState.EXTENDED:
            return Verdict.AVOID, [
                *notes,
                f"피벗 대비 +{cfg.extended_pct_above_pivot:.0f}% 이상 확장 — 추격 매수 금지",
            ]
        if setup is SetupState.FAILED_BREAKOUT:
            return Verdict.AVOID, [*notes, "돌파 후 피벗 아래로 되돌림 — 실패한 돌파"]
        if setup is SetupState.NO_SETUP:
            return Verdict.WATCH, [*notes, "베이스가 형성되지 않았다 — 진입 지점이 없다"]

        base = self._base(ctx)

        # 베이스 깊이 상한 — 깊은 베이스는 돌파해도 사지 않는다 (실패 확률 필터).
        if base is not None and base.depth_pct > cfg.max_base_depth_pct:
            return Verdict.WATCH, [
                *notes,
                f"베이스 깊이 {base.depth_pct:.1f}%가 상한 {cfg.max_base_depth_pct:.0f}%를 "
                "넘는다 — 미너비니가 유효한 베이스로 보지 않는 구조다",
            ]

        if setup in (SetupState.PIVOT_READY, SetupState.BREAKOUT):
            if not ctx.volume_reliable:
                return Verdict.WATCH, [
                    *notes,
                    "거래량 확인 없이 BUY를 내지 않는다 — 장 마감 후 재평가",
                ]

            # 진입 정의: 돌파를 확인하고 살 것인가, 피벗 근접에서 미리 살 것인가.
            # 원전은 확인 후 진입이지만 어느 쪽이 나은지는 재서 정할 문제다 (config).
            if setup is SetupState.PIVOT_READY and cfg.require_breakout_for_buy:
                return Verdict.WATCH, [
                    *notes,
                    f"피벗 {base.pivot_price:.2f} 돌파 전이다 — 돌파 확인 후에만 진입한다",
                ]

            # 돌파 거래량 — 돌파가 일어난 뒤에만 확인 가능한 조건이다.
            if setup is SetupState.BREAKOUT:
                confirmed, volume_note = self._breakout_volume_note(base)
                if not confirmed:
                    return Verdict.WATCH, [*notes, volume_note]
                notes.append(volume_note)
            else:
                notes.append(
                    f"돌파 전이다 — 돌파 시 거래량이 50일 평균의 "
                    f"{cfg.breakout_volume_ratio:.1f}배 이상인지 확인해야 한다"
                )

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

        return Verdict.WATCH, [*notes, f"{setup.value} — 피벗까지 거리가 남았다"]
