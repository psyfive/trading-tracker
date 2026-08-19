"""프론트엔드 계약 (frozen schema).

이 모듈은 CLI, 백테스트 하네스, 향후 React 프론트엔드가 공유하는 단일 데이터 계약이다.
여기 정의된 필드명과 enum 값은 그대로 JSON 키/값이 되므로, 변경 시 SCHEMA_VERSION을 올린다.

원칙:
  - 판정 로직은 여기 없다. 이 모듈은 순수 데이터 형태만 정의한다.
  - 전략 점수는 절대 하나로 합산/평균되지 않는다. StrategyVerdict 리스트로 끝까지 분리 보존.
  - '데이터 없음'과 '조건 미달'은 다른 것이다 (CheckStatus 참조).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.2.0"

# progress_ratio 대조 허용 오차 (DiagnosisReport validator에서 사용).
# 목업/조립 코드가 소수 4자리로 반올림해도 통과하되 (2/3 -> 0.6667, 오차 3.3e-5),
# 다른 게이트의 비율값(최소 간격 1/total)은 걸러낸다.
PROGRESS_RATIO_TOLERANCE = 1e-3


# ---------------------------------------------------------------------------
# 공통 베이스
# ---------------------------------------------------------------------------


class Contract(BaseModel):
    """모든 계약 모델의 베이스.

    extra="forbid": 오타 필드가 조용히 통과하는 것을 막는다.
    frozen=True: 리포트는 생성 후 불변. 렌더러가 값을 고쳐 쓰는 사고를 원천 차단.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=False,
        str_strip_whitespace=True,
    )


Pct = Annotated[float, Field(description="퍼센트 값. 0.0~100.0 스케일 (0.15가 아니라 15.0)")]
Ratio = Annotated[float, Field(description="배수/비율. 1.0 = 100%")]
Price = Annotated[float, Field(ge=0.0, description="가격. 통화는 티커의 상장 통화")]


# ---------------------------------------------------------------------------
# Enums — 값 문자열이 곧 JSON 값이다. 변경 = breaking change.
# ---------------------------------------------------------------------------


class MarketRegime(StrEnum):
    """시장 전체 국면. 개별 종목 판정 위에 걸리는 상위 필터."""

    RISK_ON = "RISK_ON"
    CAUTION = "CAUTION"
    RISK_OFF = "RISK_OFF"


class Stage(StrEnum):
    """와인스타인 Stage Analysis 국면."""

    STAGE_1 = "STAGE_1"      # 바닥 다지기 / 무관심 구간
    STAGE_2 = "STAGE_2"      # 상승 추세 — 유일한 매수 구간
    STAGE_3 = "STAGE_3"      # 천장 다지기 / 분산
    STAGE_4 = "STAGE_4"      # 하락 추세
    UNDEFINED = "UNDEFINED"  # 판정 불가 (데이터 부족 또는 모호)


class SetupState(StrEnum):
    """차트 셋업의 진행 단계. 전략별로 독립 판정된다."""

    NO_SETUP = "NO_SETUP"
    BASE_FORMING = "BASE_FORMING"        # 베이스 형성 중
    CONTRACTING = "CONTRACTING"          # 변동성 수축 (VCP)
    PIVOT_READY = "PIVOT_READY"          # 피봇 근접, 돌파 대기
    BREAKOUT = "BREAKOUT"                # 돌파 발생
    EXTENDED = "EXTENDED"                # 돌파 후 과확장 — 신규 진입 부적합
    FAILED_BREAKOUT = "FAILED_BREAKOUT"  # 돌파 실패 / 되돌림


class PositionState(StrEnum):
    """보유 포지션의 관리 단계.

    RESERVED: 현재 어떤 계약 필드도 이 enum을 참조하지 않는다.
    보유 추적 기능이 없고, 청산 규칙이 전략마다 달라 소속 위치(리포트 최상위인지
    StrategyVerdict 하위인지)가 미확정이기 때문이다.
    해당 Phase에서 SCHEMA_VERSION을 올리며 필드를 추가한다.
    """

    NONE = "NONE"
    INITIAL = "INITIAL"                # 최초 진입, 손절 리스크 온전히 노출
    FREE_ROLL = "FREE_ROLL"            # 본전 손절 이동 완료
    TRAILING = "TRAILING"              # 추격 손절 운용 중
    EXIT_TRIGGERED = "EXIT_TRIGGERED"  # 청산 조건 발동


