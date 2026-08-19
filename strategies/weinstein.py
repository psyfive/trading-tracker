"""와인스타인 Stage Analysis — Stage 2 국면(GATE) + 거래범위 돌파 타이밍(SCORE).

## 미너비니와 무엇이 다른가

같은 추세추종이지만 **자를 대는 지점이 다르다.**

- 미너비니: 이동평균 8개 조건이 정렬됐는가 (추세가 이미 확립됐는가)
- 와인스타인: 지금이 **Stage 2인가**, 그리고 그 Stage 2가 **얼마나 초기인가**

그래서 게이트도 다르게 짠다. Stage 2 판정 자체가 '30주선 위 + 30주선 상승'을
함의하므로 그 둘을 별도 조건으로 다시 세면 게이트가 같은 사실을 세 번 세는 셈이 되고,
`pass_count/total` 진행률이 부풀어 워치리스트 근접도 정렬이 망가진다.
Stage 2가 함의하지 않는 것만 남겼다: 10주선, 시장 국면, RS, 유동성.

## 시장 국면이 게이트에 있는 이유

와인스타인은 "시장이 Stage 4면 개별 종목이 아무리 좋아도 사지 않는다"고 못 박는다.
그래서 이 전략에서 regime은 **이진 필터**다. 미너비니는 국면을 게이트에 넣지 않으므로
같은 종목·같은 날 두 전략의 판정이 갈릴 수 있고, 그것이 이 프로젝트가 판정을
합치지 않고 나란히 두는 이유다.

국면으로 **점수를 깎지는 않는다**. 통과 여부만 가른다.

## Stage 신선도는 전략 고유 계산이다

게이트가 쓰는 Stage는 리포트 최상위에 실리는 것과 **같은 값**이다 (호출부가 주입한다).
사용자가 화면에서 보는 Stage와 게이트 판정이 어긋나면 안 되기 때문이다.

반면 '몇 일째 Stage 2인가'는 계약에 없고 방법론마다 정의가 갈리므로 여기서 직접 센다
(30주선 위 + 30주선 상승이 이어진 연속 일수). 이 값은 워밍업 구간에서 잘리고
max_stage2_age_days에서도 잘리므로 **"최소 N일째"**로 읽어야 한다.
조건식과 파라미터는 regime/market.py의 Stage 판정과 같으며 AppConfig가 그 일치를 강제한다.

## 돌파 거래량은 BUY의 필요조건이다 (점수만이 아니다)

와인스타인의 돌파는 거래량이 조건이다. 채점 항목으로만 두면 거래량 0점이어도 신선도·
이격도·RS로 기준 점수를 넘겨 BUY가 나온다 — 문구와 로직이 어긋나는 상태였다.
그래서 BREAKOUT 상태에서는 `volume_confirm_ratio`를 **이진 조건**으로 요구한다.
게이트가 아니라 decide에 두는 이유는 게이트가 '국면' 필터이고 돌파 거래량은
셋업·타이밍의 문제이기 때문이다.

## 결정론

상태를 들고 있지 않으며 모든 계산이 ctx에서만 유도된다. look-ahead 감사의 전제다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import WeinsteinConfig
from core.context import StockContext
from core.types import (
    CheckStatus,
    Comparator,
    GateResult,
    MarketRegime,
    ScoreComponent,
    SetupMetrics,
    SetupState,
    Stage,
    Verdict,
    WeinsteinSetup,
)
from indicators.core import slope_pct, sma, swing_positions
from strategies.base import (
    StrategyBase,
    build_gate_check,
    build_gate_result,
    decay_score,
    memoize_per_context,
)
from strategies.base import (
    breakout_volume_ratio as recent_volume_ratio,
)


@dataclass(frozen=True)
class TradingRange:
    """탐지된 거래범위(Stage 1 베이스). 전부 t 이하 봉에서만 유도된다.

    resistance는 **와인스타인의 피벗**이다 — 거래범위 상단(마지막 스윙 고점).
    미너비니의 '마지막 수축 고점'과 같은 방식으로 확정되지만 창 길이와 앵커 규칙이
    달라 값이 다르다. 그래서 IndicatorSnapshot이 아니라 SetupMetrics에 실린다.
    """

    start_position: int
    length_days: int
    resistance: float
    range_high: float
    low_price: float
    depth_pct: float
    breakout_volume_ratio: float | None


def stage2_age_days(ctx: StockContext, config: WeinsteinConfig) -> int | None:
    """30주선 위 + 30주선 상승 상태가 이어진 연속 일수. 근거가 없으면 None.

    와인스타인은 Stage 2의 **초기**를 산다. 이미 1년째 Stage 2인 종목은 같은 조건을
    만족해도 남은 상승 여력이 다르다고 본다.

    조건식은 `regime.market._stage_of()`의 STAGE_2 판정과 **같은 식**이다
    (종가 > MA and 기울기 > 평탄 기준). 파라미터도 같아야 하며 AppConfig가 그것을
    강제한다 — 다르면 화면에 뜨는 Stage와 여기서 세는 Stage 2가 갈라진다.

    두 방향으로 잘린 값이라는 점이 중요하다:
      - 아래로: 이동평균 워밍업 구간은 조건이 False로 떨어지므로 관측 가능한 **하한**이다.
      - 위로: max_stage2_age_days에서 자른다. 신선도 채점이 그 이상을 구분하지 않으므로
        점수에는 손실이 없고, 대신 **매 평가마다 전체 히스토리를 다시 계산하지 않는다**
        (계산 창이 상수로 묶여 백테스트가 O(n^2)로 되돌아가지 않는다).
        따라서 계약에 실리는 값은 "최소 N일째"로 읽어야 한다.
    """
    # 필요한 창: 마지막 (cap+1)봉의 기울기를 얻으려면 그 앞에 이동평균 워밍업(ma_period)과
    # 기울기 lookback이 더 있어야 한다.
    needed = config.ma_period_daily + config.slope_lookback + config.max_stage2_age_days + 1
    close = ctx.ohlcv["close"].iloc[-needed:]
    ma = sma(close, config.ma_period_daily)
    slope = slope_pct(ma, config.slope_lookback)

    if pd.isna(ma.iloc[-1]) or pd.isna(slope.iloc[-1]):
        return None

    in_stage2 = ((close > ma) & (slope > config.stage_flat_slope_pct)).to_numpy()
    age = 0
    for flag in in_stage2[::-1]:
        if not flag or age >= config.max_stage2_age_days:
            break
        age += 1
    return age


def detect_range(ctx: StockContext, config: WeinsteinConfig) -> TradingRange | None:
    """직전 거래범위와 그 상단(저항선)을 잡는다. 데이터가 모자라면 None.

    앵커 후보에서 최근 min_range_length_days 봉을 제외하는 것은 미너비니의 베이스
    탐지와 같은 이유다 — 신고가로 돌파하는 순간 앵커가 오늘 봉으로 점프하면
    거래범위가 소멸해, 돌파를 탐지해야 할 바로 그 시점에 셋업이 사라진다.
    """
    df = ctx.ohlcv
    if len(df) < config.range_lookback_days:
        return None

    window = df.iloc[-config.range_lookback_days :]
    searchable = window.iloc[: -config.min_range_length_days]
    if len(searchable) == 0:
        return None

    anchor_offset = int(searchable["high"].to_numpy().argmax())
    trading_range = window.iloc[anchor_offset:]

    # 상단도 '확립된' 값이어야 한다. 오늘 고가를 포함하면 돌파할수록 범위가
    # 높아져 영원히 돌파하지 못한다.
    established = trading_range["high"].iloc[: -config.swing_fractal_k]
    if len(established) == 0:
        return None

    range_high = float(established.max())
    low = float(trading_range["low"].min())
    if range_high <= 0:
        return None

    swings = swing_positions(trading_range["high"], config.swing_fractal_k, is_high=True)
    highs = trading_range["high"].to_numpy()
    resistance = float(highs[swings[-1]]) if swings else range_high

    # 돌파 거래량은 '오늘'이 아니라 최근 며칠 중 최대다. 돌파 봉이 며칠 전일 수 있고,
    # 그 며칠은 스윙 고점이 확정되는 데 걸리는 시간(k봉)과 같은 스케일이다.
    recent_span = min(len(trading_range), config.swing_fractal_k * 2)

    return TradingRange(
        start_position=len(df) - len(trading_range),
        length_days=len(trading_range),
        resistance=resistance,
        range_high=range_high,
        low_price=low,
        depth_pct=(range_high - low) / range_high * 100.0,
        breakout_volume_ratio=recent_volume_ratio(
            trading_range["volume"], ctx.indicators.avg_volume_50, recent_span
        ),
    )


class WeinsteinStrategy(StrategyBase):
    """Stage Analysis — Stage 2 국면 + 거래범위 돌파."""

    name = "weinstein"
    version = "1.1.0"

    def __init__(self, config: WeinsteinConfig) -> None:
        self.config = config

    @memoize_per_context
    def _range(self, ctx: StockContext) -> TradingRange | None:
        """이 ctx의 거래범위. evaluate() 한 번에 세 번 탐지하던 것을 한 번으로 줄인다."""
        return detect_range(ctx, self.config)

    @memoize_per_context
    def _stage2_age(self, ctx: StockContext) -> int | None:
        """이 ctx의 Stage 2 신선도. 채점과 셋업 수치가 같은 값을 두 번 계산하지 않는다."""
        return stage2_age_days(ctx, self.config)

    # -----------------------------------------------------------------
    # GATE — Stage 2가 함의하지 않는 조건만. 전부 이진 필터다.
    # -----------------------------------------------------------------

    def build_gate(self, ctx: StockContext) -> GateResult:
        cfg = self.config
        ind = ctx.indicators
        price = ctx.price
        checks = []

        # 1. Stage 2인가 — 와인스타인의 유일한 매수 구간
        stage = ctx.stage
        if stage is Stage.UNDEFINED:
            checks.append(
                build_gate_check(
                    id="stage_is_2",
                    label="Stage 2 (상승 추세)",
                    status=CheckStatus.UNAVAILABLE,
                    comparator=Comparator.BOOL,
                    reason="Stage 판정 불가 — 30주선 산출에 필요한 봉이 없거나 주입되지 않았다",
                )
            )
        else:
            passed = stage is Stage.STAGE_2
            checks.append(
                build_gate_check(
                    id="stage_is_2",
                    label="Stage 2 (상승 추세)",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    comparator=Comparator.BOOL,
                    reason=(
                        "Stage 2 — 30주선 위에서 30주선이 상승 중"
                        if passed
                        else f"{stage.value} — 와인스타인은 Stage 2에서만 매수한다"
                    ),
                )
            )

        # 2. 10주선 위 — Stage 2 안에서의 단기 이탈을 거른다 (Stage는 30주선만 본다)
        sma_short = ind.sma50
        if sma_short is None:
            checks.append(
                build_gate_check(
                    id="price_above_ma10w",
                    label="주가 > 10주선",
                    status=CheckStatus.UNAVAILABLE,
                    comparator=Comparator.GT,
                    reason=f"{cfg.ma_short_period_daily}일선 미산출 — 상장 기간이 짧다",
                )
            )
        else:
            passed = price > sma_short
            checks.append(
                build_gate_check(
                    id="price_above_ma10w",
                    label="주가 > 10주선",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    actual=round(price, 4),
                    threshold=round(sma_short, 4),
                    comparator=Comparator.GT,
                    reason=(
                        f"주가 {price:.2f} > 10주선 {sma_short:.2f}"
                        if passed
                        else f"주가 {price:.2f} <= 10주선 {sma_short:.2f} — 단기 추세가 꺾였다"
                    ),
                )
            )

        # 3. 시장 국면 — 와인스타인은 시장이 나쁘면 개별 종목을 사지 않는다
        risk_off = ctx.regime is MarketRegime.RISK_OFF
        checks.append(
            build_gate_check(
                id="market_not_risk_off",
                label="시장 국면이 RISK_OFF가 아님",
                status=CheckStatus.FAIL if risk_off else CheckStatus.PASS,
                comparator=Comparator.BOOL,
                reason=(
                    "시장이 RISK_OFF — 지수가 200일선 아래다. 개별 조건과 무관하게 진입하지 않는다"
                    if risk_off
                    else f"시장 국면 {ctx.regime.value}"
                ),
            )
        )

        # 4. RS 백분위 — 시장보다 강한 종목만
        rs = ind.rs_percentile
        if rs is None:
            checks.append(
                build_gate_check(
                    id="rs_percentile",
                    label=f"RS 백분위 {cfg.min_rs_percentile:.0f} 이상",
                    status=CheckStatus.UNAVAILABLE,
                    threshold=cfg.min_rs_percentile,
                    reason="RS 백분위 미산출 — 유니버스 데이터가 없다",
                )
            )
        else:
            passed = rs >= cfg.min_rs_percentile
            checks.append(
                build_gate_check(
                    id="rs_percentile",
                    label=f"RS 백분위 {cfg.min_rs_percentile:.0f} 이상",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    actual=round(rs, 4),
                    threshold=cfg.min_rs_percentile,
                    reason=(
                        f"RS 백분위 {rs:.1f}"
                        if passed
                        else f"RS 백분위 {rs:.1f} — 기준 {cfg.min_rs_percentile:.0f}에 "
                        f"{cfg.min_rs_percentile - rs:.1f} 미달"
                    ),
                )
            )

        # 5. 유동성 — 50일 평균 거래대금.
        #    미완성 봉 1개는 50일 평균을 흔들지 못하므로 장중에도 판정한다
        #    (UNAVAILABLE 규약의 대상은 '돌파 거래량·볼륨 드라이업'처럼 당일 봉에
        #     직접 의존하는 조건이다).
        dollar_volume = ind.dollar_volume_50
        if dollar_volume is None:
            checks.append(
                build_gate_check(
                    id="dollar_volume",
                    label="50일 평균 거래대금",
                    status=CheckStatus.UNAVAILABLE,
                    threshold=cfg.min_dollar_volume,
                    unit="$",
                    reason="50일 평균 거래대금 미산출 — 50봉 이상 필요",
                )
            )
        else:
            passed = dollar_volume >= cfg.min_dollar_volume
            checks.append(
                build_gate_check(
                    id="dollar_volume",
                    label="50일 평균 거래대금",
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    actual=round(dollar_volume, 2),
                    threshold=cfg.min_dollar_volume,
                    unit="$",
                    reason=(
                        f"50일 평균 거래대금 {dollar_volume:,.0f}"
                        if passed
                        else f"50일 평균 거래대금 {dollar_volume:,.0f} — "
                        f"기준 {cfg.min_dollar_volume:,.0f} 미달. 실행 가능성이 의심된다"
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
        trading_range = self._range(ctx)
        components: list[ScoreComponent] = []

        # 1. 돌파 거래량 — 미완성 봉이면 채점하지 않는다 (0점이 아니라 만점에서 제외)
        volume_ratio = trading_range.breakout_volume_ratio if trading_range else None
        if ctx.volume_reliable and volume_ratio is not None:
            quality = min(1.0, volume_ratio / cfg.volume_confirm_ratio)
            components.append(
                ScoreComponent(
                    id="breakout_volume",
                    label="돌파 거래량",
                    earned=round(cfg.weight_breakout_volume * quality, 2),
                    max=cfg.weight_breakout_volume,
                    detail=(
                        f"최근 최대 거래량이 50일 평균의 {volume_ratio:.2f}배 "
                        f"(기준 {cfg.volume_confirm_ratio:.1f}배)"
                    ),
                )
            )

        # 2. Stage 2 신선도 — 초기일수록 높다
        age = self._stage2_age(ctx)
        if age is not None:
            quality = decay_score(
                float(age), float(cfg.ideal_stage2_age_days), float(cfg.max_stage2_age_days)
            )
            components.append(
                ScoreComponent(
                    id="stage_freshness",
                    label="Stage 2 신선도",
                    earned=round(cfg.weight_stage_freshness * quality, 2),
                    max=cfg.weight_stage_freshness,
                    detail=(
                        f"Stage 2 조건이 {age}거래일째 유지 중 "
                        f"(이상 {cfg.ideal_stage2_age_days}일 이내)"
                    ),
                )
            )

        # 3. 30주선 이격도 — 멀수록 진입 리스크가 크다
        ma_long = ind.sma150
        if ma_long:
            distance_pct = (ctx.price - ma_long) / ma_long * 100.0
            quality = decay_score(
                max(0.0, distance_pct),
                cfg.ideal_distance_from_ma_pct,
                cfg.max_distance_from_ma_pct,
            )
            components.append(
                ScoreComponent(
                    id="ma_distance",
                    label="30주선 이격도",
                    earned=round(cfg.weight_ma_distance * quality, 2),
                    max=cfg.weight_ma_distance,
                    detail=f"30주선 {ma_long:.2f} 대비 {distance_pct:+.1f}%",
                )
            )

        # 4. 상대강도 — 게이트를 넘긴 뒤 '얼마나 더 강한가'
        rs = ind.rs_percentile
        if rs is not None:
            headroom = max(0.0, 100.0 - cfg.min_rs_percentile)
            quality = (
                max(0.0, min(1.0, (rs - cfg.min_rs_percentile) / headroom)) if headroom else 0.0
            )
            components.append(
                ScoreComponent(
                    id="rs_strength",
                    label="상대강도",
                    earned=round(cfg.weight_rs_strength * quality, 2),
                    max=cfg.weight_rs_strength,
                    detail=f"RS 백분위 {rs:.1f} (기준 {cfg.min_rs_percentile:.0f})",
                )
            )

        score = round(sum(c.earned for c in components), 2)
        return score, sum(c.max for c in components), components

    # -----------------------------------------------------------------
    # SETUP — 채점이 아니므로 게이트 탈락 종목에도 수행한다
    # -----------------------------------------------------------------

    def detect_setup(self, ctx: StockContext) -> SetupState:
        """CONTRACTING은 쓰지 않는다 — 변동성 수축은 VCP 어휘이지 Stage 어휘가 아니다."""
        cfg = self.config
        trading_range = self._range(ctx)
        if trading_range is None or trading_range.resistance <= 0:
            return SetupState.NO_SETUP

        price, resistance = ctx.price, trading_range.resistance
        above_pct = (price - resistance) / resistance * 100.0

        if above_pct > cfg.extended_pct_above_pivot:
            return SetupState.EXTENDED
        if above_pct > 0.0:
            return SetupState.BREAKOUT

        recent = ctx.ohlcv["close"].iloc[-cfg.swing_fractal_k * 2 :]
        if len(recent) and float(recent.max()) > resistance:
            return SetupState.FAILED_BREAKOUT

        if abs(above_pct) <= cfg.pivot_proximity_pct:
            return SetupState.PIVOT_READY
        return SetupState.BASE_FORMING

    def build_setup_metrics(self, ctx: StockContext) -> SetupMetrics:
        trading_range = self._range(ctx)
        ma_long = ctx.indicators.sma150
        age = self._stage2_age(ctx)

        detail = WeinsteinSetup(
            ma30w=round(ma_long, 4) if ma_long is not None else None,
            distance_from_ma30w_pct=(
                round((ctx.price - ma_long) / ma_long * 100.0, 4) if ma_long else None
            ),
            stage2_age_days=age,
            breakout_volume_ratio=(
                round(trading_range.breakout_volume_ratio, 4)
                if trading_range is not None and trading_range.breakout_volume_ratio is not None
                else None
            ),
        )

        if trading_range is None:
            return SetupMetrics(detail=detail)

        price = ctx.price
        resistance = trading_range.resistance
        return SetupMetrics(
            pivot_price=round(resistance, 4),
            to_pivot_ratio=round(price / resistance, 6) if resistance else None,
            to_pivot_pct=round((resistance - price) / price * 100.0, 4) if price else None,
            base_length_days=trading_range.length_days,
            base_depth_pct=round(trading_range.depth_pct, 4),
            detail=detail,
        )

    # -----------------------------------------------------------------
    # DECIDE
    # -----------------------------------------------------------------

    def _breakout_volume_note(self, trading_range: TradingRange | None) -> tuple[bool, str]:
        """돌파 거래량 확인 결과와 근거 한 줄. 반환: (확인됨, 메모).

        비율을 낼 수 없으면 **확인 실패**다. 확인 못 한 조건을 충족으로 치지 않는다.
        """
        required = self.config.volume_confirm_ratio
        ratio = trading_range.breakout_volume_ratio if trading_range is not None else None
        if ratio is None:
            return False, (
                "돌파 거래량을 확인할 수 없다 (50일 평균 거래량 미산출) — "
                "확인되지 않은 돌파는 사지 않는다"
            )
        if ratio < required:
            return False, (
                f"돌파 거래량이 50일 평균의 {ratio:.2f}배 — 기준 {required:.1f}배 미달. "
                "와인스타인의 돌파는 거래량이 조건이다"
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
        notes: list[str] = [f"Stage 2 국면 — 시장 국면 {ctx.regime.value}"]
        score_pct = 100.0 * score / max_score if max_score else 0.0

        if not ctx.volume_reliable:
            notes.append(
                "미완성 봉 — 돌파 거래량 항목을 만점에서 제외했다 "
                f"(만점 {max_score:.0f}). 종가 확정 후 재평가 필요"
            )

        if setup is SetupState.EXTENDED:
            return Verdict.AVOID, [
                *notes,
                f"거래범위 상단 대비 +{cfg.extended_pct_above_pivot:.0f}% 이상 확장 — "
                "와인스타인은 되돌림을 기다린다",
            ]
        if setup is SetupState.FAILED_BREAKOUT:
            return Verdict.AVOID, [*notes, "저항 돌파 후 범위 안으로 되돌림 — 실패한 돌파"]
        if setup is SetupState.NO_SETUP:
            return Verdict.WATCH, [*notes, "거래범위를 잡을 수 없다 — 저항선 기준이 없다"]

        if setup in (SetupState.PIVOT_READY, SetupState.BREAKOUT):
            if not ctx.volume_reliable:
                return Verdict.WATCH, [
                    *notes,
                    "거래량 확인 없이 BUY를 내지 않는다 — 와인스타인의 돌파는 거래량이 조건이다",
                ]

            # 돌파 거래량 — 돌파가 일어난 뒤에만 확인 가능한 조건이다.
            if setup is SetupState.BREAKOUT:
                confirmed, volume_note = self._breakout_volume_note(self._range(ctx))
                if not confirmed:
                    return Verdict.WATCH, [*notes, volume_note]
                notes.append(volume_note)
            else:
                notes.append(
                    "돌파 전이다 — 저항 돌파 시 거래량이 50일 평균의 "
                    f"{cfg.volume_confirm_ratio:.1f}배 이상인지 확인해야 한다"
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

        return Verdict.WATCH, [*notes, f"{setup.value} — 저항선까지 거리가 남았다"]
