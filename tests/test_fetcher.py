"""데이터 레이어 테스트. 네트워크를 타지 않는다.

conftest의 _block_network가 yfinance 호출을 예외로 만들기 때문에, 여기서 통과한다는 것은
곧 '네트워크 없이 동작한다'는 뜻이다. 실제 수집 경로는 test_fetcher_network.py로 분리했다.

봉 완성 여부 판정이 이 파일의 핵심이다. 여기가 틀리면 장중에 받은 부분 거래량으로
'거래량 부족' 오진이 나온다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from config import DataConfig, ExchangeConfig
from core.types import SessionState, WarningCode
from data.fetcher import (
    DataFetchError,
    InsufficientDataError,
    InvalidTickerError,
    build_bar_meta,
    cache_path,
    load_ohlcv,
    normalize_ohlcv,
    read_cache,
    resolve_session,
    write_cache,
)

EXCHANGES = ExchangeConfig()


def frame(dates: list[str], close: float = 100.0) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="date")
    return pd.DataFrame(
        {
            "open": [close] * len(dates),
            "high": [close * 1.01] * len(dates),
            "low": [close * 0.99] * len(dates),
            "close": [close] * len(dates),
            "volume": [1_000_000.0] * len(dates),
        },
        index=index,
    )


def et(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def kst(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))


# ===========================================================================
# 거래소 판정
# ===========================================================================


@pytest.mark.parametrize(
    ("ticker", "exchange"),
    [("AAPL", "US"), ("aapl", "US"), ("BRK-B", "US"), ("005930.KS", "KRX"), ("247540.KQ", "KRX")],
)
def test_resolve_session_known_exchanges(ticker, exchange):
    session = resolve_session(ticker, EXCHANGES)
    assert session is not None and session.exchange == exchange


@pytest.mark.parametrize("ticker", ["7203.T", "VOD.L", "0700.HK"])
def test_resolve_session_unknown_suffix_is_none(ticker):
    """모르는 거래소는 추측하지 않는다. None이면 호출부가 보수적으로 처리한다."""
    assert resolve_session(ticker, EXCHANGES) is None


# ===========================================================================
# 봉 완성 여부 — 미국
# ===========================================================================


def test_us_intraday_bar_is_incomplete():
    """2026-08-18(화) 13:42 ET, 당일 봉 존재 -> 미완성."""
    meta, warnings = build_bar_meta(
        frame(["2026-08-17", "2026-08-18"]), "AAPL", EXCHANGES, now=et(2026, 8, 18, 13, 42)
    )
    assert meta.is_bar_complete is False
    assert meta.session_state is SessionState.OPEN
    assert meta.volume_judgements_reliable is False
    assert [w.code for w in warnings] == [WarningCode.INCOMPLETE_BAR]


def test_us_bar_is_complete_after_close_plus_settle_buffer():
    """16:00 마감 + 15분 버퍼 -> 16:15 이후 완성."""
    meta, warnings = build_bar_meta(
        frame(["2026-08-17", "2026-08-18"]), "AAPL", EXCHANGES, now=et(2026, 8, 18, 16, 20)
    )
    assert meta.is_bar_complete is True
    assert meta.session_state is SessionState.CLOSED
    assert warnings == []


def test_us_bar_inside_settle_buffer_is_still_incomplete():
    """16:05는 장은 끝났지만 종가 확정 대기 구간이다."""
    meta, _ = build_bar_meta(
        frame(["2026-08-18"] * 25), "AAPL", EXCHANGES, now=et(2026, 8, 18, 16, 5)
    )
    assert meta.session_state is SessionState.POST_MARKET
    assert meta.is_bar_complete is False


def test_us_pre_market_bar_of_today_is_incomplete():
    meta, _ = build_bar_meta(
        frame(["2026-08-18"] * 25), "AAPL", EXCHANGES, now=et(2026, 8, 18, 8, 0)
    )
    assert meta.session_state is SessionState.PRE_MARKET
    assert meta.is_bar_complete is False


def test_previous_session_bar_is_complete_even_during_market_hours():
    """마지막 봉이 어제 것이면 장중이어도 그 봉 자체는 확정이다."""
    meta, warnings = build_bar_meta(
        frame(["2026-08-14", "2026-08-17"]), "AAPL", EXCHANGES, now=et(2026, 8, 18, 11, 0)
    )
    assert meta.is_bar_complete is True
    assert meta.session_state is SessionState.OPEN
    assert warnings == []


def test_weekend_is_closed():
    meta, _ = build_bar_meta(
        frame(["2026-08-14"]), "AAPL", EXCHANGES, now=et(2026, 8, 15, 12, 0)
    )
    assert meta.session_state is SessionState.CLOSED
    assert meta.is_bar_complete is True


# ===========================================================================
# 봉 완성 여부 — KRX (마감 15:30 KST)
# ===========================================================================


def test_krx_bar_is_incomplete_at_15_00_kst():
    meta, warnings = build_bar_meta(
        frame(["2026-08-18"] * 25), "005930.KS", EXCHANGES, now=kst(2026, 8, 18, 15, 0)
    )
    assert meta.is_bar_complete is False
    assert meta.session_state is SessionState.OPEN
    assert meta.exchange_tz == "Asia/Seoul"
    assert warnings[0].code is WarningCode.INCOMPLETE_BAR


def test_krx_bar_is_complete_at_16_00_kst():
    meta, _ = build_bar_meta(
        frame(["2026-08-18"] * 25), "005930.KS", EXCHANGES, now=kst(2026, 8, 18, 16, 0)
    )
    assert meta.is_bar_complete is True
    assert meta.session_state is SessionState.CLOSED


def test_krx_and_us_disagree_at_the_same_instant():
    """같은 순간에 한국은 마감, 미국은 개장 전. 타임존 처리가 실제로 되는지 확인."""
    moment = kst(2026, 8, 18, 16, 0)  # = 03:00 ET
    krx, _ = build_bar_meta(frame(["2026-08-18"] * 25), "005930.KS", EXCHANGES, now=moment)
    us, _ = build_bar_meta(frame(["2026-08-18"] * 25), "AAPL", EXCHANGES, now=moment)
    assert krx.is_bar_complete is True
    assert us.is_bar_complete is False
    assert us.session_state is SessionState.PRE_MARKET


# ===========================================================================
# 판정 불가 거래소 — 보수적으로 미완성
# ===========================================================================


def test_unknown_exchange_is_conservatively_incomplete():
    """모르는 거래소의 봉을 완성됐다고 단정하면 거래량 오진으로 이어진다."""
    meta, warnings = build_bar_meta(
        frame(["2026-08-18"] * 25), "7203.T", EXCHANGES, now=et(2026, 8, 18, 20, 0)
    )
    assert meta.is_bar_complete is False
    assert meta.session_state is SessionState.UNKNOWN
    assert meta.volume_judgements_reliable is False
    assert warnings[0].code is WarningCode.INCOMPLETE_BAR
    assert "판정할 수 없어" in warnings[0].message


def test_settle_buffer_is_configurable():
    """마감 버퍼가 config에서 온다. 하드코딩이면 이 테스트가 깨진다."""
    no_buffer = replace(EXCHANGES, settle_buffer_minutes=0)
    meta, _ = build_bar_meta(
        frame(["2026-08-18"] * 25), "AAPL", no_buffer, now=et(2026, 8, 18, 16, 1)
    )
    assert meta.is_bar_complete is True


# ===========================================================================
# 정규화
# ===========================================================================


def test_normalize_flattens_multiindex_columns():
    """yfinance는 단일 티커에도 (필드, 티커) MultiIndex를 붙일 때가 있다."""
    index = pd.DatetimeIndex(["2026-08-17", "2026-08-18"])
    raw = pd.DataFrame(
        [[1.0, 2.0, 0.5, 1.5, 100.0], [2.0, 3.0, 1.5, 2.5, 200.0]],
        index=index,
        columns=pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], ["AAPL"]]
        ),
    )
    result = normalize_ohlcv(raw, "AAPL")
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_sorts_ascending_and_strips_timezone():
    index = pd.DatetimeIndex(["2026-08-18", "2026-08-17"], tz="America/New_York")
    raw = pd.DataFrame(
        {"Open": [2.0, 1.0], "High": [2.0, 1.0], "Low": [2.0, 1.0],
         "Close": [2.0, 1.0], "Volume": [2.0, 1.0]},
        index=index,
    )
    result = normalize_ohlcv(raw, "AAPL")
    assert result.index.tz is None
    assert result.index.is_monotonic_increasing
    assert result["close"].tolist() == [1.0, 2.0]


def test_normalize_drops_rows_without_close():
    raw = pd.DataFrame(
        {"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
         "Close": [1.0, float("nan")], "Volume": [1.0, 2.0]},
        index=pd.DatetimeIndex(["2026-08-17", "2026-08-18"]),
    )
    assert len(normalize_ohlcv(raw, "AAPL")) == 1


def test_normalize_empty_result_is_invalid_ticker():
    with pytest.raises(InvalidTickerError):
        normalize_ohlcv(pd.DataFrame(), "NOSUCH")


def test_normalize_missing_columns_is_a_fetch_error():
    raw = pd.DataFrame({"Open": [1.0], "Close": [1.0]}, index=pd.DatetimeIndex(["2026-08-18"]))
    with pytest.raises(DataFetchError, match="필수 컬럼 누락"):
        normalize_ohlcv(raw, "AAPL")


# ===========================================================================
# 캐시
# ===========================================================================


@pytest.fixture
def cache_config(tmp_path) -> DataConfig:
    return DataConfig(cache_dir=str(tmp_path / "cache"))


def test_cache_hit_same_day(cache_config):
    df = frame(["2026-08-17", "2026-08-18"] * 15)
    write_cache("AAPL", df, cache_config, today_local=df.index[-1].date(), bar_complete=True)
    cached = read_cache(
        "AAPL", cache_config, today_local=df.index[-1].date(), session_complete_now=True
    )
    assert cached is not None and len(cached) == len(df)


def test_cache_miss_on_different_day(cache_config):
    df = frame(["2026-08-18"] * 25)
    write_cache("AAPL", df, cache_config, today_local=df.index[-1].date(), bar_complete=True)
    from datetime import date

    assert (
        read_cache("AAPL", cache_config, today_local=date(2026, 8, 19), session_complete_now=True)
        is None
    )


def test_cache_miss_when_history_years_changed(cache_config):
    df = frame(["2026-08-18"] * 25)
    write_cache("AAPL", df, cache_config, today_local=df.index[-1].date(), bar_complete=True)
    changed = replace(cache_config, history_years=5)
    assert (
        read_cache("AAPL", changed, today_local=df.index[-1].date(), session_complete_now=True)
        is None
    )


def test_cache_miss_when_partial_bar_cached_and_session_has_since_closed(cache_config):
    """장중에 받은 부분 거래량이 종일 고정되면 안 된다."""
    df = frame(["2026-08-18"] * 25)
    write_cache("AAPL", df, cache_config, today_local=df.index[-1].date(), bar_complete=False)
    assert (
        read_cache("AAPL", cache_config, today_local=df.index[-1].date(), session_complete_now=True)
        is None
    )
    assert (
        read_cache(
            "AAPL", cache_config, today_local=df.index[-1].date(), session_complete_now=False
        )
        is not None
    )


def test_cache_miss_on_corrupt_sidecar_does_not_raise(cache_config):
    df = frame(["2026-08-18"] * 25)
    write_cache("AAPL", df, cache_config, today_local=df.index[-1].date(), bar_complete=True)
    cache_path("AAPL", cache_config).with_suffix(".json").write_text("{ not json", encoding="utf-8")
    assert (
        read_cache("AAPL", cache_config, today_local=df.index[-1].date(), session_complete_now=True)
        is None
    )


def test_cache_filename_is_filesystem_safe(cache_config):
    assert "." not in cache_path("005930.KS", cache_config).stem


# ===========================================================================
# load_ohlcv — 캐시 재사용으로 네트워크를 타지 않는다
# ===========================================================================


def test_load_ohlcv_uses_cache_on_second_call(monkeypatch, cache_config):
    """두 번째 호출은 네트워크에 닿지 않아야 한다. 닿으면 여기서 터진다."""
    df = frame(["2026-08-17"] * 30)
    calls = []

    def fake_fetch(ticker, config):
        calls.append(ticker)
        return df

    monkeypatch.setattr("data.fetcher.fetch_ohlcv", fake_fetch)
    now = et(2026, 8, 17, 18, 0)

    first = load_ohlcv("AAPL", cache_config, EXCHANGES, now=now)
    second = load_ohlcv("AAPL", cache_config, EXCHANGES, now=now)

    assert calls == ["AAPL"], "두 번째 호출이 네트워크를 탔다"
    assert first.from_cache is False
    assert second.from_cache is True


def test_load_ohlcv_raises_insufficient_data_below_absolute_minimum(monkeypatch, cache_config):
    monkeypatch.setattr("data.fetcher.fetch_ohlcv", lambda t, c: frame(["2026-08-17"] * 5))
    with pytest.raises(InsufficientDataError, match="계산할 수 없다"):
        load_ohlcv("AAPL", cache_config, EXCHANGES, now=et(2026, 8, 17, 18, 0))


def test_short_listing_history_warns_instead_of_raising(monkeypatch, cache_config):
    """상장 6개월 종목은 예외가 아니라 경고다. 여기서 예외를 던지면 신규주가 사라진다."""
    dates = pd.bdate_range("2026-03-02", periods=120).strftime("%Y-%m-%d").tolist()
    monkeypatch.setattr("data.fetcher.fetch_ohlcv", lambda t, c: frame(dates))

    bundle = load_ohlcv("AAPL", cache_config, EXCHANGES, now=et(2026, 8, 17, 18, 0))

    codes = [w.code for w in bundle.warnings]
    assert WarningCode.INSUFFICIENT_HISTORY in codes
    assert len(bundle.ohlcv) == 120


def test_stale_data_warns(monkeypatch, cache_config):
    monkeypatch.setattr("data.fetcher.fetch_ohlcv", lambda t, c: frame(["2026-01-05"] * 30))
    bundle = load_ohlcv("AAPL", cache_config, EXCHANGES, now=et(2026, 8, 17, 18, 0))
    assert WarningCode.STALE_DATA in [w.code for w in bundle.warnings]


def test_load_ohlcv_bundle_carries_warnings_with_the_data(monkeypatch, cache_config):
    """경고가 데이터와 함께 다녀야 상위에서 잃어버리지 않는다."""
    monkeypatch.setattr("data.fetcher.fetch_ohlcv", lambda t, c: frame(["2026-08-18"] * 30))
    bundle = load_ohlcv("AAPL", cache_config, EXCHANGES, now=et(2026, 8, 18, 11, 0))
    assert bundle.bar_meta.is_bar_complete is False
    assert WarningCode.INCOMPLETE_BAR in [w.code for w in bundle.warnings]


# ===========================================================================
# 고정 픽스처 — 실제 데이터 형태로 동작하는지
# ===========================================================================


def test_us_fixture_shape(aapl):
    assert list(aapl.columns) == ["open", "high", "low", "close", "volume"]
    assert aapl.index.is_monotonic_increasing
    assert len(aapl) > 700


def test_krx_fixture_shape(samsung):
    assert list(samsung.columns) == ["open", "high", "low", "close", "volume"]
    assert samsung.index.is_monotonic_increasing
    assert len(samsung) > 700


@pytest.mark.parametrize("ticker", ["AAPL", "005930.KS"])
def test_fixture_bar_meta_builds_for_both_exchanges(ticker, aapl, samsung):
    df = aapl if ticker == "AAPL" else samsung
    meta, _ = build_bar_meta(df, ticker, EXCHANGES, now=datetime(2026, 8, 25, 12, 0, tzinfo=UTC))
    assert meta.bars_available == len(df)
    assert meta.is_bar_complete is True
