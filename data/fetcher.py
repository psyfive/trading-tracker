"""yfinance OHLCV 수집 + parquet 캐시.

네트워크 호출은 이 레이어에서만 일어난다 (CLAUDE.md 원칙: 지표/전략은 네트워크 금지).
yfinance의 raw 예외는 여기서 도메인 예외로 변환한다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from config import DataConfig
from core.types import BarMeta


class DataError(Exception):
    """데이터 레이어 공통 예외 베이스."""


class TickerNotFoundError(DataError):
    """티커가 존재하지 않거나 상장폐지."""


class InsufficientHistoryError(DataError):
    """지표 산출에 필요한 최소 봉 수 미달."""


def fetch_ohlcv(ticker: str, config: DataConfig, *, use_cache: bool = True) -> pd.DataFrame:
    """일봉 OHLCV를 config.history_years 만큼 가져온다.

    반환 DataFrame: DatetimeIndex 오름차순, 컬럼 [open, high, low, close, volume] 소문자.
    분할/배당 조정된 가격을 쓴다.
    """
    raise NotImplementedError


def build_bar_meta(df: pd.DataFrame, config: DataConfig, *, now: date | None = None) -> BarMeta:
    """마지막 봉의 완성 여부를 판정한다.

    거래소 시간대 기준으로 정규장 종료 이후여야 is_bar_complete = True.
    장중이면 False이고, 호출자는 반드시 INCOMPLETE_BAR 경고를 붙여야 한다.
    """
    raise NotImplementedError


def cache_path(ticker: str, config: DataConfig) -> str:
    """티커별 parquet 캐시 경로."""
    raise NotImplementedError


def read_cache(ticker: str, config: DataConfig) -> pd.DataFrame | None:
    """TTL 이내면 캐시 반환, 아니면 None."""
    raise NotImplementedError


def write_cache(ticker: str, df: pd.DataFrame, config: DataConfig) -> None:
    """parquet으로 저장."""
    raise NotImplementedError