class Verdict(StrEnum):
    """전략 하나의 최종 판정.

    REJECTED_BY_GATE는 '점수를 계산하지 않았다'는 뜻이다. 낮은 점수와 다르다.
    """

    BUY = "BUY"
    WATCH = "WATCH"
    HOLD = "HOLD"
    AVOID = "AVOID"
    REJECTED_BY_GATE = "REJECTED_BY_GATE"


class CheckStatus(StrEnum):
    """게이트 개별 조건의 3-state 결과.

    UNAVAILABLE은 FAIL이 아니다. 상장 6개월 종목의 200일선처럼
    '평가에 필요한 데이터가 없는' 경우이며, 조건 미달과 반드시 구분해서 보여준다.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class Comparator(StrEnum):
    """actual과 threshold의 비교 방향. 프론트가 '>= 70' 같은 문구를 조립할 때 쓴다."""

    GTE = "GTE"
    GT = "GT"
    LTE = "LTE"
    LT = "LT"
    EQ = "EQ"
    BETWEEN = "BETWEEN"
    BOOL = "BOOL"  # 수치 비교가 아닌 참/거짓 조건 (actual/threshold는 None)


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class WarningCode(StrEnum):
    """구조화 경고 코드. 프론트는 message가 아니라 code로 분기한다."""

    INCOMPLETE_BAR = "INCOMPLETE_BAR"              # 장중 실행 — 당일 봉 미완성
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"  # 지표 산출에 필요한 봉 수 부족
    STALE_DATA = "STALE_DATA"                      # 마지막 봉이 과도하게 오래됨
    LOW_LIQUIDITY = "LOW_LIQUIDITY"                # 거래대금 미달 — 실행 가능성 의심
    EARNINGS_NEAR = "EARNINGS_NEAR"                # 실적 발표 임박
    RS_UNIVERSE_MISSING = "RS_UNIVERSE_MISSING"    # 유니버스 미구축 — RS 백분위 신뢰 불가
    REGIME_RISK_OFF = "REGIME_RISK_OFF"            # 시장 국면 역풍
    SPLIT_OR_GAP_SUSPECT = "SPLIT_OR_GAP_SUSPECT"  # 액면분할 / 데이터 이상 의심
    STRATEGY_ERROR = "STRATEGY_ERROR"              # 전략 평가 중 예외 — 해당 전략만 실패


class SessionState(StrEnum):
    """리포트 생성 시점의 거래 세션 상태."""

    PRE_MARKET = "PRE_MARKET"
    OPEN = "OPEN"
    POST_MARKET = "POST_MARKET"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class Agreement(StrEnum):
    """전략 간 의견 일치 정도. 점수 평균이 아니라 판정 개수에서만 파생된다.

    분모는 **전체 전략 수**다 (게이트 통과 전략 수가 아니다). REJECTED_BY_GATE도
    '비-BUY 의견'으로 센다 — 게이트 탈락 자체가 그 방법론의 부정적 판정이기 때문이다.
    이 정의는 DiagnosisReport validator가 강제하므로 조립 코드가 임의로 해석할 수 없다.
    """

    UNANIMOUS_BUY = "UNANIMOUS_BUY"  # 전 전략 BUY
    MAJORITY_BUY = "MAJORITY_BUY"    # 과반 BUY (전체 전략 대비)
    SPLIT = "SPLIT"                  # BUY 존재하나 과반 미달
    NONE = "NONE"                    # BUY 없음


# ---------------------------------------------------------------------------
# 경고
# ---------------------------------------------------------------------------


class DiagnosticWarning(Contract):
    """구조화 경고.

    이름이 DiagnosticWarning인 이유: 파이썬 빌트인 Warning 예외를 가리지 않기 위함.
    JSON 상의 위치는 report.warnings 이므로 프론트에는 영향 없다.
    """

    code: WarningCode
    severity: Severity
    message: str = Field(description="사람이 읽는 한글 설명. 프론트는 이걸로 분기하지 말 것.")
    field: str | None = Field(
        default=None,
        description="경고가 가리키는 리포트 내 필드 경로. 예: indicators.volume_ratio",
    )


# ---------------------------------------------------------------------------
# 게이트 (이진 필터)
# ---------------------------------------------------------------------------


class GateCheck(Contract):
    """게이트를 구성하는 개별 조건 하나.

    '왜 통과/탈락인지'를 숫자와 함께 담는 것이 이 모델의 존재 이유다.
    passed 불리언만 담으면 사용자가 아무것도 배우지 못한다.
    """

    id: str = Field(description="전략 내 안정적 식별자. 예: price_above_sma200")
    label: str = Field(description="화면 표시용 짧은 문구. 예: 주가 > 200일선")
    status: CheckStatus = Field(
        description="PASS / FAIL / UNAVAILABLE. bool로 미러링하지 않는다 — "
        "UNAVAILABLE을 False로 접으면 '데이터 없음'이 '조건 미달'로 둔갑한다."
    )
    actual: float | None = Field(default=None, description="측정값. UNAVAILABLE/BOOL이면 None 가능")
    threshold: float | None = Field(default=None, description="기준값. BOOL 조건이면 None")
    comparator: Comparator = Comparator.GTE
    unit: str | None = Field(default=None, description="표시 단위. %, x, $, days 등")
    shortfall_pct: Pct | None = Field(
        default=None,
        description=(
            "FAIL인 조건이 기준에서 얼마나 모자란지. |actual - threshold| / |threshold| * 100. "
            "PASS/UNAVAILABLE이거나 BOOL 조건이거나 threshold가 0이면 None. "
            "워치리스트를 '게이트 근접도 순'으로 정렬할 때 쓴다 — 이 값이 없으면 "
            "프론트가 comparator 방향과 스케일을 직접 해석해야 하고, 그 순간 "
            "판정 로직이 렌더러로 샌다."
        ),
    )
    reason: str = Field(description="숫자를 포함한 한 줄 설명. 예: 주가 182.4 > 200일선 164.1")

    @model_validator(mode="after")
    def _shortfall_only_on_failure(self) -> GateCheck:
        if self.shortfall_pct is not None and self.status is not CheckStatus.FAIL:
            raise ValueError("shortfall_pct는 FAIL인 조건에만 실린다")
        return self


class GateResult(Contract):
    """한 전략의 게이트 판정 전체.

    게이트는 이진 필터다. 여기서 탈락하면 SCORE 단계는 실행조차 되지 않는다.
    추세 조건은 절대 가산점이 아니다 — 전부 이 안에 들어온다.
    """

    strategy: str
    passed: bool
    checks: list[GateCheck]
    pass_count: int = Field(ge=0, description="status == PASS 인 항목 수")
    total: int = Field(ge=0, description="전체 조건 수")
    unavailable_count: int = Field(default=0, ge=0, description="status == UNAVAILABLE 인 항목 수")
    required_count: int | None = Field(
        default=None,
        description="통과에 필요한 최소 개수. None이면 전항목 통과 필요(AND 게이트).",
    )

    @model_validator(mode="after")
    def _counts_consistent(self) -> GateResult:
        if self.total != len(self.checks):
            raise ValueError("total은 checks 길이와 일치해야 한다")
        if self.pass_count != sum(c.status is CheckStatus.PASS for c in self.checks):
            raise ValueError("pass_count가 checks와 불일치")
        if self.unavailable_count != sum(c.status is CheckStatus.UNAVAILABLE for c in self.checks):
            raise ValueError("unavailable_count가 checks와 불일치")
        return self

    @property
    def failed_checks(self) -> list[GateCheck]:
        return [c for c in self.checks if c.status is CheckStatus.FAIL]

    @property
    def unavailable_checks(self) -> list[GateCheck]:
        return [c for c in self.checks if c.status is CheckStatus.UNAVAILABLE]


class GateProgress(Contract):
    """게이트 근접도 요약. 워치리스트 정렬 키.

    strategy_verdicts[i].gate.pass_count 는 3단계 중첩이라 다수 종목을 정렬할 때 불편하다.
    같은 값을 리포트 상단(consensus)에 평평하게 올려 둔다.
    DiagnosisReport validator가 원본 GateResult와의 일치를 강제한다 —
    전략 집합 자체의 일치(누락 금지)와 progress_ratio 값까지 전부 검증 대상이다.
    """

    strategy: str
    passed: bool
    pass_count: int = Field(ge=0)
    total: int = Field(ge=0)
    unavailable_count: int = Field(default=0, ge=0)
    progress_ratio: Ratio = Field(description="pass_count / total. 1.0 = 전 조건 통과")


# ---------------------------------------------------------------------------
# 점수 (타이밍 채점)
# ---------------------------------------------------------------------------


class ScoreComponent(Contract):
    """점수 한 항목. 게이트를 통과한 종목의 '진입 타이밍 품질'만 채점한다."""

    id: str = Field(description="전략 내 안정적 식별자. 예: volume_dry_up")
    label: str
    earned: float = Field(description="획득 점수")
    max: float = Field(gt=0.0, description="이 항목의 만점")
    detail: str = Field(description="숫자 포함 근거 한 줄")


class MinerviniSetup(Contract):
    """미너비니 VCP 전용 수치. SetupMetrics.detail에 실린다.

    여기 있는 값은 **VCP 어휘**다. 다른 방법론은 이 필드를 채우지 않으며,
    채울 수도 없다 (수축 횟수·건조도는 VCP 정의에서만 의미를 갖는다).
    """

    kind: Literal["minervini"] = "minervini"
    contraction_count: int | None = Field(
        default=None, description="베이스 내 수축 횟수. 미너비니는 2~4회를 이상적으로 본다"
    )
    contraction_ratio: Ratio | None = Field(
        default=None, description="VCP 수축도. 마지막 수축폭 / 첫 수축폭. 작을수록 타이트"
    )
    volume_dryup_ratio: Ratio | None = Field(
        default=None, description="거래량 건조도. 최근 평균거래량 / 베이스 평균거래량"
    )


class WeinsteinSetup(Contract):
    """와인스타인 Stage Analysis 전용 수치. SetupMetrics.detail에 실린다.

    여기 있는 값은 **Stage 어휘**다. 30주선과의 관계, Stage 2에 머문 기간처럼
    이 방법론에서만 의미를 갖는 것들이며 다른 전략은 채우지 않는다.

    피벗은 SetupMetrics 공통 코어에 있고 그 정의는 '직전 거래범위(Stage 1) 상단'이다 —
    미너비니의 '마지막 수축 고점'과 다른 값이며, 그래서 판정 주체 옆에 보존된다.
    """

    kind: Literal["weinstein"] = "weinstein"
    ma30w: float | None = Field(
        default=None, description="30주선. 일봉 150선으로 근사한다"
    )
    distance_from_ma30w_pct: Pct | None = Field(
        default=None, description="(주가 - 30주선) / 30주선 * 100. 음수 = 이동평균 아래"
    )
    stage2_age_days: int | None = Field(
        default=None,
        description="30주선 위 + 30주선 상승 상태가 이어진 일수. 와인스타인은 초기를 산다",
    )
    breakout_volume_ratio: Ratio | None = Field(
        default=None, description="최근 거래량 / 50일 평균. 돌파 확인용"
    )


class QullamaggieSetup(Contract):
    """Qullamaggie 브레이크아웃 전용 수치. SetupMetrics.detail에 실린다.

    핵심 어휘는 **직전 급등(prior move)**이다. 이 방법론은 이미 크게 오른 종목이
    쉬는 자리를 사므로, 급등 폭이 셋업의 전제 조건이자 품질 지표가 된다.

    공통 코어의 base_length_days / base_depth_pct는 여기서 '컨솔리데이션 길이/깊이'다.
    피벗은 '컨솔리데이션 상단'이며 미너비니의 피벗과 값이 다르다.
    """

    kind: Literal["qullamaggie"] = "qullamaggie"
    prior_move_pct: Pct | None = Field(
        default=None, description="컨솔리데이션 직전 저점에서 컨솔 고점까지의 상승률"
    )
    ma_distance_pct: Pct | None = Field(
        default=None, description="(주가 - 10일선) / 10일선 * 100. 붙어 있을수록 이상적"
    )
    volume_dryup_ratio: Ratio | None = Field(
        default=None, description="컨솔 후반 평균거래량 / 컨솔 평균거래량"
    )


# 전략별 셋업 상세의 판별 유니온. `kind`로 분기한다.
#
# 새 전략을 추가할 때 이 유니온에 변형을 **덧붙이는 것은 additive 변경**이다
# (기존 소비자는 모르는 kind를 만나면 detail을 무시하면 된다).
# 아직 구현하지 않은 전략의 필드를 미리 넣지 않는다 — 구현 전에 지은 필드명은
# 추측이고, 이 프로젝트는 미확정 필드를 계약에 넣지 않는다.
# (그래서 CANSLIM 변형은 여기 없다. 그 전략을 구현하는 Phase에서 덧붙인다.)
SetupDetail = Annotated[
    MinerviniSetup | WeinsteinSetup | QullamaggieSetup, Field(discriminator="kind")
]


class SetupMetrics(Contract):
    """셋업 수치. **공통 코어 + 전략별 detail** 구조다.

    IndicatorSnapshot에 두지 않는 이유: 두 전략이 다르게 계산할 수 있으면 지표가 아니다.
    피벗 정의(미너비니 VCP 마지막 수축 고점 vs Qullamaggie 컨솔 상단),
    베이스 시작점 정의가 전략마다 다르므로 판정 주체 옆에 둔다.

    공통 코어에 남긴 것: 모든 추세추종 방법론이 어떤 형태로든 갖는 개념
    (피벗 가격, 피벗까지 거리, 베이스 길이/깊이). **값의 정의는 전략마다 다르지만
    개념은 공유된다** — 그래서 프론트는 전략을 몰라도 이 네 값은 그릴 수 있다.

    detail에는 그 전략에서만 의미를 갖는 수치가 들어간다. 프론트는 `detail.kind`로
    분기하며, 모르는 kind는 무시하면 된다.
    """

    pivot_price: float | None = None
    to_pivot_ratio: Ratio | None = Field(
        default=None, description="price / pivot_price. 1.0 초과 = 이미 돌파"
    )
    to_pivot_pct: Pct | None = Field(
        default=None, description="피벗까지 남은 거리 %. 음수 = 이미 돌파"
    )
    base_length_days: int | None = None
    base_depth_pct: Pct | None = None
    detail: SetupDetail | None = Field(
        default=None, description="전략 고유 수치. kind로 분기한다. 없으면 None"
    )


class StrategyVerdict(Contract):
    """전략 하나의 독립 판정. 다른 전략과 절대 합산되지 않는다.

    gate.passed == False 이면 score/components는 None/빈 리스트이고
    verdict는 REJECTED_BY_GATE 이다. 이것은 '0점'이 아니라 '채점 안 함'이다.
    """

    strategy_name: str
    strategy_version: str = Field(default="0.1.0", description="전략 로직 버전. 백테스트 재현용.")
    gate: GateResult
    score: float | None = Field(default=None, description="게이트 탈락 시 None (0.0이 아님)")
    max_score: float | None = Field(default=None, description="만점. 게이트 탈락 시 None")
    components: list[ScoreComponent] = Field(default_factory=list)
    setup_state: SetupState = SetupState.NO_SETUP
    setup_metrics: SetupMetrics = Field(
        default_factory=SetupMetrics, description="전략별 셋업 수치. 지표가 아니다."
    )
    verdict: Verdict
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _gate_first_invariant(self) -> StrategyVerdict:
        if not self.gate.passed:
            if self.verdict is not Verdict.REJECTED_BY_GATE:
                raise ValueError("게이트 탈락 시 verdict는 REJECTED_BY_GATE여야 한다")
            if self.score is not None or self.components:
                raise ValueError("게이트 탈락 종목은 채점하지 않는다")
        elif self.verdict is Verdict.REJECTED_BY_GATE:
            raise ValueError("게이트 통과인데 REJECTED_BY_GATE일 수 없다")
        return self

    @property
    def score_pct(self) -> float | None:
        if self.score is None or not self.max_score:
            return None
        return 100.0 * self.score / self.max_score


# ---------------------------------------------------------------------------
# 지표 스냅샷
# ---------------------------------------------------------------------------


class IndicatorSnapshot(Contract):
    """마지막 봉 기준 지표 값 모음. 전부 Optional — 데이터 부족 시 None.

    수록 기준: **두 전략이 다르게 계산할 수 있으면 지표가 아니다.**
    계산식이 하나뿐인 것만 둔다 (SMA/EMA/RSI/MACD/BB/ATR/52주 고저/거래량 평균/RS).
    피벗·베이스 구조처럼 전략마다 정의가 갈리는 값은 StrategyVerdict.setup_metrics로 간다.

    None과 0.0은 다르다. 계산 불가는 반드시 None으로 두고, 전략은
    None을 만나면 GateCheck를 UNAVAILABLE로 만들어야 한다.
    """

    # 이동평균
    sma20: float | None = None
    sma50: float | None = None
    sma150: float | None = None
    sma200: float | None = None
    ema10: float | None = None
    ema21: float | None = None

    # 기울기 (해당 기간 대비 % 변화)
    sma50_slope_10d_pct: Pct | None = None
    sma150_slope_20d_pct: Pct | None = None
    sma200_slope_20d_pct: Pct | None = None

    # 오실레이터 / 변동성 (RSI, ATR 모두 Wilder smoothing)
    rsi14: float | None = None
    atr14: float | None = None
    atr_pct: Pct | None = Field(default=None, description="ATR14 / 종가 * 100")
    adr20_pct: Pct | None = Field(default=None, description="Qullamaggie용 20일 평균 일간 레인지")

    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None

    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    bb_width_pct: Pct | None = None

    # 52주 위치
    high_52w: float | None = None
    low_52w: float | None = None
    from_52w_high_pct: Pct | None = Field(default=None, description="고점 대비. 음수 = 고점 아래")
    above_52w_low_pct: Pct | None = None

    # 거래량 (is_bar_complete=False면 volume / volume_ratio는 신뢰 불가)
    volume: float | None = None
    avg_volume_50: float | None = None
    volume_ratio: Ratio | None = Field(default=None, description="volume / avg_volume_50")
    dollar_volume_50: float | None = None

    # 상대강도 (유니버스 필요)
    rs_percentile: float | None = Field(
        default=None, ge=0.0, le=100.0, description="유니버스 내 순위 0~100"
    )
    rs_line_new_high: bool | None = None

    # 베이스/피벗 구조는 여기 없다 -> StrategyVerdict.setup_metrics
    # 전략마다 정의가 다르므로 지표가 아니다.


# ---------------------------------------------------------------------------
# 리스크 플랜
# ---------------------------------------------------------------------------


class RLevel(Contract):
    """R 배수 도달 지점 하나.

    리스트로 둔 이유: 목표 배수 개수가 설정(RiskConfig.r_targets)에 따라 달라지고,
    별칭 없는 평범한 필드명이라 직렬화에 by_alias가 필요 없다.
    """

    multiple: float = Field(gt=0.0, description="R 배수. 1.0 = 1R")
    price: Price = Field(description="entry + multiple * r_per_share")
    suggested_action: str = Field(description="해당 지점에서의 권고 행동. 예: 손절을 본전으로 이동")


class RiskPlan(Contract):
    """진입 / 손절 / 사이징. 계좌 파라미터가 없으면 shares 계열은 None."""

    entry: Price
    stop: Price
    stop_pct: Pct = Field(description="(entry - stop) / entry * 100")
    r_per_share: float = Field(description="entry - stop. 1R의 절대 금액")
    shares: int | None = Field(default=None, ge=0)
    position_value: float | None = Field(default=None, ge=0.0)
    account_equity: float | None = Field(
        default=None, description="사이징 근거. 없으면 shares도 None"
    )
    risk_pct: Pct | None = Field(default=None, description="계좌 대비 감수 리스크 %")
    risk_amount: float | None = Field(default=None, description="손절 시 실제 손실 금액")
    r_levels: list[RLevel] = Field(default_factory=list, description="배수 오름차순 정렬")
    exit_rules: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stop_below_entry(self) -> RiskPlan:
        if self.stop >= self.entry:
            raise ValueError("롱 진입에서 손절가는 진입가보다 낮아야 한다")
        multiples = [level.multiple for level in self.r_levels]
        if multiples != sorted(multiples):
            raise ValueError("r_levels는 multiple 오름차순이어야 한다")
        return self


# ---------------------------------------------------------------------------
# 컨센서스 — 집계만, 평균 없음
# ---------------------------------------------------------------------------


class ConsensusSummary(Contract):
    """전략 판정들의 집계.

    의도적으로 평균 점수 필드가 없다. 서로 다른 방법론의 점수는
    척도가 다르므로 평균이 의미를 갖지 않는다. 개수만 센다.

    여기 있는 필드는 전부 strategy_verdicts에서 파생된 **중복 저장**이다.
    DiagnosisReport validator가 verdict_counts / buy_strategies /
    gate_passed_strategies / gate_progress / agreement 전부를 원본과 대조한다.
    드리프트는 리포트 생성 시점에 ValidationError로 죽는다.
    """

    total_strategies: int = Field(ge=0)
    verdict_counts: dict[Verdict, int] = Field(default_factory=dict)
    buy_strategies: list[str] = Field(default_factory=list)
    gate_passed_strategies: list[str] = Field(default_factory=list)
    gate_progress: list[GateProgress] = Field(
        default_factory=list,
        description="전략별 게이트 근접도. 워치리스트를 근접도 순으로 정렬할 때 쓴다.",
    )
    agreement: Agreement = Agreement.NONE

    @property
    def best_gate_progress(self) -> float:
        """가장 근접한 전략의 진행률. 워치리스트 정렬의 기본 키."""
        return max((g.progress_ratio for g in self.gate_progress), default=0.0)


# ---------------------------------------------------------------------------
# 봉 메타 — is_bar_complete의 근거
# ---------------------------------------------------------------------------


class BarMeta(Contract):
    """마지막 봉의 신뢰도 정보.

    장중 실행 시 당일 봉은 미완성이므로 거래량 기반 판정(돌파 거래량, 볼륨 드라이업)이
    무의미해진다. 그 사실을 계약 레벨에서 명시적으로 전달한다.
    """

    last_bar_date: date
    session_state: SessionState = SessionState.UNKNOWN
    is_bar_complete: bool
    bars_available: int = Field(ge=0, description="지표 계산에 쓸 수 있는 총 봉 수")
    exchange_tz: str = Field(default="America/New_York")
    volume_judgements_reliable: bool = Field(
        description="is_bar_complete와 유동성을 함께 본 결과. False면 거래량 조건을 UNAVAILABLE로.",
    )


# ---------------------------------------------------------------------------
# 최종 리포트 — 프론트엔드가 받는 루트 객체
# ---------------------------------------------------------------------------


class DiagnosisReport(Contract):
    """한 티커에 대한 진단 전체. CLI와 프론트엔드가 공유하는 루트 계약."""

    schema_version: str = SCHEMA_VERSION
    ticker: str
    as_of: date = Field(description="마지막 봉의 거래일")
    generated_at: datetime = Field(description="리포트 생성 시각 (tz-aware UTC)")
    price: Price
    is_bar_complete: bool = Field(description="bar_meta.is_bar_complete의 최상위 미러. 계약 고정.")
    bar_meta: BarMeta
    regime: MarketRegime
    stage: Stage
    indicators: IndicatorSnapshot
    strategy_verdicts: list[StrategyVerdict] = Field(default_factory=list)
    consensus: ConsensusSummary
    risk_plan: RiskPlan | None = Field(default=None, description="BUY/WATCH가 하나도 없으면 None")
    warnings: list[DiagnosticWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mirror_and_counts_consistent(self) -> DiagnosisReport:
        if self.is_bar_complete != self.bar_meta.is_bar_complete:
            raise ValueError("is_bar_complete가 bar_meta와 불일치")

        consensus = self.consensus
        verdicts = self.strategy_verdicts
        if consensus.total_strategies != len(verdicts):
            raise ValueError("consensus.total_strategies가 strategy_verdicts와 불일치")

        # gate_progress: 빈 리스트로 검증을 우회할 수 없다.
        # 전략이 있으면 반드시 전략별 근접도가 있어야 하고, 값도 원본 게이트와 같아야 한다.
        expected = {
            v.strategy_name: (
                v.gate.passed,
                v.gate.pass_count,
                v.gate.total,
                v.gate.unavailable_count,
            )
            for v in verdicts
        }
        actual = {
            g.strategy: (g.passed, g.pass_count, g.total, g.unavailable_count)
            for g in consensus.gate_progress
        }
        if expected != actual:
            raise ValueError("consensus.gate_progress가 strategy_verdicts의 게이트와 불일치")
        for g in consensus.gate_progress:
            ratio = g.pass_count / g.total if g.total else 0.0
            if abs(g.progress_ratio - ratio) > PROGRESS_RATIO_TOLERANCE:
                raise ValueError(
                    f"gate_progress[{g.strategy}].progress_ratio({g.progress_ratio})가 "
                    f"pass_count/total({ratio:.6f})과 불일치"
                )

        # verdict_counts: 0인 항목은 있어도 되지만, 0이 아닌 항목은 실제 개수와 같아야 한다.
        counted: dict[Verdict, int] = {}
        for v in verdicts:
            counted[v.verdict] = counted.get(v.verdict, 0) + 1
        provided = {k: n for k, n in consensus.verdict_counts.items() if n != 0}
        if provided != counted:
            raise ValueError("consensus.verdict_counts가 strategy_verdicts와 불일치")

        buys = [v.strategy_name for v in verdicts if v.verdict is Verdict.BUY]
        if consensus.buy_strategies != buys:
            raise ValueError("consensus.buy_strategies가 strategy_verdicts와 불일치")
        gate_passed = [v.strategy_name for v in verdicts if v.gate.passed]
        if consensus.gate_passed_strategies != gate_passed:
            raise ValueError("consensus.gate_passed_strategies가 strategy_verdicts와 불일치")

        # agreement: Agreement enum docstring의 정의를 그대로 강제한다.
        total, n_buy = len(verdicts), len(buys)
        if n_buy == 0:
            derived = Agreement.NONE
        elif n_buy == total:
            derived = Agreement.UNANIMOUS_BUY
        elif n_buy * 2 > total:
            derived = Agreement.MAJORITY_BUY
        else:
            derived = Agreement.SPLIT
        if consensus.agreement is not derived:
            raise ValueError(
                f"consensus.agreement({consensus.agreement.value})가 판정 개수에서 파생된 "
                f"값({derived.value})과 불일치"
            )

        if not self.is_bar_complete and not any(
            w.code is WarningCode.INCOMPLETE_BAR for w in self.warnings
        ):
            raise ValueError("미완성 봉이면 INCOMPLETE_BAR 경고가 반드시 있어야 한다")
        return self
