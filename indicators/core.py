"""지표 직접 구현. 외부 TA 라이브러리 사용 금지 (CLAUDE.md 원칙 5).

계약:
  - 모든 함수는 입력 Series와 **같은 길이**의 Series를 반환한다. 워밍업 구간은 NaN이다.
    잘라서 반환하면 인덱스 정렬이 깨지므로 금지.
  - 데이터가 period보다 짧으면 예외가 아니라 전부 NaN을 반환한다.
    (상장 6개월 종목의 SMA200은 '에러'가 아니라 '아직 없음'이다.)
  - period <= 0 은 데이터 문제가 아니라 호출 버그이므로 ValueError.

RSI와 ATR은 반드시 Wilder smoothing을 쓴다. pandas ewm(span=...)이 아니다.
이 두 지표는 라이브러리마다 값이 갈리는 지점이므로 seed 처리를 테스트로 못 박아 둔다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _check_period(period: int) -> None:
    if period <= 0:
        raise ValueError(f"period는 1 이상이어야 한다: {period}")


def _recursive_average(series: pd.Series, period: int, alpha: float) -> pd.Series:
    """seed = 첫 period개 유효값의 단순평균, 이후 prev*(1-alpha) + cur*alpha.

    선행 NaN은 건너뛴다 (MACD 시그널선처럼 이미 NaN이 앞에 붙은 입력을 받기 때문).
    EMA와 Wilder smoothing이 alpha만 다르고 구조는 같아서 공유한다.
    """
    values = series.to_numpy(dtype=float)
    out = np.full(values.shape, np.nan)
    valid = ~np.isnan(values)

    if not valid.any():
        return pd.Series(out, index=series.index, name=series.name)

    start = int(np.argmax(valid))
    if len(values) - start < period:
        return pd.Series(out, index=series.index, name=series.name)

    seed_end = start + period
    prev = float(np.mean(values[start:seed_end]))
    out[seed_end - 1] = prev

    for i in range(seed_end, len(values)):
        current = values[i]
        if np.isnan(current):
            out[i] = prev
            continue
        prev = current * alpha + prev * (1.0 - alpha)
        out[i] = prev

    return pd.Series(out, index=series.index, name=series.name)


def sma(series: pd.Series, period: int) -> pd.Series:
    """단순이동평균. 앞 period-1개는 NaN."""
    _check_period(period)
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균. alpha = 2/(period+1), seed는 SMA(period).

    pandas ewm(adjust=True)와 다르다. ewm은 첫 값부터 가중 평균을 내지만
    여기서는 SMA seed 이전 구간을 NaN으로 둔다 (워밍업 미완료를 값으로 위장하지 않는다).
    """
    _check_period(period)
    return _recursive_average(series, period, alpha=2.0 / (period + 1.0))


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing (RMA).

    seed = 첫 period개 유효값의 단순평균.
    이후 = (prev * (period - 1) + current) / period  ==  prev*(1-1/period) + current*(1/period).

    RSI와 ATR이 공통으로 쓴다. alpha = 1/period 인 EMA와 점화식은 같지만
    의미가 다르므로(그리고 seed 기준일이 다르므로) 별도 이름을 유지한다.
    """
    _check_period(period)
    return _recursive_average(series, period, alpha=1.0 / period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI.

    RSI = 100 * avg_gain / (avg_gain + avg_loss).
    100 - 100/(1+RS) 와 대수적으로 같지만 avg_loss == 0 일 때 0으로 나누지 않는다.

    상승도 하락도 없는 완전 횡보 구간(avg_gain == avg_loss == 0)은 수학적으로 정의되지 않는다.
    중립값 50.0으로 둔다. 이 규약이 바뀌면 테스트가 먼저 깨진다.
    """
    _check_period(period)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)

    total = avg_gain + avg_loss
    result = 100.0 * avg_gain / total
    flat = total.notna() & (total == 0.0)
    return result.mask(flat, 50.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range = max(H-L, |H - C_prev|, |L - C_prev|).

    첫 봉은 C_prev가 없으므로 H-L이다 (NaN 항은 max에서 제외된다).
    갭 상황에서 TR이 H-L보다 커지는 것이 이 지표의 존재 이유다.
    """
    prev_close = close.shift(1)
    candidates = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return candidates.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder ATR = wilder_smooth(true_range, period).

    TR은 첫 봉부터 값이 있으므로 첫 유효 ATR은 인덱스 period-1이다.
    """
    _check_period(period)
    return wilder_smooth(true_range(high, low, close), period)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """표준 MACD. 반환: (macd_line, signal_line, histogram).

    시그널선은 MACD선의 EMA다. MACD선 앞부분이 NaN이므로
    시그널선 seed는 '첫 유효 MACD값 기준' signal개의 평균이다.
    """
    _check_period(fast)
    _check_period(slow)
    _check_period(signal)
    if fast >= slow:
        raise ValueError(f"fast({fast})는 slow({slow})보다 작아야 한다")

    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0, ddof: int = 0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """볼린저 밴드. 반환: (upper, mid, lower).

    ddof=0 = 모집단 표준편차. pandas .std() 기본값은 ddof=1이므로 명시적으로 넘긴다.
    """
    _check_period(period)
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=ddof)
    return mid + num_std * std, mid, mid - num_std * std


