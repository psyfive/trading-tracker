"""지표 직접 구현. 외부 TA 라이브러리 사용 금지 (CLAUDE.md 원칙 5).

모든 함수는 입력 Series와 **같은 길이**의 Series를 반환한다. 워밍업 구간은 NaN이다.
잘라서 반환하면 인덱스 정렬이 깨지므로 금지.

RSI와 ATR은 반드시 Wilder smoothing을 쓴다. pandas ewm(span=...)이 아니다.
각 함수에는 손계산 기대값을 박은 유닛테스트가 tests/test_indicators_core.py에 붙는다.
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """단순이동평균. 앞 period-1개는 NaN."""
    raise NotImplementedError


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균. alpha = 2/(period+1), 첫 값은 SMA(period) seed."""
    raise NotImplementedError


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing (RMA).

    seed = 첫 period개의 단순평균.
    이후 = (prev * (period - 1) + current) / period.

    RSI와 ATR이 공통으로 쓴다. alpha = 1/period 인 EMA와 수식은 같지만
    seed 방식이 다르므로 별도 함수로 둔다.
    """
    raise NotImplementedError


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI.

    gain/loss를 wilder_smooth로 평활한 뒤 RS = avg_gain / avg_loss,
    RSI = 100 - 100/(1+RS). avg_loss == 0이면 RSI = 100.
    """
    raise NotImplementedError


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range = max(H-L, |H - C_prev|, |L - C_prev|). 첫 봉은 H-L."""
    raise NotImplementedError


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR = wilder_smooth(true_range, period)."""
    raise NotImplementedError


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """표준 MACD. 반환: (macd_line, signal_line, histogram)."""
    raise NotImplementedError


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0, ddof: int = 0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """볼린저 밴드. 반환: (upper, mid, lower). ddof=0 = 모집단 표준편차."""
    raise NotImplementedError


def adr_pct(high: pd.Series, low: pd.Series, period: int = 20) -> pd.Series:
    """평균 일간 레인지 %. mean(high/low) 기반 (Qullamaggie 방식)."""
    raise NotImplementedError


def slope_pct(series: pd.Series, lookback: int) -> pd.Series:
    """lookback 봉 전 대비 % 변화. 이동평균의 방향 판정용."""
    raise NotImplementedError


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    """기간 내 최고값."""
    raise NotImplementedError


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    """기간 내 최저값."""
    raise NotImplementedError
