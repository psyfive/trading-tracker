"""모든 임계값의 단일 출처.

하드코딩 금지 원칙(CLAUDE.md 원칙 3)의 구현체다. 전략 파일 안에 200, 0.25, 70 같은
리터럴이 등장하면 안 되고, 전부 여기 dataclass 필드로 올라온다.

frozen=True인 이유: 백테스트 파라미터 스윕에서 dataclasses.replace()로 변형본을 만들되
원본이 오염되지 않게 하기 위함.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


@dataclass(frozen=True)
class MarketSession:
    """거래소 정규장 정보. 봉 완성 여부 판정의 기준이 된다.

    ticker_suffixes가 빈 튜플이면 '접미사 없는 티커'(미국)를 뜻한다.
    """

    exchange: str
    timezone: str
    open_time: time
    close_time: time
    ticker_suffixes: tuple[str, ...]


US_EQUITY = MarketSession(
    exchange="US",
    timezone="America/New_York",
    open_time=time(9, 30),
    close_time=time(16, 0),
    ticker_suffixes=(),
)

KRX_EQUITY = MarketSession(
    exchange="KRX",
    timezone="Asia/Seoul",
    open_time=time(9, 0),
    close_time=time(15, 30),
    ticker_suffixes=(".KS", ".KQ"),
)


@dataclass(frozen=True)
class ExchangeConfig:
    """거래소 판정 설정.

    등록되지 않은 접미사(.T, .L, .HK 등)는 판정 불가로 처리한다.
    이때는 보수적으로 is_bar_complete=False로 두고 경고를 붙인다 —
    모르는 거래소의 봉을 완성됐다고 단정하면 거래량 오진으로 이어진다.
    """

    sessions: tuple[MarketSession, ...] = (US_EQUITY, KRX_EQUITY)
    settle_buffer_minutes: int = 15


@dataclass(frozen=True)
class DataConfig:
    """데이터 수집 설정."""

    history_years: int = 3
    cache_dir: str = ".cache"
    interval: str = "1d"
    auto_adjust: bool = True
    min_bars_absolute: int = 20
    warn_below_bars: int = 200
    stale_data_max_days: int = 5


@dataclass(frozen=True)
class IndicatorConfig:
    """지표 기간 파라미터. RSI/ATR은 Wilder smoothing 전제."""

    sma_periods: tuple[int, ...] = (20, 50, 150, 200)
    ema_periods: tuple[int, ...] = (10, 21)
    rsi_period: int = 14
    atr_period: int = 14
    adr_period: int = 20
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    bb_ddof: int = 0
    volume_avg_period: int = 50
    slope_lookback_short: int = 10
    slope_lookback_long: int = 20
    high_low_lookback: int = 252


@dataclass(frozen=True)
class RegimeConfig:
    """시장 국면 + Stage 판정 임계값."""

    # 시장별 벤치마크 지수. 티커 접미사로 고른다 (.KS/.KQ -> KOSPI, 그 외 -> SPY).
    benchmark_by_exchange: tuple[tuple[str, str], ...] = (("US", "SPY"), ("KRX", "^KS11"))
    default_benchmark: str = "SPY"

    sma_period: int = 200
    slope_lookback: int = 20
    risk_on_min_sma200_slope_pct: float = 0.0

    # 분산일: 거래량이 전일보다 늘면서 종가가 이 폭 이상 하락한 날
    distribution_min_drop_pct: float = 0.2
    caution_max_distribution_days: int = 4
    distribution_lookback_days: int = 25

    # Stage 판정 (와인스타인 30주선 = 일봉 150선 근사)
    stage_ma_period: int = 150
    stage_flat_slope_pct: float = 0.5


@dataclass(frozen=True)
class RiskConfig:
    """포지션 사이징 / 손절 규칙."""

    account_equity: float | None = None
    risk_pct_per_trade: float = 1.0
    max_position_pct: float = 25.0
    stop_atr_multiple: float = 2.0
    max_stop_pct: float = 8.0
    r_targets: tuple[float, ...] = (1.0, 2.0, 3.0)


@dataclass(frozen=True)
class UniverseConfig:
    """RS 백분위 계산용 유니버스.

    RS는 '같은 시점 유니버스 내 순위'다. 시장이 다르면 거래일이 다르므로
    유니버스도 시장별로 분리한다 — 섞으면 서로 다른 날짜를 비교하게 된다.
    """

    universe_by_exchange: tuple[tuple[str, str], ...] = (("US", "us_large"), ("KRX", "kospi"))
    default_universe: str = "us_large"

    rs_lookback_days: int = 252
    rs_weights: tuple[float, ...] = (0.4, 0.2, 0.2, 0.2)
    # 이 수 미만이면 백분위를 내지 않는다. 해상도가 100/n p 단위라
    # 표본이 적으면 임계값 부근 판정이 거칠어지기 때문이다.
    min_universe_size: int = 30


@dataclass(frozen=True)
class MinerviniConfig:
    """미너비니 SEPA 추세 템플릿(GATE) + VCP 타이밍(SCORE) 임계값.

    GATE 임계값과 SCORE 임계값을 한 dataclass에 두되 주석으로 구분한다.
    추세 조건을 SCORE로 옮기지 말 것 — 이 프로젝트의 핵심 설계다.
    """

    # --- GATE: 추세 템플릿 8조건 ---
    min_pct_above_52w_low: float = 30.0
    max_pct_below_52w_high: float = 25.0
    min_rs_percentile: float = 70.0
    sma200_slope_min_pct: float = 0.0

    # --- 베이스/피벗 탐지 ---
    base_lookback_days: int = 65      # 베이스 앵커(최근 고점)를 찾는 창
    swing_fractal_k: int = 3          # 스윙 고저 판정 창. i봉이 i±k 구간의 극값인가
    min_base_length_days: int = 25

    # --- 돌파 확인: BUY의 필요조건. 점수가 아니라 이진 조건이다 ---
    # 깊은 베이스는 미너비니가 실패 확률로 거르는 대상이다. 이 깊이를 넘는 베이스는
    # 돌파해도 BUY를 내지 않는다 (셋업 판정 자체는 그대로 수행한다).
    max_base_depth_pct: float = 35.0
    # 돌파 봉 거래량 / 50일 평균. 거래량 없는 돌파는 사지 않는다.
    breakout_volume_ratio: float = 1.5

    # --- SCORE: 타이밍 채점 (게이트 통과 종목만) ---
    pivot_proximity_pct: float = 3.0        # 피벗 이내 이 거리면 PIVOT_READY
    extended_pct_above_pivot: float = 5.0   # 피벗 대비 이 이상이면 EXTENDED
    ideal_contraction_ratio: float = 0.5    # 마지막 수축폭 / 첫 수축폭. 작을수록 타이트
    ideal_volume_dryup_ratio: float = 0.7   # 최근 거래량 / 베이스 평균. 작을수록 건조
    ideal_base_length_days: int = 40
    min_contractions: int = 2
    buy_min_score_pct: float = 60.0         # 만점 대비 이 비율 이상이어야 BUY

    # 배점 (합계 100)
    weight_contraction: float = 25.0
    weight_volume_dryup: float = 20.0
    weight_pivot_proximity: float = 20.0
    weight_base_length: float = 15.0
    weight_rs_strength: float = 20.0


@dataclass(frozen=True)
class WeinsteinConfig:
    """와인스타인 Stage Analysis 임계값 (주봉 30주선 = 일봉 150선 근사).

    게이트는 **미너비니와 겹치지 않는 조건만** 담는다. Stage 2 판정 자체가
    '30주선 위 + 30주선 상승'을 함의하므로 그 둘을 별도 조건으로 또 세면
    게이트 진행률(pass_count/total)이 부풀고 근접도 정렬이 망가진다.
    Stage 2가 함의하지 않는 것 — 10주선, 시장 국면, RS, 유동성 — 만 남긴다.
    """

    # --- GATE ---
    # 아래 3개는 RegimeConfig의 Stage 판정 파라미터와 **같은 값이어야 한다**.
    # 게이트가 쓰는 Stage(주입값)와 신선도가 세는 Stage 2가 다른 선을 보면
    # 화면의 Stage와 점수의 Stage가 조용히 갈라진다. AppConfig.__post_init__이 강제한다.
    ma_period_daily: int = 150        # 30주선 근사. Stage 판정에 쓰는 선
    slope_lookback: int = 20
    stage_flat_slope_pct: float = 0.5  # 이 기울기 이하면 '평탄' — Stage 2가 아니다

    ma_short_period_daily: int = 50   # 10주선 근사. Stage 2 안에서의 단기 이탈 필터
    min_rs_percentile: float = 50.0   # 미너비니(70)보다 낮다 — 국면 전환 초기를 잡는 방법론
    min_dollar_volume: float = 1_000_000.0

    # --- 저항선(Stage 1 거래범위 상단) 탐지 ---
    range_lookback_days: int = 60
    swing_fractal_k: int = 3
    min_range_length_days: int = 20

    # --- SCORE: 타이밍 채점 (게이트 통과 종목만) ---
    volume_confirm_ratio: float = 2.0        # 돌파 거래량 / 50일 평균. 와인스타인의 확인 조건
    pivot_proximity_pct: float = 3.0
    extended_pct_above_pivot: float = 8.0    # 미너비니(5)보다 관대 — 진입 창이 더 길다
    ideal_distance_from_ma_pct: float = 10.0 # 30주선 이격이 이 이내면 만점
    max_distance_from_ma_pct: float = 30.0   # 이 이상 벌어지면 0점 (과확장)
    ideal_stage2_age_days: int = 40          # Stage 2 진입 후 이 정도까지가 '초기'
    max_stage2_age_days: int = 150           # 이보다 오래되면 신선도 0점
    buy_min_score_pct: float = 60.0

    # 배점 (합계 100)
    weight_breakout_volume: float = 25.0
    weight_stage_freshness: float = 25.0
    weight_ma_distance: float = 25.0
    weight_rs_strength: float = 25.0


@dataclass(frozen=True)
class CanslimConfig:
    """오닐 CANSLIM 임계값. 펀더멘털 항목은 이후 단계에서 확장."""

    min_rs_percentile: float = 80.0
    max_pct_below_52w_high: float = 15.0
    breakout_volume_ratio: float = 1.4


@dataclass(frozen=True)
class QullamaggieConfig:
    """Qullamaggie 브레이크아웃 셋업 임계값.

    이 방법론의 게이트는 추세 자체보다 **직전 급등(prior move)과 변동성·유동성**이다.
    "이미 크게 오른 종목이 쉬고 있는 자리"를 사는 방식이라, 급등 이력이 없는 종목은
    아무리 추세가 곱게 서 있어도 대상이 아니다.
    """

    # --- GATE ---
    min_adr_pct: float = 3.0                  # 변동성이 없으면 목표 수익도 없다
    min_dollar_volume: float = 3_000_000.0
    min_prior_move_pct: float = 30.0          # 컨솔 직전 상승폭
    min_rs_percentile: float = 80.0           # 미너비니(70)보다 높다 — 주도주만 본다

    # --- 컨솔리데이션 탐지 ---
    consolidation_min_days: int = 10
    consolidation_max_days: int = 65
    prior_move_lookback_days: int = 130       # 컨솔 시작 이전 상승을 재는 창
    swing_fractal_k: int = 3

    # --- SCORE: 타이밍 채점 (게이트 통과 종목만) ---
    ideal_consolidation_depth_pct: float = 12.0  # 얕게 조일수록 좋다
    max_consolidation_depth_pct: float = 35.0    # 이보다 깊으면 0점
    ideal_ma_distance_pct: float = 3.0           # 10일선에 붙어 있을수록 좋다
    max_ma_distance_pct: float = 15.0
    ideal_prior_move_pct: float = 100.0
    ideal_volume_dryup_ratio: float = 0.7
    # 컨솔 평균과 같은 거래량(1.0배)이면 건조가 아니다 -> 0점. 항등원이 아니라 판정 기준이다.
    max_volume_dryup_ratio: float = 1.0
    pivot_proximity_pct: float = 3.0
    extended_pct_above_pivot: float = 5.0
    buy_min_score_pct: float = 60.0

    # --- 돌파 확인: BUY의 필요조건 ---
    # 돌파 봉 거래량 / 50일 평균. 미너비니(1.5)와 같은 기준을 쓰되 값은 독립이다.
    breakout_volume_ratio: float = 1.5

    # 배점 (합계 100)
    weight_tightness: float = 25.0
    weight_ma_proximity: float = 25.0
    weight_pivot_proximity: float = 20.0
    weight_prior_move: float = 15.0
    weight_volume_dryup: float = 15.0


@dataclass(frozen=True)
class BacktestConfig:
    """백테스트 하네스 설정.

    entry_offset_bars=1: 시그널이 난 봉의 **다음 봉 시가**로 진입한다.
    시그널 봉의 종가로 사는 것은 종가를 미리 아는 것이므로 look-ahead다.
    """

    horizons: tuple[int, ...] = (20, 60)
    entry_offset_bars: int = 1
    # 252(52주 고저) + 20(sma200 기울기 lookback) = 272. 200으로 두면 평가 초반 ~70봉에서
    # 52주/기울기 지표가 None -> 게이트 UNAVAILABLE이 되어 표본이 조용히 줄어든다.
    warmup_bars: int = 272
    score_buckets: tuple[tuple[float, float], ...] = (
        (0.0, 40.0),
        (40.0, 60.0),
        (60.0, 70.0),
        (70.0, 85.0),
        (85.0, 100.0),
    )
    min_sample_size: int = 30
    lookahead_audit_samples: int = 12
    strict_lookahead: bool = True


@dataclass(frozen=True)
class AppConfig:
    """최상위 설정. main.py와 backtest/harness.py가 이것 하나만 주고받는다.

    __post_init__은 **전략 간 파라미터 드리프트**를 막는다. 와인스타인의 Stage 신선도는
    자기 config로 Stage 2 조건을 직접 세는데, 게이트가 쓰는 Stage는 RegimeConfig로
    산출돼 주입된 값이다. 두 곳이 다른 선(또는 다른 평탄 기준)을 보면 화면의 Stage와
    점수가 세는 Stage 2가 조용히 갈라진다 — 스윕에서 replace()로 한쪽만 바꿀 때
    실제로 일어나는 사고라, 조용히 어긋나는 대신 여기서 시끄럽게 죽인다.
    """

    data: DataConfig = field(default_factory=DataConfig)
    exchanges: ExchangeConfig = field(default_factory=ExchangeConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    minervini: MinerviniConfig = field(default_factory=MinerviniConfig)
    weinstein: WeinsteinConfig = field(default_factory=WeinsteinConfig)
    canslim: CanslimConfig = field(default_factory=CanslimConfig)
    qullamaggie: QullamaggieConfig = field(default_factory=QullamaggieConfig)

    def __post_init__(self) -> None:
        mismatches = [
            (name, mine, theirs)
            for name, mine, theirs in (
                ("이동평균 기간", self.weinstein.ma_period_daily, self.regime.stage_ma_period),
                ("기울기 lookback", self.weinstein.slope_lookback, self.regime.slope_lookback),
                (
                    "평탄 기울기 기준",
                    self.weinstein.stage_flat_slope_pct,
                    self.regime.stage_flat_slope_pct,
                ),
            )
            if mine != theirs
        ]
        if mismatches:
            detail = ", ".join(f"{name}: weinstein={mine} vs regime={theirs}"
                               for name, mine, theirs in mismatches)
            raise ValueError(
                "WeinsteinConfig의 Stage 파라미터가 RegimeConfig와 다르다 — "
                f"게이트의 Stage와 신선도가 세는 Stage 2가 갈라진다 ({detail})"
            )


DEFAULT_CONFIG = AppConfig()
