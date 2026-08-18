"""RS 백분위 계산용 유니버스.

RS 백분위는 '같은 시점의 다른 종목들과 비교한 순위'이므로 단일 종목만으로는 낼 수 없다.
유니버스가 없으면 rs_percentile은 None이 되고, RS_UNIVERSE_MISSING 경고가 붙는다.
"""

from __future__ import annotations

import pandas as pd

from config import UniverseConfig


def load_universe_tickers(config: UniverseConfig) -> list[str]:
    """유니버스 구성 종목 목록."""
    raise NotImplementedError


def compute_rs_scores(closes: pd.DataFrame, config: UniverseConfig) -> pd.Series:
    """가중 수익률 기반 RS 원점수. 인덱스 = 티커.

    closes: 컬럼 = 티커, 인덱스 = 날짜인 종가 행렬.
    """
    raise NotImplementedError


def rs_percentile(ticker: str, scores: pd.Series) -> float | None:
    """유니버스 내 백분위 0~100. 유니버스가 min_universe_size 미만이면 None."""
    raise NotImplementedError
