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
    """시장 국면 판정 임계값."""

    index_ticker: str = "^GSPC"
    breadth_ticker: str | None = None
    risk_on_min_pct_above_sma200: float = 0.0
    caution_max_distribution_days: int = 4
    distribution_lookback_days: int = 25


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
    """RS 백분위 계산용 유니버스."""

    source: str = "sp500"
    rs_lookback_days: int = 252
    rs_weights: tuple[float, ...] = (0.4, 0.2, 0.2, 0.2)
    min_universe_size: int = 100


@dataclass(frozen=True)
class MinerviniConfig:
    """미너비니 SEPA 추세 템플릿 + VCP 임계값."""

    min_pct_above_52w_low: float = 30.0
    max_pct_below_52w_high: float = 25.0
    min_rs_percentile: float = 70.0
    sma200_slope_min_pct: float = 0.0
    sma150_slope_min_pct: float = 0.0
    max_base_depth_pct: float = 35.0
    min_base_length_days: int = 25
    breakout_volume_ratio: float = 1.5
    extended_pct_above_pivot: float = 5.0


@dataclass(frozen=True)
class WeinsteinConfig:
    """와인스타인 Stage Analysis 임계값 (주봉 30주선 = 일봉 150선 근사)."""

    ma_period_daily: int = 150
    slope_lookback: int = 20
    slope_min_pct: float = 0.0
    volume_confirm_ratio: float = 2.0


@dataclass(frozen=True)
class CanslimConfig:
    """오닐 CANSLIM 임계값. 펀더멘털 항목은 이후 단계에서 확장."""

    min_rs_percentile: float = 80.0
    max_pct_below_52w_high: float = 15.0
    breakout_volume_ratio: float = 1.4


@dataclass(frozen=True)
class QullamaggieConfig:
    """Qullamaggie 브레이크아웃/EP 임계값."""

    min_adr_pct: float = 3.0
    min_dollar_volume: float = 3_000_000.0
    min_prior_move_pct: float = 30.0
    consolidation_min_days: int = 10


@dataclass(frozen=True)
class AppConfig:
    """최상위 설정. main.py와 backtest/harness.py가 이것 하나만 주고받는다."""

    data: DataConfig = field(default_factory=DataConfig)
    exchanges: ExchangeConfig = field(default_factory=ExchangeConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    minervini: MinerviniConfig = field(default_factory=MinerviniConfig)
    weinstein: WeinsteinConfig = field(default_factory=WeinsteinConfig)
    canslim: CanslimConfig = field(default_factory=CanslimConfig)
    qullamaggie: QullamaggieConfig = field(default_factory=QullamaggieConfig)


DEFAULT_CONFIG = AppConfig()
