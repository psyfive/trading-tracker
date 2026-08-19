"""전략 3종의 시점 정합성 — 하네스 감사를 CI에 걸어 둔다.

전략별 단위 테스트는 '같은 컨텍스트에 같은 판정'(결정론)까지만 본다. 그것만으로는
**시점이 달라졌을 때** 판정이 흔들리는지 알 수 없다. 여기서는 실제 픽스처에
`audit_lookahead()`를 돌려 '미래가 있을 때와 없을 때의 판정이 같은가'를 검사한다.

verify 스크립트에서도 같은 검사를 하지만 그것은 사람이 실행하는 것이다.
전략을 고치다 미래를 참조하게 되는 사고는 자동으로 잡혀야 한다.

주의: 이 감사는 **주입 시리즈(stage/rs) 안의** 미래 참조는 잡지 못한다.
그쪽은 `tests/test_regime_and_rs.py`와 `tests/test_universe.py`의 책임이다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import audit_lookahead
from config import DEFAULT_CONFIG
from data.universe import rs_percentile_frame, rs_percentile_series, rs_score_frame
from regime.market import stage_series
from strategies.minervini import MinerviniStrategy
from strategies.qullamaggie import QullamaggieStrategy
from strategies.weinstein import WeinsteinStrategy

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# 감사는 시점당 2회 평가라 비싸다. 봉 수를 줄이고 표본 시점도 줄인다 —
# 검사의 성격(시점별 재현 비교)은 그대로다.
AUDIT_CONFIG = replace(
    DEFAULT_CONFIG,
    backtest=replace(
        DEFAULT_CONFIG.backtest,
        warmup_bars=300,
        horizons=(20,),
        lookahead_audit_samples=6,
    ),
)


# 시장을 하나만 태우면 통화 스케일·거래일·유동성 자릿수가 다른 경로가 검사되지 않는다.
# 픽스처 2종목이면 감사 비용은 2배지만, 미래 참조가 특정 셋업 국면에서만 나는 유형을
# 비켜갈 확률이 줄어든다.
AUDIT_TICKERS = (
    ("AAPL", "aapl", "universe_us_large_closes.parquet"),
    ("005930.KS", "samsung", "universe_kospi_closes.parquet"),
)


@pytest.fixture(scope="module")
def rs_by_ticker() -> dict[str, dict]:
    """시점별 RS 백분위. 유니버스 픽스처에서 계산한다 (네트워크 없음)."""
    out: dict[str, dict] = {}
    for ticker, _, universe_file in AUDIT_TICKERS:
        closes = pd.read_parquet(FIXTURE_DIR / universe_file)
        percentiles = rs_percentile_frame(
            rs_score_frame(closes, DEFAULT_CONFIG.universe), DEFAULT_CONFIG.universe
        )
        out[ticker] = rs_percentile_series(ticker, percentiles)
    return out


def strategies():
    cfg = DEFAULT_CONFIG
    return [
        MinerviniStrategy(cfg.minervini),
        WeinsteinStrategy(cfg.weinstein),
        QullamaggieStrategy(cfg.qullamaggie),
    ]


@pytest.mark.parametrize("strategy", strategies(), ids=lambda s: s.name)
@pytest.mark.parametrize(
    ("ticker", "fixture_name"),
    [(ticker, fixture_name) for ticker, fixture_name, _ in AUDIT_TICKERS],
)
def test_strategy_passes_the_lookahead_audit(
    strategy, ticker, fixture_name, rs_by_ticker, request
):
    """미래 유무에 따라 판정이 달라지면 위반이다.

    비교 대상은 verdict/score만이 아니라 StrategyVerdict 전체다 — setup_state나
    setup_metrics로만 새는 미래 참조도 리포트에 실리므로 똑같이 잡아야 한다.
    """
    df = request.getfixturevalue(fixture_name)
    audit = audit_lookahead(
        strategy,
        ticker,
        df,
        AUDIT_CONFIG,
        stage_by_date=stage_series(df, DEFAULT_CONFIG.regime),
        rs_percentile_by_date=rs_by_ticker[ticker],
    )
    assert audit.checked_points > 0, "감사가 한 시점도 검사하지 못했다"
    assert audit.violations == ()
    assert audit.clean is True


@pytest.mark.parametrize("strategy", strategies(), ids=lambda s: s.name)
def test_strategy_does_not_use_the_full_history_backdoor(strategy):
    """뒷문(requires_full_history)을 쓰는 전략은 더미뿐이어야 한다."""
    assert getattr(strategy, "requires_full_history", False) is False
