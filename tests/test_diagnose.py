"""진단 파이프라인 테스트 — `main.diagnose()`.

네트워크를 타지 않기 위해 수집 진입점 두 개(`load_ohlcv` / `load_universe_closes`)만
픽스처로 갈아끼운다. 그 아래(지표·국면·RS·전략·플랜·조립)는 전부 실제 코드가 돈다 —
파이프라인 테스트가 검증해야 하는 것은 배선이지 목(mock)의 동작이 아니다.

가장 중요한 회귀 방지 대상은 **RS 배선**이다. 세 전략 모두 게이트에 RS 조건이 있고
UNAVAILABLE은 AND 게이트를 막으므로, 유니버스 종가를 실어 나르지 못하면 어떤 종목을
진단해도 전부 REJECTED_BY_GATE가 나온다. 그 상태는 '종목이 나쁘다'와 화면상 구분되지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import main as cli
from config import DEFAULT_CONFIG
from core.types import (
    BarMeta,
    MarketRegime,
    SessionState,
    Verdict,
    WarningCode,
)
from data.fetcher import InvalidTickerError, OhlcvBundle
from data.universe import UniverseCloses, UniverseDataError
from strategies.registry import UnknownStrategyError

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
TICKER = "AAPL"


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / name, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


@pytest.fixture(scope="module")
def universe_closes() -> pd.DataFrame:
    return pd.read_parquet(FIXTURE_DIR / "universe_us_large_closes.parquet")


def bundle_for(ticker: str, df: pd.DataFrame, *, complete: bool = True) -> OhlcvBundle:
    return OhlcvBundle(
        ticker=ticker.upper(),
        ohlcv=df,
        bar_meta=BarMeta(
            last_bar_date=df.index[-1].date(),
            session_state=SessionState.CLOSED if complete else SessionState.OPEN,
            is_bar_complete=complete,
            bars_available=len(df),
            volume_judgements_reliable=complete,
        ),
        warnings=(),
        from_cache=True,
    )


@pytest.fixture
def wired(monkeypatch, universe_closes):
    """수집 경로를 픽스처로 고정한다. 반환값으로 시나리오를 바꿀 수 있다."""
    state = {
        "ohlcv": {TICKER: load_csv("AAPL_3y.csv"), "SPY": load_csv("SPY_3y.csv")},
        "complete": True,
        "universe": UniverseCloses("us_large", universe_closes, (), from_cache=True),
        "universe_error": None,
    }

    def fake_load_ohlcv(ticker, data_config, exchanges, *, use_cache=True, now=None):
        upper = ticker.upper()
        if upper not in state["ohlcv"]:
            raise InvalidTickerError(f"{ticker}: 테스트 픽스처에 없다")
        return bundle_for(upper, state["ohlcv"][upper], complete=state["complete"])

    def fake_load_universe(name, data_config, exchanges, *, use_cache=True, now=None):
        if state["universe_error"] is not None:
            raise state["universe_error"]
        return state["universe"]

    monkeypatch.setattr(cli, "load_ohlcv", fake_load_ohlcv)
    monkeypatch.setattr(cli, "load_universe_closes", fake_load_universe)
    return state


def diagnose(strategies: str = "all", config=None):
    config = config or DEFAULT_CONFIG
    return cli.diagnose(TICKER, config, cli.load_strategies(strategies, config))


# ===========================================================================
# 배선 — 파이프라인이 실제로 흐르는가
# ===========================================================================


def test_report_is_assembled_for_every_strategy(wired):
    report = diagnose()
    assert report.ticker == TICKER
    assert [v.strategy_name for v in report.strategy_verdicts] == [
        "minervini",
        "weinstein",
        "qullamaggie",
    ]
    assert report.consensus.total_strategies == 3


def test_rs_percentile_actually_reaches_the_context(wired):
    """회귀 방지 (파이프라인의 핵심 함정).

    유니버스 종가를 RS 백분위까지 실어 나르지 못하면 세 전략 모두 RS 조건이
    UNAVAILABLE이 되고, AND 게이트가 막혀 어떤 종목도 게이트를 통과하지 못한다.
    """
    report = diagnose()
    assert report.indicators.rs_percentile is not None
    for verdict in report.strategy_verdicts:
        statuses = {c.id: c.status for c in verdict.gate.checks}
        assert statuses["rs_percentile"].value != "UNAVAILABLE"


def test_regime_and_stage_are_measured_not_defaulted(wired):
    report = diagnose()
    assert report.stage.value != "UNDEFINED"
    assert report.regime in tuple(MarketRegime)


def test_indicators_are_computed(wired):
    indicators = diagnose().indicators
    assert indicators.sma200 is not None
    assert indicators.atr14 is not None
    assert indicators.dollar_volume_50 is not None


def test_report_serializes_through_the_single_entry_point(wired):
    from render.json_out import to_json

    payload = to_json(diagnose())
    assert '"schema_version"' in payload
    assert TICKER in payload


# ===========================================================================
# 리스크 플랜
# ===========================================================================


def test_risk_plans_are_attached_to_actionable_verdicts_only(wired):
    report = diagnose()
    actionable = {
        v.strategy_name
        for v in report.strategy_verdicts
        if v.verdict in (Verdict.BUY, Verdict.WATCH)
    }
    assert set(report.risk_plans) <= actionable


def test_equity_override_reaches_the_plan(wired):
    """--equity가 실제로 주수까지 흘러야 한다. 안 그러면 옵션이 거짓말이다."""
    config = cli.apply_cli_overrides(DEFAULT_CONFIG, equity=100_000.0, risk_pct=1.0)
    report = cli.diagnose(TICKER, config, cli.load_strategies("all", config))
    if not report.risk_plans:
        pytest.skip("이 픽스처 시점에는 진입 의사가 있는 판정이 없다")
    for plan in report.risk_plans.values():
        assert plan.account_equity == 100_000.0
        assert plan.shares is not None


def test_without_equity_shares_stay_none(wired):
    report = diagnose()
    for plan in report.risk_plans.values():
        assert plan.shares is None
        assert plan.entry > plan.stop


# ===========================================================================
# 실패 경로 — 이유가 보여야 한다
# ===========================================================================


def test_missing_universe_explains_that_it_is_not_the_stock_s_fault(wired):
    wired["universe_error"] = UniverseDataError("테스트: 유니버스 없음")
    report = diagnose()

    assert report.indicators.rs_percentile is None
    warning = next(
        w for w in report.warnings if w.code is WarningCode.RS_UNIVERSE_MISSING
    )
    assert "종목의 문제가 아니다" in warning.message
    assert all(v.verdict is Verdict.REJECTED_BY_GATE for v in report.strategy_verdicts)


def test_missing_benchmark_falls_back_to_caution_with_a_warning(wired):
    del wired["ohlcv"]["SPY"]
    report = diagnose()

    assert report.regime is MarketRegime.CAUTION
    codes = {w.code for w in report.warnings}
    assert WarningCode.BENCHMARK_UNAVAILABLE in codes


def test_a_failing_strategy_does_not_kill_the_others(wired):
    """계약상 STRATEGY_ERROR의 의미가 '해당 전략만 실패'다."""

    class Exploding:
        name = "exploding"
        version = "0.0.1"

        def evaluate(self, ctx):
            raise RuntimeError("의도적 실패")

    config = DEFAULT_CONFIG
    strategies = [*cli.load_strategies("minervini", config), Exploding()]
    report = cli.diagnose(TICKER, config, strategies)

    assert [v.strategy_name for v in report.strategy_verdicts] == ["minervini"]
    assert report.consensus.total_strategies == 1
    warning = next(w for w in report.warnings if w.code is WarningCode.STRATEGY_ERROR)
    assert "exploding" in warning.message
    assert "'거절'이 아니다" in warning.message


def test_unknown_ticker_raises_a_domain_error(wired):
    with pytest.raises(InvalidTickerError):
        cli.diagnose("NOPE", DEFAULT_CONFIG, cli.load_strategies("minervini", DEFAULT_CONFIG))


# ===========================================================================
# 미완성 봉
# ===========================================================================


def test_incomplete_bar_is_mirrored_and_warned(wired):
    wired["complete"] = False
    report = diagnose()

    assert report.is_bar_complete is False
    assert report.bar_meta.is_bar_complete is False
    assert any(w.code is WarningCode.INCOMPLETE_BAR for w in report.warnings)


def test_incomplete_bar_withholds_buy(wired):
    """거래량을 확인할 수 없으면 어떤 전략도 BUY를 내지 않는다."""
    wired["complete"] = False
    report = diagnose()
    assert report.consensus.buy_strategies == []


# ===========================================================================
# 전략 선택 / CLI 배선
# ===========================================================================


def test_load_strategies_all_returns_every_registered_strategy():
    strategies = cli.load_strategies("all", DEFAULT_CONFIG)
    assert [s.name for s in strategies] == ["minervini", "weinstein", "qullamaggie"]


def test_load_strategies_accepts_a_subset():
    strategies = cli.load_strategies("weinstein,qullamaggie", DEFAULT_CONFIG)
    assert [s.name for s in strategies] == ["weinstein", "qullamaggie"]


def test_unknown_strategy_name_is_loud():
    """오타를 무시하면 사용자는 요청한 전략이 돈 줄 안다."""
    with pytest.raises(UnknownStrategyError, match="minerviny"):
        cli.load_strategies("minerviny", DEFAULT_CONFIG)


def test_dummy_strategies_are_not_registered():
    """하네스 검증용 더미가 사용자 화면에 뜨면 안 된다."""
    names = {s.name for s in cli.load_strategies("all", DEFAULT_CONFIG)}
    assert not names & {"always_buy", "random", "perfect_hindsight"}


def test_cli_json_mode_prints_a_valid_report(wired, capsys):
    import json

    assert cli.main([TICKER, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ticker"] == TICKER
    assert len(payload["strategy_verdicts"]) == 3


def test_cli_renders_without_error(wired, capsys):
    assert cli.main([TICKER]) == 0
    out = capsys.readouterr().out
    assert TICKER in out
    assert "컨센서스" in out


def test_cli_reports_data_errors_as_exit_1(wired, capsys):
    assert cli.main(["NOPE"]) == 1
    assert "데이터 오류" in capsys.readouterr().err


def test_cli_reports_unknown_strategy_as_exit_2(capsys):
    assert cli.main([TICKER, "--strategies", "nope"]) == 2
    assert "등록되지 않은 전략" in capsys.readouterr().err
