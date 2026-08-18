"""yfinance OHLCV 수집 + parquet 캐시 + 봉 완성 여부 판정.

네트워크 호출은 이 레이어에서만 일어난다 (CLAUDE.md: 지표/전략은 네트워크 금지).
yfinance의 raw 예외는 여기서 전부 도메인 예외로 변환한다.

**상장 기간이 짧은 종목은 예외가 아니다.** 가용 데이터와 INSUFFICIENT_HISTORY 경고를
함께 돌려준다. SMA200이 None이 되고, 그것을 쓰는 게이트 조건은 나중에 UNAVAILABLE이 된다.
여기서 예외를 던지면 신규 상장주가 진단 대상에서 조용히 사라진다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config import DataConfig, ExchangeConfig, MarketSession
from core.types import BarMeta, DiagnosticWarning, SessionState, Severity, WarningCode

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_CACHE_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# 도메인 예외
# ---------------------------------------------------------------------------


class DataError(Exception):
    """데이터 레이어 공통 예외 베이스."""


class InvalidTickerError(DataError):
    """티커가 존재하지 않거나 상장폐지되어 데이터가 하나도 없다."""


class InsufficientDataError(DataError):
    """봉 수가 절대 최소치 미만이라 어떤 지표도 의미가 없다.

    '3년치가 안 된다'는 이 예외가 아니다. 그건 경고로 처리한다.
    """


class DataFetchError(DataError):
    """네트워크/파싱 실패. yfinance 예외를 감싼다."""


# ---------------------------------------------------------------------------
# 반환 묶음
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OhlcvBundle:
    """수집 결과 일체. 경고는 데이터와 함께 다녀야 상위에서 잃어버리지 않는다."""

    ticker: str
    ohlcv: pd.DataFrame
    bar_meta: BarMeta
    warnings: tuple[DiagnosticWarning, ...]
    from_cache: bool


# ---------------------------------------------------------------------------
# 거래소 세션
# ---------------------------------------------------------------------------


def resolve_session(ticker: str, config: ExchangeConfig) -> MarketSession | None:
    """티커 접미사로 거래소를 판정한다. 모르면 None.

    접미사가 있는 세션을 먼저 확인하고, 어디에도 걸리지 않으면서 '.'이 없으면 미국으로 본다.
    ".T"(도쿄) 같은 미등록 접미사는 None이며 호출부가 보수적으로 처리한다.
    """
    upper = ticker.upper()
    for session in config.sessions:
        if any(upper.endswith(suffix) for suffix in session.ticker_suffixes):
            return session
    if "." not in upper:
        for session in config.sessions:
            if not session.ticker_suffixes:
                return session
    return None


def _session_state(
    local_now: datetime, session: MarketSession, buffer_minutes: int
) -> SessionState:
    """정규장 기준 세션 상태.

    POST_MARKET은 '장은 끝났지만 종가가 아직 확정 대기 중'인 buffer 구간을 뜻한다.
    시간외 거래 세션을 모델링하는 것이 아니다.
    """
    if local_now.weekday() >= 5:
        return SessionState.CLOSED

    now_time = local_now.time()
    if now_time < session.open_time:
        return SessionState.PRE_MARKET
    if now_time < session.close_time:
        return SessionState.OPEN

    settle_deadline = (
        datetime.combine(local_now.date(), session.close_time) + timedelta(minutes=buffer_minutes)
    ).time()
    if now_time < settle_deadline:
        return SessionState.POST_MARKET
    return SessionState.CLOSED


def build_bar_meta(
    df: pd.DataFrame,
    ticker: str,
    config: ExchangeConfig,
    *,
    now: datetime | None = None,
) -> tuple[BarMeta, list[DiagnosticWarning]]:
    """마지막 봉의 완성 여부를 판정한다.

    판정 규칙:
      - 마지막 봉 날짜 < 거래소 현지 오늘  -> 지난 세션의 봉이므로 완성
      - 마지막 봉 날짜 == 거래소 현지 오늘 -> 종가 + settle_buffer 이후여야 완성
      - 거래소 미상                        -> 보수적으로 미완성 + 경고

    거래량 신뢰도는 봉 완성 여부를 그대로 따른다. 미완성 봉의 누적 거래량으로
    '거래량 부족'을 판정하면 명백한 오진이다.
    """
    now = now or datetime.now(UTC)
    warnings: list[DiagnosticWarning] = []

    if df.empty:
        raise InvalidTickerError(f"{ticker}: 봉이 하나도 없다")

    last_bar_date: date = df.index[-1].date()
    session = resolve_session(ticker, config)

    if session is None:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.INCOMPLETE_BAR,
                severity=Severity.WARN,
                message=(
                    f"{ticker}의 거래소를 판정할 수 없어 봉 완성 여부를 확인하지 못했다. "
                    "보수적으로 미완성으로 간주한다 — 거래량 기반 조건은 평가하지 않는다"
                ),
                field="bar_meta.is_bar_complete",
            )
        )
        return (
            BarMeta(
                last_bar_date=last_bar_date,
                session_state=SessionState.UNKNOWN,
                is_bar_complete=False,
                bars_available=len(df),
                exchange_tz="UTC",
                volume_judgements_reliable=False,
            ),
            warnings,
        )

    local_now = now.astimezone(ZoneInfo(session.timezone))
    state = _session_state(local_now, session, config.settle_buffer_minutes)

    if last_bar_date < local_now.date():
        is_complete = True
    elif last_bar_date == local_now.date():
        is_complete = state is SessionState.CLOSED
    else:
        is_complete = False

    if not is_complete:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.INCOMPLETE_BAR,
                severity=Severity.WARN,
                message=(
                    f"{session.exchange} 장중 실행 "
                    f"({local_now:%Y-%m-%d %H:%M} {session.timezone}) — "
                    "당일 봉이 미완성이다. 거래량 기반 판정은 신뢰할 수 없고 "
                    "종가 조건도 확정이 아니다"
                ),
                field="indicators.volume_ratio",
            )
        )

    return (
        BarMeta(
            last_bar_date=last_bar_date,
            session_state=state,
            is_bar_complete=is_complete,
            bars_available=len(df),
            exchange_tz=session.timezone,
            volume_judgements_reliable=is_complete,
        ),
        warnings,
    )


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------


def normalize_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance 출력을 프로젝트 규약에 맞춘다.

    - 컬럼명 소문자 open/high/low/close/volume
    - MultiIndex 컬럼(단일 티커인데도 붙는 경우가 있다) 평탄화
    - DatetimeIndex 오름차순, tz 제거(일봉은 날짜만 의미가 있다)
    - 종가가 없는 행 제거
    """
    if raw is None or len(raw) == 0:
        raise InvalidTickerError(f"{ticker}: 조회 결과가 비어 있다")

    df = raw.copy()

    if isinstance(df.columns, pd.MultiIndex):
        levels = [lvl for lvl in range(df.columns.nlevels) if len(df.columns.levels[lvl]) > 1]
        df.columns = df.columns.get_level_values(levels[0] if levels else 0)

    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise DataFetchError(f"{ticker}: 필수 컬럼 누락 {missing} (받은 컬럼: {list(df.columns)})")

    df = df.loc[:, list(OHLCV_COLUMNS)]

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df.index.name = "date"

    df = df[df["close"].notna()]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    return df.astype(float)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------


