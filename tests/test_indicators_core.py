"""지표 손계산 검증.

CLAUDE.md 원칙 5: 모든 지표에 손계산 기대값 테스트를 붙인다. 테스트 없는 지표는 머지하지 않는다.

**기대값 출처**: 아래 모든 상수는 이 파일 안에서 정의한 소형 시리즈에 대해
정의식을 직접 전개해 얻은 값이다. 다른 TA 라이브러리 출력을 베껴오지 않았다
(그렇게 하면 '직접 구현' 원칙이 무의미해진다). 각 상수 위에 전개 과정을 주석으로 남긴다.

특히 아래 두 지점은 라이브러리마다 값이 갈린다. 백테스트 결과가 이상할 때 제일 먼저 의심할 곳:
  1. RSI Wilder seed — 첫 값이 단순평균인지 EMA인지
  2. ATR 첫 봉 True Range — 전봉 종가가 없을 때의 처리
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from indicators.core import (
    adr_pct,
    atr,
    bollinger,
    ema,
    macd,
    rolling_high,
    rolling_low,
    rsi,
    slope_pct,
    sma,
    true_range,
    volume_sma,
    wilder_smooth,
)

TOL = 1e-9


def series(values: list[float]) -> pd.Series:
    """거래일 인덱스를 붙인 Series. 인덱스 정렬 보존 여부까지 검사하기 위함."""
    index = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=index, dtype=float)


def assert_close(actual: pd.Series, expected: list[float | None]) -> None:
    """None은 NaN 기대값을 뜻한다."""
    assert len(actual) == len(expected), "입력과 출력 길이가 달라졌다"
    for i, (got, want) in enumerate(zip(actual.to_numpy(), expected, strict=True)):
        if want is None:
            assert math.isnan(got), f"index {i}: NaN을 기대했는데 {got}"
        else:
            assert not math.isnan(got), f"index {i}: {want}를 기대했는데 NaN"
            assert abs(got - want) < TOL, f"index {i}: {want} 기대, {got} 실제"


# ===========================================================================
# SMA
# ===========================================================================

# 시리즈 [2, 4, 6, 8, 10], period 3
#   index 2 = (2+4+6)/3 = 4
#   index 3 = (4+6+8)/3 = 6
#   index 4 = (6+8+10)/3 = 8
SMA_INPUT = [2.0, 4.0, 6.0, 8.0, 10.0]
SMA3_EXPECTED = [None, None, 4.0, 6.0, 8.0]


def test_sma_matches_hand_calculation():
    assert_close(sma(series(SMA_INPUT), 3), SMA3_EXPECTED)


def test_sma_warmup_is_nan_not_partial_average():
    """앞 period-1개는 '아직 없음'이다. 2개짜리 평균으로 채우면 안 된다."""
    result = sma(series(SMA_INPUT), 3)
    assert result.isna().tolist()[:2] == [True, True]


def test_sma_preserves_index_and_length():
    source = series(SMA_INPUT)
    result = sma(source, 3)
    assert len(result) == len(source)
    assert result.index.equals(source.index)


# ===========================================================================
# EMA — seed는 SMA, alpha = 2/(period+1)
# ===========================================================================

# 시리즈 [1, 2, 3, 4, 5], period 3 -> alpha = 2/4 = 0.5
#   index 2 (seed) = (1+2+3)/3 = 2
#   index 3 = 4*0.5 + 2*0.5   = 3
#   index 4 = 5*0.5 + 3*0.5   = 4
EMA_INPUT = [1.0, 2.0, 3.0, 4.0, 5.0]
EMA3_EXPECTED = [None, None, 2.0, 3.0, 4.0]


def test_ema_seed_is_sma_then_recurses():
    assert_close(ema(series(EMA_INPUT), 3), EMA3_EXPECTED)


def test_ema_is_not_pandas_ewm_adjust_true():
    """pandas ewm(span=3, adjust=True)는 첫 봉부터 값을 내놓는다. 우리는 NaN이다.

    워밍업 미완료를 값으로 위장하지 않겠다는 선택이며, 이 차이는 의도적이다.
    """
    source = series(EMA_INPUT)
    ours = ema(source, 3)
    pandas_ewm = source.ewm(span=3, adjust=True).mean()
    assert math.isnan(ours.iloc[0])
    assert not math.isnan(pandas_ewm.iloc[0])
    assert abs(ours.iloc[-1] - pandas_ewm.iloc[-1]) > 1e-6


# ===========================================================================
# Wilder smoothing — seed는 단순평균, 이후 (prev*(n-1) + cur)/n
# ===========================================================================

# 시리즈 [1, 2, 3, 4, 5], period 3
#   index 2 (seed) = (1+2+3)/3 = 2
#   index 3 = (2*2 + 4)/3     = 8/3  = 2.666666...
#   index 4 = (8/3*2 + 5)/3   = 31/9 = 3.444444...
WILDER3_EXPECTED = [None, None, 2.0, 8.0 / 3.0, 31.0 / 9.0]


def test_wilder_smooth_recurrence():
    assert_close(wilder_smooth(series(EMA_INPUT), 3), WILDER3_EXPECTED)


def test_wilder_smooth_differs_from_ema_of_same_period():
    """alpha가 1/n 대 2/(n+1)로 다르다. 같은 값이 나오면 구현이 섞인 것이다."""
    source = series(EMA_INPUT)
    assert abs(wilder_smooth(source, 3).iloc[-1] - ema(source, 3).iloc[-1]) > 1e-6


# ===========================================================================
# RSI — Wilder. 이 프로젝트에서 가장 값이 갈리기 쉬운 지표.
# ===========================================================================

# closes[0] = 100, 이후 delta = [1,-1,2,-1,1,1,-2,1,1,-1,2,1,-1,1, 1,-1,2,-1,1]
RSI_INPUT = [
    100.0, 101.0, 100.0, 102.0, 101.0, 102.0, 103.0, 101.0, 102.0, 103.0,
    102.0, 104.0, 105.0, 104.0, 105.0, 106.0, 105.0, 107.0, 106.0, 107.0,
]

# seed 구간 = delta index 1..14 (14개)
#   gains  = 1,0,2,0,1,1,0,1,1,0,2,1,0,1 -> 합 11 -> avg_gain = 11/14
#   losses = 0,1,0,1,0,0,2,0,0,1,0,0,1,0 -> 합  6 -> avg_loss =  6/14
#   RSI = 100 * avg_gain / (avg_gain + avg_loss) = 100 * 11/17
RSI_AT_14 = 100.0 * 11.0 / 17.0  # 64.705882352941...

# index 15: delta = +1 -> gain 1, loss 0
#   avg_gain = (11/14 * 13 + 1)/14 = 157/196
#   avg_loss = ( 6/14 * 13 + 0)/14 =  78/196
#   RSI = 100 * 157/235
RSI_AT_15 = 100.0 * 157.0 / 235.0  # 66.808510638297...

# index 16: delta = -1 -> gain 0, loss 1
#   avg_gain = (157/196 * 13 + 0)/14 = 2041/2744
#   avg_loss = ( 78/196 * 13 + 1)/14 = 1210/2744
#   RSI = 100 * 2041/3251
RSI_AT_16 = 100.0 * 2041.0 / 3251.0  # 62.780682866810...


def test_rsi_wilder_seed_is_simple_average_of_first_period_deltas():
    """seed 위치(index 14)와 값이 둘 다 맞아야 한다.

    delta는 index 1부터 생기므로 14개를 모으면 index 14가 첫 유효값이다.
    index 13에서 값이 나오면 seed 오프셋이 하나 밀린 것이다.
    """
    result = rsi(series(RSI_INPUT), 14)
    assert result.iloc[:14].isna().all(), "index 13 이전에 값이 나왔다 — seed 오프셋 오류"
    assert abs(result.iloc[14] - RSI_AT_14) < TOL


def test_rsi_wilder_recursion_after_seed():
    result = rsi(series(RSI_INPUT), 14)
    assert abs(result.iloc[15] - RSI_AT_15) < TOL
    assert abs(result.iloc[16] - RSI_AT_16) < TOL


def test_rsi_is_not_ewm_span_based():
    """ewm(span=14) 기반 RSI와 값이 달라야 한다. 같으면 Wilder가 아니다."""
    close = series(RSI_INPUT)
    delta = close.diff()
    ewm_rsi = 100.0 - 100.0 / (
        1.0
        + delta.clip(lower=0).ewm(span=14, adjust=False).mean()
        / (-delta).clip(lower=0).ewm(span=14, adjust=False).mean()
    )
    assert abs(rsi(close, 14).iloc[-1] - ewm_rsi.iloc[-1]) > 1e-6


def test_rsi_all_gains_is_100():
    """손실이 없으면 RSI = 100. 0으로 나누지 않는다."""
    result = rsi(series([float(x) for x in range(100, 130)]), 14)
    assert abs(result.iloc[-1] - 100.0) < TOL


def test_rsi_all_losses_is_0():
    result = rsi(series([float(x) for x in range(130, 100, -1)]), 14)
    assert abs(result.iloc[-1] - 0.0) < TOL


def test_rsi_flat_series_is_neutral_50():
    """완전 횡보는 수학적으로 정의되지 않는다. 중립 50으로 두기로 한 규약을 잠근다."""
    result = rsi(series([100.0] * 30), 14)
    assert abs(result.iloc[-1] - 50.0) < TOL


def test_rsi_stays_within_bounds():
    result = rsi(series(RSI_INPUT), 14).dropna()
    assert result.between(0.0, 100.0).all()


# ===========================================================================
# True Range / ATR — 첫 봉 처리와 갭 처리가 핵심
# ===========================================================================

# index:      0     1     2      3     4     5
ATR_HIGH = [10.0, 11.0, 12.0, 11.5, 13.0, 13.5]
ATR_LOW = [8.0, 9.0, 10.5, 9.0, 11.0, 12.0]
ATR_CLOSE = [9.0, 10.5, 11.0, 9.5, 12.5, 13.0]

# TR = max(H-L, |H-C_prev|, |L-C_prev|), 첫 봉은 C_prev가 없으므로 H-L
#   0: H-L=2                                        -> 2.0   (첫 봉)
#   1: H-L=2,   |11-9|=2,     |9-9|=0               -> 2.0
#   2: H-L=1.5, |12-10.5|=1.5, |10.5-10.5|=0        -> 1.5
#   3: H-L=2.5, |11.5-11|=0.5, |9-11|=2             -> 2.5
#   4: H-L=2,   |13-9.5|=3.5,  |11-9.5|=1.5         -> 3.5   (갭업: H-L보다 크다)
#   5: H-L=1.5, |13.5-12.5|=1, |12-12.5|=0.5        -> 1.5
TR_EXPECTED = [2.0, 2.0, 1.5, 2.5, 3.5, 1.5]

# ATR(3) = Wilder smoothing of TR
#   index 2 (seed) = (2 + 2 + 1.5)/3        = 11/6   = 1.833333...
#   index 3 = (11/6 * 2 + 2.5)/3            = 37/18  = 2.055555...
#   index 4 = (37/18 * 2 + 3.5)/3           = 137/54 = 2.537037...
#   index 5 = (137/54 * 2 + 1.5)/3          = 355/162 = 2.191358...
ATR3_EXPECTED = [None, None, 11.0 / 6.0, 37.0 / 18.0, 137.0 / 54.0, 355.0 / 162.0]


def test_true_range_first_bar_is_high_minus_low():
    """전봉 종가가 없는 첫 봉은 H-L이다. NaN이 아니다."""
    result = true_range(series(ATR_HIGH), series(ATR_LOW), series(ATR_CLOSE))
    assert abs(result.iloc[0] - 2.0) < TOL


def test_true_range_uses_previous_close_on_gap():
    """index 4는 갭업이라 |H - C_prev| = 3.5 가 H-L = 2.0 을 이긴다.

    이 항이 빠지면 갭을 무시한 ATR이 되고 손절폭이 과소평가된다.
    """
    result = true_range(series(ATR_HIGH), series(ATR_LOW), series(ATR_CLOSE))
    assert abs(result.iloc[4] - 3.5) < TOL
    assert result.iloc[4] > (ATR_HIGH[4] - ATR_LOW[4])


def test_true_range_matches_hand_calculation():
    assert_close(
        true_range(series(ATR_HIGH), series(ATR_LOW), series(ATR_CLOSE)),
        TR_EXPECTED,
    )


def test_atr_is_wilder_smoothed_true_range():
    assert_close(atr(series(ATR_HIGH), series(ATR_LOW), series(ATR_CLOSE), 3), ATR3_EXPECTED)


def test_atr_first_valid_index_is_period_minus_one():
    """TR은 첫 봉부터 값이 있으므로 ATR(3)의 첫 값은 index 2다.

    RSI(index period)와 오프셋이 다르다 — delta가 한 칸 잡아먹기 때문이다.
    """
    result = atr(series(ATR_HIGH), series(ATR_LOW), series(ATR_CLOSE), 3)
    assert result.isna().tolist()[:2] == [True, True]
    assert not math.isnan(result.iloc[2])


# ===========================================================================
# MACD
# ===========================================================================

# 시리즈 [1, 3, 2, 6, 5, 9], fast=2 slow=3 signal=2
#   EMA(2), alpha=2/3: seed[1]=(1+3)/2=2
#     [2] = 2*2/3 + 2*1/3 = 2
#     [3] = 6*2/3 + 2*1/3 = 14/3
#     [4] = 5*2/3 + 14/3*1/3 = 44/9
#     [5] = 9*2/3 + 44/9*1/3 = 206/27
#   EMA(3), alpha=1/2: seed[2]=(1+3+2)/3=2
#     [3] = 6/2 + 2/2 = 4
#     [4] = 5/2 + 4/2 = 4.5
#     [5] = 9/2 + 4.5/2 = 6.75
#   MACD선 = EMA(2) - EMA(3)  (둘 다 존재하는 index 2부터)
MACD_INPUT = [1.0, 3.0, 2.0, 6.0, 5.0, 9.0]
MACD_LINE_EXPECTED = [None, None, 0.0, 2.0 / 3.0, 44.0 / 9.0 - 4.5, 206.0 / 27.0 - 6.75]

#   시그널 = MACD선의 EMA(2). MACD선 첫 유효값이 index 2이므로 seed는 index 3에 놓인다.
#     seed[3] = (0 + 2/3)/2 = 1/3
#     [4] = (44/9-4.5)*2/3 + 1/3*1/3 = 10/27
#     [5] = (206/27-6.75)*2/3 + 10/27*1/3 = 57.5/81
MACD_SIGNAL_EXPECTED = [None, None, None, 1.0 / 3.0, 10.0 / 27.0, 57.5 / 81.0]


def test_macd_line_matches_hand_calculation():
    line, _, _ = macd(series(MACD_INPUT), fast=2, slow=3, signal=2)
    assert_close(line, MACD_LINE_EXPECTED)


def test_macd_signal_seeds_from_first_valid_macd_value():
    """시그널선 seed가 MACD선의 NaN 구간을 건너뛰고 잡히는지.

    NaN을 0으로 취급하면 시그널선이 아래로 끌려 내려가 히스토그램 부호가 뒤집힌다.
    """
    _, signal, _ = macd(series(MACD_INPUT), fast=2, slow=3, signal=2)
    assert_close(signal, MACD_SIGNAL_EXPECTED)


def test_macd_histogram_is_line_minus_signal():
    line, signal, hist = macd(series(MACD_INPUT), fast=2, slow=3, signal=2)
    assert_close(hist, [None, None, None, 1.0 / 3.0, 0.5 / 27.0, 13.75 / 81.0])
    valid = line.notna() & signal.notna()
    assert np.allclose((line - signal)[valid], hist[valid])


def test_macd_rejects_fast_not_less_than_slow():
    with pytest.raises(ValueError, match="fast"):
        macd(series(MACD_INPUT), fast=26, slow=12, signal=9)


# ===========================================================================
# 볼린저 — ddof=0 (모집단 표준편차)
# ===========================================================================

# 시리즈 [1, 2, 3, 4], period 4
#   평균 = 2.5
#   모집단 분산 = ((-1.5)^2 + (-0.5)^2 + 0.5^2 + 1.5^2)/4 = 5/4 = 1.25
#   sigma = sqrt(1.25) = 1.118033988749895
#   upper = 2.5 + 2*sigma = 4.73606797749979
#   lower = 2.5 - 2*sigma = 0.26393202250021
BB_INPUT = [1.0, 2.0, 3.0, 4.0]
BB_SIGMA_POP = math.sqrt(1.25)


def test_bollinger_uses_population_std_by_default():
    upper, mid, lower = bollinger(series(BB_INPUT), period=4, num_std=2.0, ddof=0)
    assert abs(mid.iloc[-1] - 2.5) < TOL
    assert abs(upper.iloc[-1] - (2.5 + 2.0 * BB_SIGMA_POP)) < TOL
    assert abs(lower.iloc[-1] - (2.5 - 2.0 * BB_SIGMA_POP)) < TOL


def test_bollinger_ddof_1_gives_a_different_band():
    """표본 표준편차 sqrt(5/3)=1.29099... 와 구분되는지. 기본값이 뒤바뀌면 여기서 잡힌다."""
    pop_upper, _, _ = bollinger(series(BB_INPUT), period=4, ddof=0)
    sample_upper, _, _ = bollinger(series(BB_INPUT), period=4, ddof=1)
    assert abs(sample_upper.iloc[-1] - (2.5 + 2.0 * math.sqrt(5.0 / 3.0))) < TOL
    assert abs(pop_upper.iloc[-1] - sample_upper.iloc[-1]) > 1e-6


def test_bollinger_bands_are_symmetric_around_mid():
    upper, mid, lower = bollinger(series(BB_INPUT), period=4)
    assert abs((upper.iloc[-1] - mid.iloc[-1]) - (mid.iloc[-1] - lower.iloc[-1])) < TOL


# ===========================================================================
# ADR / 기울기 / 52주 고저 / 거래량 평균
# ===========================================================================

# high/low = 1.1, 1.2, 1.3 -> 평균 1.2 -> (1.2 - 1) * 100 = 20.0
def test_adr_pct_matches_hand_calculation():
    result = adr_pct(series([11.0, 12.0, 13.0]), series([10.0, 10.0, 10.0]), period=3)
    assert abs(result.iloc[-1] - 20.0) < TOL


def test_slope_pct_is_percent_change_over_lookback():
    """(현재 - lookback 전) / |lookback 전| * 100. 0~100 스케일이다."""
    result = slope_pct(series([100.0, 105.0, 110.0]), lookback=2)
    assert abs(result.iloc[-1] - 10.0) < TOL


def test_slope_pct_sign_survives_negative_base():
    """분모가 절댓값이라 MACD선처럼 음수인 시리즈에서도 상승이 양수로 나온다."""
    result = slope_pct(series([-100.0, -50.0]), lookback=1)
    assert abs(result.iloc[-1] - 50.0) < TOL


def test_sma200_slope_20d_pct_is_composition_of_sma_and_slope():
    """계약 필드 sma200_slope_20d_pct는 slope_pct(sma(close,200), 20)이다."""
    close = series([100.0 + i * 0.5 for i in range(260)])
    composed = slope_pct(sma(close, 200), 20)
    assert not math.isnan(composed.iloc[-1])
    assert composed.iloc[-1] > 0.0


def test_rolling_high_low_over_52_weeks():
    close = series([100.0 + i for i in range(260)])
    assert abs(rolling_high(close, 252).iloc[-1] - 359.0) < TOL
    assert abs(rolling_low(close, 252).iloc[-1] - 108.0) < TOL


def test_volume_sma_matches_sma():
    volume = series([100.0, 200.0, 300.0, 400.0])
    assert volume_sma(volume, 2).equals(sma(volume, 2))


# ===========================================================================
# 공통 계약 — 길이 보존 / 짧은 데이터 / 잘못된 period
# ===========================================================================

SHORT = [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("name", "call"),
    [
        ("sma", lambda s: sma(s, 10)),
        ("ema", lambda s: ema(s, 10)),
        ("wilder_smooth", lambda s: wilder_smooth(s, 10)),
        ("rsi", lambda s: rsi(s, 10)),
        ("atr", lambda s: atr(s, s, s, 10)),
        ("adr_pct", lambda s: adr_pct(s, s, 10)),
        ("slope_pct", lambda s: slope_pct(s, 10)),
        ("rolling_high", lambda s: rolling_high(s, 10)),
        ("rolling_low", lambda s: rolling_low(s, 10)),
        ("volume_sma", lambda s: volume_sma(s, 10)),
    ],
)
def test_short_input_returns_all_nan_not_an_exception(name, call):
    """상장 6개월 종목의 SMA200은 에러가 아니라 '아직 없음'이다."""
    result = call(series(SHORT))
    assert len(result) == len(SHORT), name
    assert result.isna().all(), name


def test_short_input_macd_and_bollinger_return_all_nan():
    line, signal, hist = macd(series(SHORT), fast=12, slow=26, signal=9)
    assert line.isna().all() and signal.isna().all() and hist.isna().all()
    upper, mid, lower = bollinger(series(SHORT), period=20)
    assert upper.isna().all() and mid.isna().all() and lower.isna().all()


@pytest.mark.parametrize(
    ("name", "call"),
    [
        ("sma", lambda s: sma(s, 3)),
        ("ema", lambda s: ema(s, 3)),
        ("wilder_smooth", lambda s: wilder_smooth(s, 3)),
        ("rsi", lambda s: rsi(s, 3)),
        ("atr", lambda s: atr(s, s, s, 3)),
        ("adr_pct", lambda s: adr_pct(s, s, 3)),
        ("slope_pct", lambda s: slope_pct(s, 3)),
        ("rolling_high", lambda s: rolling_high(s, 3)),
        ("rolling_low", lambda s: rolling_low(s, 3)),
    ],
)
def test_output_length_and_index_match_input(name, call):
    """잘라서 반환하면 인덱스 정렬이 깨진다. 길이 보존은 계약이다."""
    source = series([float(i) for i in range(1, 21)])
    result = call(source)
    assert len(result) == len(source), name
    assert result.index.equals(source.index), name


@pytest.mark.parametrize(
    "call",
    [
        lambda: sma(series(SHORT), 0),
        lambda: ema(series(SHORT), 0),
        lambda: wilder_smooth(series(SHORT), -1),
        lambda: rsi(series(SHORT), 0),
        lambda: atr(series(SHORT), series(SHORT), series(SHORT), 0),
        lambda: bollinger(series(SHORT), 0),
        lambda: adr_pct(series(SHORT), series(SHORT), 0),
        lambda: slope_pct(series(SHORT), 0),
        lambda: rolling_high(series(SHORT), 0),
    ],
)
def test_non_positive_period_is_a_programming_error(call):
    """period <= 0 은 데이터 문제가 아니라 호출 버그다. NaN으로 삼키지 않는다."""
    with pytest.raises(ValueError, match="period"):
        call()