def volume_sma(volume: pd.Series, period: int = 50) -> pd.Series:
    """거래량 이동평균. sma와 같은 계산이지만 호출부 가독성을 위해 이름을 둔다."""
    return sma(volume, period)


def adr_pct(high: pd.Series, low: pd.Series, period: int = 20) -> pd.Series:
    """평균 일간 레인지 %. mean(high/low) - 1 을 백분율로 (Qullamaggie 방식).

    (high-low)/close 평균이 아니다. 저가 대비 배수의 평균이라 갭에 덜 민감하다.
    """
    _check_period(period)
    ratio = (high / low).rolling(window=period, min_periods=period).mean()
    return (ratio - 1.0) * 100.0


def slope_pct(series: pd.Series, lookback: int) -> pd.Series:
    """lookback 봉 전 대비 % 변화. 이동평균의 방향 판정용.

    분모는 절댓값이다. 음수 값을 가지는 시리즈(MACD선 등)에 써도 부호가 뒤집히지 않는다.
    """
    _check_period(lookback)
    past = series.shift(lookback)
    return (series - past) / past.abs() * 100.0


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    """기간 내 최고값."""
    _check_period(period)
    return series.rolling(window=period, min_periods=period).max()


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    """기간 내 최저값."""
    _check_period(period)
    return series.rolling(window=period, min_periods=period).min()


def swing_high_flags(high: pd.Series, k: int) -> pd.Series:
    """프랙탈 스윙 고점 여부 (입력과 같은 길이의 bool Series).

    i봉의 고가가 i-k..i+k 구간의 최고가면 True다. 양옆으로 k봉이 확보되지 않는
    앞뒤 k개 구간은 판정 근거가 없으므로 False다.

    **미래 참조가 아니다.** i봉의 판정에 i+k봉이 들어가지만, 호출부가 넘기는 Series는
    이미 판정 시점까지 잘려 있으므로 True가 될 수 있는 위치는 i+k <= 마지막 봉인
    곳뿐이다. 즉 스윙 고점은 최소 k봉이 지나야 확정되며, 그래서 돌파 트리거로 쓸 수 있다
    (오늘 봉의 고가는 스윙 고점이 될 수 없으므로 '오늘 고가를 오늘 넘어야 하는' 모순이
    생기지 않는다).

    전략 3종이 각자 다른 k로 이 정의를 공유한다. 계산식이 하나뿐이므로 지표다.
    """
    _check_period(k)
    window = 2 * k + 1
    centered = high.rolling(window=window, center=True, min_periods=window).max()
    return high >= centered


def swing_low_flags(low: pd.Series, k: int) -> pd.Series:
    """프랙탈 스윙 저점 여부. swing_high_flags의 대칭."""
    _check_period(k)
    window = 2 * k + 1
    centered = low.rolling(window=window, center=True, min_periods=window).min()
    return low <= centered


def swing_positions(series: pd.Series, k: int, *, is_high: bool) -> list[int]:
    """스윙 극점의 **위치 목록**(0-based). 베이스/컨솔 구조 탐지의 공통 입력이다."""
    flags = swing_high_flags(series, k) if is_high else swing_low_flags(series, k)
    return [int(i) for i in np.flatnonzero(flags.to_numpy())]
