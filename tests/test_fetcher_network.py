"""실제 yfinance를 타는 테스트. 기본 실행에서 제외된다.

    python -m pytest tests/test_fetcher_network.py --run-network

이 파일만이 네트워크를 만진다. 나머지 테스트는 conftest의 _block_network가
yfinance 호출을 예외로 만들어 구조적으로 차단한다.

TLS 인터셉션 환경(사내 프록시 등)에서는 SSL_CERT_FILE에 사내 루트 CA를 포함한
번들 경로를 지정해야 통과한다.
"""

from __future__ import annotations

import pytest

from config import DEFAULT_CONFIG
from data.fetcher import DataError, fetch_ohlcv

pytestmark = pytest.mark.network


def test_fetch_real_us_ticker():
    df = fetch_ohlcv("AAPL", DEFAULT_CONFIG.data)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) > 600
    assert df.index.is_monotonic_increasing
    assert df.index.tz is None


def test_fetch_real_krx_ticker():
    df = fetch_ohlcv("005930.KS", DEFAULT_CONFIG.data)
    assert len(df) > 600
    assert (df["close"] > 0).all()


def test_bogus_ticker_raises_domain_error_not_raw_yfinance():
    """yfinance의 raw 예외가 상위로 올라오면 안 된다."""
    with pytest.raises(DataError):
        fetch_ohlcv("ZZZZNOTAREALTICKER", DEFAULT_CONFIG.data)