def cache_path(ticker: str, config: DataConfig) -> Path:
    """티커별 parquet 캐시 경로. 접미사의 '.'은 파일명에서 '_'로 바꾼다."""
    safe = ticker.upper().replace(".", "_").replace("/", "_")
    return Path(config.cache_dir) / f"{safe}.parquet"


def _meta_path(ticker: str, config: DataConfig) -> Path:
    return cache_path(ticker, config).with_suffix(".json")


def _cache_is_valid(
    meta: dict,
    config: DataConfig,
    *,
    today_local: date,
    session_complete_now: bool,
) -> bool:
    """캐시 무효화 조건 (하나라도 걸리면 재수집).

    1. 캐시 포맷 버전이 다르다
    2. 수집 파라미터(history_years / interval / auto_adjust)가 달라졌다
    3. 마지막 수집일(거래소 현지 날짜)이 오늘이 아니다
    4. 캐시가 '장중 미완성 봉' 상태로 저장됐는데 지금은 장이 끝났다
       -> 이게 없으면 오전에 받은 부분 거래량이 종일 고정된다
    """
    if meta.get("format_version") != _CACHE_FORMAT_VERSION:
        return False
    if meta.get("history_years") != config.history_years:
        return False
    if meta.get("interval") != config.interval:
        return False
    if meta.get("auto_adjust") != config.auto_adjust:
        return False
    if meta.get("fetched_on_local") != today_local.isoformat():
        return False
    if not meta.get("bar_complete_at_fetch", False) and session_complete_now:
        return False
    return True


def read_cache(
    ticker: str,
    config: DataConfig,
    *,
    today_local: date,
    session_complete_now: bool,
) -> pd.DataFrame | None:
    """유효한 캐시가 있으면 DataFrame, 없으면 None. 예외를 던지지 않는다."""
    parquet, meta_file = cache_path(ticker, config), _meta_path(ticker, config)
    if not parquet.exists() or not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _cache_is_valid(
        meta, config, today_local=today_local, session_complete_now=session_complete_now
    ):
        return None
    try:
        return pd.read_parquet(parquet)
    except (OSError, ValueError):
        return None


