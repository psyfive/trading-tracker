"""상대강도(RS) 산출.

RS 백분위의 **정의**는 '같은 시점 유니버스 내 순위'다. 그것을 계산하려면 수백 종목의
동일 기간 시세가 필요하고, 그 수집은 Phase 3.5다.

그 전까지 이 모듈은 **지수 대비 근사치**를 제공한다. 근사임을 이름과 경고로 계속
드러내는 것이 이 모듈의 설계 의도다 — 근사치를 진짜 백분위인 척 흘리면
게이트 임계값(RS >= 70)의 의미가 조용히 달라진다.

## 근사 방식

1. RS 라인 = 종목 종가 / 벤치마크 종가
2. RS 점수 = RS 라인의 기간별 수익률 가중합 (IBD 방식: 최근 분기에 큰 가중치)
3. 백분위 = 그 점수를 **자기 자신의 과거 분포**에서 순위화 (rolling rank, rs_rank_window)

3번이 진짜 백분위와 다른 지점이고, 그 결과 **재는 대상 자체가 달라진다**:

  진짜 백분위 -> "이 종목이 다른 종목들보다 강한가" (수준)
  이 근사     -> "이 종목의 상대강도가 자기 과거보다 강해지고 있는가" (가속도)

꾸준히 벤치마크를 이기는 종목은 RS 점수가 시간에 대해 평탄하므로 순위가 중간값
근처로 수렴한다 — 진짜 백분위였다면 상위권이어야 할 종목이다.
따라서 이 값을 쓰는 게이트(미너비니 RS >= 70)는 '강한 종목'이 아니라
'최근 상대강도가 붙는 종목'을 고른다. tests/test_regime_and_rs.py가 이 성질을 잠근다.

## 시점 정합성

모든 계산이 rolling/shift 기반이라 날짜 t의 값은 t 이하 데이터만 쓴다.
백테스트가 이 시리즈를 주입받으므로 여기서 미래를 섞으면 하네스 감사로는 잡히지 않는다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from config import RegimeConfig, UniverseConfig
from core.types import DiagnosticWarning, Severity, WarningCode


def benchmark_for(ticker: str, regime_config: RegimeConfig, exchange: str | None) -> str:
    """종목이 속한 시장의 벤치마크 지수 티커."""
    mapping = dict(regime_config.benchmark_by_exchange)
    return mapping.get(exchange or "", regime_config.default_benchmark)


def relative_strength_line(stock_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """RS 라인 = 종목 / 벤치마크. 공통 거래일에서만 정의된다.

    거래일이 어긋나는 시장(한국 지수와 미국 지수)을 섞지 않도록 교집합만 쓴다.
    """
    aligned = pd.concat([stock_close, benchmark_close], axis=1, join="inner")
    aligned.columns = ["stock", "benchmark"]
    return aligned["stock"] / aligned["benchmark"]


def rs_score(rs_line: pd.Series, config: UniverseConfig) -> pd.Series:
    """RS 라인의 기간별 수익률 가중합.

    가중치는 config.rs_weights (기본 0.4/0.2/0.2/0.2). 최근 구간에 더 큰 가중치를 준다.
    구간은 rs_lookback_days를 가중치 개수로 등분한다 (252일 -> 63/126/189/252).
    """
    periods = len(config.rs_weights)
    step = max(1, config.rs_lookback_days // periods)
    total = pd.Series(0.0, index=rs_line.index)
    usable = pd.Series(True, index=rs_line.index)

    for i, weight in enumerate(config.rs_weights, start=1):
        past = rs_line.shift(step * i)
        change = (rs_line - past) / past * 100.0
        usable &= change.notna()
        total = total + weight * change.fillna(0.0)

    return total.where(usable)


def approximate_rs_percentile_series(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    config: UniverseConfig,
) -> dict[date, float]:
    """지수 대비 RS 점수를 **자기 과거 분포**에서 순위화한 0~100 값.

    **이것은 유니버스 백분위가 아니다.** 모듈 docstring의 한계를 반드시 함께 읽을 것.
    호출부는 rs_universe_warning()을 리포트/검증 출력에 실어야 한다.

    반환에는 순위를 낼 만큼 과거가 쌓인 날짜만 담긴다 — 값이 없는 날짜는 키 자체가
    없으므로, 하네스는 그 시점 rs_percentile을 None으로 두고 게이트는 UNAVAILABLE이 된다.
    """
    line = relative_strength_line(stock_close, benchmark_close)
    scores = rs_score(line, config)
    ranked = (
        scores.rolling(window=config.rs_rank_window, min_periods=config.rs_rank_window)
        .rank(pct=True)
        .mul(100.0)
    )
    return {
        timestamp.date(): float(value)
        for timestamp, value in ranked.items()
        if pd.notna(value)
    }


def rs_line_new_high_series(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    config: UniverseConfig,
) -> dict[date, bool]:
    """RS 라인이 lookback 구간 신고가인지. 근사가 아니라 정의 그대로다."""
    line = relative_strength_line(stock_close, benchmark_close)
    rolling_max = line.rolling(
        window=config.rs_lookback_days, min_periods=config.rs_lookback_days
    ).max()
    return {
        timestamp.date(): bool(value >= peak)
        for timestamp, value, peak in zip(line.index, line, rolling_max, strict=True)
        if pd.notna(peak)
    }


def rs_universe_warning() -> DiagnosticWarning:
    """RS가 근사치임을 알리는 경고. 진짜 유니버스가 붙으면 사라진다."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.WARN,
        message=(
            "RS 백분위가 유니버스 순위가 아니라 지수 대비 상대강도를 자기 과거 분포에서 "
            "순위화한 근사치다. 재는 대상이 '다른 종목 대비 강도'가 아니라 "
            "'자기 과거 대비 상대강도의 가속도'이므로, 꾸준히 시장을 이기는 종목이 "
            "중간 점수를 받는다. 진짜 백분위는 유니버스 구축(Phase 3.5) 이후에 산출된다"
        ),
        field="indicators.rs_percentile",
    )


def load_universe_tickers(config: UniverseConfig) -> list[str]:
    """유니버스 구성 종목 목록. Phase 3.5."""
    raise NotImplementedError("유니버스 수집은 Phase 3.5")


def compute_rs_scores(closes: pd.DataFrame, config: UniverseConfig) -> pd.Series:
    """유니버스 전체의 RS 원점수. 인덱스 = 티커. Phase 3.5."""
    raise NotImplementedError("유니버스 기반 RS는 Phase 3.5")


def rs_percentile(ticker: str, scores: pd.Series) -> float | None:
    """유니버스 내 진짜 백분위. Phase 3.5."""
    raise NotImplementedError("유니버스 기반 백분위는 Phase 3.5")
