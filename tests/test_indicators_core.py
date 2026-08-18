"""지표 손계산 검증.

CLAUDE.md 원칙 5: 모든 지표에 손계산 기대값 테스트를 붙인다. 테스트 없는 지표는 머지하지 않는다.

기대값은 코드에 상수로 박고 출처를 주석으로 남긴다. 다른 라이브러리 출력을 그대로
기대값으로 쓰지 않는다 (그러면 외부 라이브러리 금지 원칙이 무의미해진다).

아직 indicators/core.py가 미구현이므로 전부 skip 상태다.
구현 단계에서 skip 마커를 걷어내고 기대값을 채운다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="indicators/core.py 미구현 — 구현 단계에서 활성화")


# 손계산용 고정 시계열. 구현 단계에서 이 값들로 기대값을 손으로 계산해 채운다.
CLOSE = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
         45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
HIGH = [c + 0.5 for c in CLOSE]
LOW = [c - 0.5 for c in CLOSE]


def test_sma_matches_hand_calculation():
    """SMA(5)의 첫 유효값 = 앞 5개 평균. 앞 4개는 NaN."""
    raise NotImplementedError


def test_sma_preserves_input_length():
    """입력과 같은 길이를 반환해야 한다. 잘라서 반환 금지."""
    raise NotImplementedError


def test_ema_seed_is_sma():
    """EMA 첫 유효값은 SMA(period) seed여야 한다."""
    raise NotImplementedError


def test_wilder_smooth_recurrence():
    """(prev * (n-1) + cur) / n 점화식을 손으로 검증."""
    raise NotImplementedError


def test_rsi_uses_wilder_not_ewm():
    """Wilder RSI. pandas ewm(span=14) 결과와는 달라야 한다.

    CLOSE 시계열은 Wilder RSI 교과서 예제 기반 — 기대값 출처를 여기 명시할 것.
    """
    raise NotImplementedError


def test_rsi_all_gains_is_100():
    """손실이 없으면 RSI = 100 (0으로 나누기 방어)."""
    raise NotImplementedError


def test_true_range_uses_previous_close():
    """max(H-L, |H-C_prev|, |L-C_prev|). 갭 상황에서 H-L보다 커야 한다."""
    raise NotImplementedError


def test_atr_is_wilder_smoothed_true_range():
    raise NotImplementedError


def test_macd_line_and_signal():
    """EMA(12) - EMA(26), signal = EMA(9) of macd line."""
    raise NotImplementedError


def test_bollinger_uses_population_std():
    """ddof=0. ddof=1과 값이 달라야 한다."""
    raise NotImplementedError


def test_slope_pct_direction():
    """상승 시계열은 양수, 하락 시계열은 음수."""
    raise NotImplementedError