def write_cache(
    ticker: str,
    df: pd.DataFrame,
    config: DataConfig,
    *,
    today_local: date,
    bar_complete: bool,
) -> None:
    """parquet + 사이드카 JSON 메타로 저장."""
    parquet = cache_path(ticker, config)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet)
    _meta_path(ticker, config).write_text(
        json.dumps(
            {
                "format_version": _CACHE_FORMAT_VERSION,
                "ticker": ticker.upper(),
                "history_years": config.history_years,
                "interval": config.interval,
                "auto_adjust": config.auto_adjust,
                "fetched_on_local": today_local.isoformat(),
                "fetched_at_utc": datetime.now(UTC).isoformat(),
                "bar_complete_at_fetch": bar_complete,
                "bars": len(df),
                "last_bar_date": df.index[-1].date().isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------


def fetch_ohlcv(ticker: str, config: DataConfig) -> pd.DataFrame:
    """yfinance에서 history_years 만큼 일봉을 받아 정규화한다. 캐시를 보지 않는다.

    이 함수만이 네트워크를 만진다. yfinance 예외는 전부 DataFetchError로 감싼다.
    """
    import yfinance as yf

    end = datetime.now(UTC).date() + timedelta(days=1)
    start = end - timedelta(days=int(config.history_years * 365.25) + 7)

    try:
        raw = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval=config.interval,
            auto_adjust=config.auto_adjust,
            progress=False,
            actions=False,
            threads=False,
        )
    except InvalidTickerError:
        raise
    except Exception as exc:  # yfinance는 예외 타입을 보장하지 않는다
        raise DataFetchError(
            f"{ticker}: 데이터 수집 실패 ({exc.__class__.__name__}: {exc})"
        ) from exc

    return normalize_ohlcv(raw, ticker)


def _history_warnings(
    df: pd.DataFrame, ticker: str, config: DataConfig
) -> list[DiagnosticWarning]:
    """상장 기간 / 최신성 관련 경고. 예외가 아니라 경고인 것이 요점이다."""
    warnings: list[DiagnosticWarning] = []

    if len(df) < config.warn_below_bars:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.INSUFFICIENT_HISTORY,
                severity=Severity.WARN,
                message=(
                    f"{ticker}: 사용 가능한 봉이 {len(df)}개로 "
                    f"{config.warn_below_bars}개 미만이다. 장기 이동평균은 None이 되고 "
                    "이를 쓰는 조건은 UNAVAILABLE로 처리된다"
                ),
                field="indicators.sma200",
            )
        )

    stale_days = (datetime.now(UTC).date() - df.index[-1].date()).days
    if stale_days > config.stale_data_max_days:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.STALE_DATA,
                severity=Severity.WARN,
                message=(
                    f"{ticker}: 마지막 봉이 {df.index[-1].date().isoformat()}로 "
                    f"{stale_days}일 전이다. 상장폐지·거래정지 가능성을 확인할 것"
                ),
                field="bar_meta.last_bar_date",
            )
        )

    return warnings


def load_ohlcv(
    ticker: str,
    config: DataConfig,
    exchanges: ExchangeConfig,
    *,
    use_cache: bool = True,
    now: datetime | None = None,
) -> OhlcvBundle:
    """캐시 -> (미스 시) 네트워크 순으로 OHLCV를 확보하고 BarMeta·경고까지 채운다.

    상위 레이어가 부르는 유일한 진입점이다.
    """
    now = now or datetime.now(UTC)
    session = resolve_session(ticker, exchanges)

    if session is None:
        today_local, session_complete_now = now.date(), False
    else:
        local_now = now.astimezone(ZoneInfo(session.timezone))
        today_local = local_now.date()
        session_complete_now = (
            _session_state(local_now, session, exchanges.settle_buffer_minutes)
            is SessionState.CLOSED
        )

    df = None
    from_cache = False
    if use_cache:
        df = read_cache(
            ticker,
            config,
            today_local=today_local,
            session_complete_now=session_complete_now,
        )
        from_cache = df is not None

    if df is None:
        df = fetch_ohlcv(ticker, config)

    if df.empty:
        raise InvalidTickerError(f"{ticker}: 유효한 봉이 없다 (상장폐지 또는 잘못된 티커)")
    if len(df) < config.min_bars_absolute:
        raise InsufficientDataError(
            f"{ticker}: 봉이 {len(df)}개뿐이라 어떤 지표도 계산할 수 없다 "
            f"(최소 {config.min_bars_absolute}개 필요)"
        )

    bar_meta, meta_warnings = build_bar_meta(df, ticker, exchanges, now=now)

    if not from_cache and use_cache:
        write_cache(
            ticker,
            df,
            config,
            today_local=today_local,
            bar_complete=bar_meta.is_bar_complete,
        )

    warnings = tuple(_history_warnings(df, ticker, config) + meta_warnings)
    return OhlcvBundle(
        ticker=ticker.upper(),
        ohlcv=df,
        bar_meta=bar_meta,
        warnings=warnings,
        from_cache=from_cache,
    )
