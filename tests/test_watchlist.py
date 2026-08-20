"""워치리스트(다종목 스캔) 테스트.

여기서 잠그는 것:

  - **스캔과 단건 진단이 같은 판정을 낸다** — 목록과 상세가 갈라지면 도구를 못 믿는다
  - 시장 공통 재료(RS 프레임)를 종목 수만큼 다시 만들지 않는다
  - 정렬이 계약이다 (렌더러가 다시 정렬하지 않는다)
  - 진단하지 못한 종목이 조용히 사라지지 않는다
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import pytest
from rich.console import Console

import main as cli
from config import DEFAULT_CONFIG
from core.types import (
    Agreement,
    BarMeta,
    ScanFailure,
    SessionState,
    SetupState,
    StrategySummary,
    Verdict,
    WatchlistEntry,
)
from core.watchlist import (
    agreement_counts,
    build_entry,
    build_watchlist,
    sort_entries,
    summarize_counts,
    summarize_verdict,
)
from data.fetcher import InvalidTickerError, OhlcvBundle
from data.universe import UniverseCloses
from render import cli as renderer

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
UNIVERSE = "us_large"


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / name, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


@pytest.fixture(scope="module")
def panel() -> dict[str, pd.DataFrame]:
    raw = pd.read_parquet(FIXTURE_DIR / f"panel_{UNIVERSE}_ohlcv.parquet")
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in raw.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date").sort_index()
        frame.index.name = "date"
        frames[str(ticker)] = frame.astype(float)
    frames["SPY"] = load_csv("SPY_3y.csv")
    return frames


@pytest.fixture(scope="module")
def universe_closes() -> pd.DataFrame:
    return pd.read_parquet(FIXTURE_DIR / f"universe_{UNIVERSE}_closes.parquet")


@pytest.fixture
def wired(monkeypatch, panel, universe_closes):
    """수집 경로만 픽스처로. 그 아래는 실제 파이프라인이 돈다."""
    state = {"universe_calls": 0, "progress": []}

    def fake_load_ohlcv(ticker, data_config, exchanges, *, use_cache=True, now=None):
        upper = ticker.upper()
        if upper not in panel:
            raise InvalidTickerError(f"{ticker}: 테스트 픽스처에 없다")
        df = panel[upper]
        return OhlcvBundle(
            ticker=upper,
            ohlcv=df,
            bar_meta=BarMeta(
                last_bar_date=df.index[-1].date(),
                session_state=SessionState.CLOSED,
                is_bar_complete=True,
                bars_available=len(df),
                volume_judgements_reliable=True,
            ),
            warnings=(),
            from_cache=True,
        )

    def fake_load_universe(
        name, data_config, exchanges, *, use_cache=True, now=None, progress=None
    ):
        state["universe_calls"] += 1
        if progress is not None:
            progress("수집 TEST", 1, 1)
        return UniverseCloses(name, universe_closes, (), from_cache=True)

    monkeypatch.setattr(cli, "load_ohlcv", fake_load_ohlcv)
    monkeypatch.setattr(cli, "load_universe_closes", fake_load_universe)
    return state


@pytest.fixture(scope="module")
def sample_tickers(panel) -> list[str]:
    return [t for t in sorted(panel) if t != "SPY"][:8]


def run_scan(tickers, **kwargs):
    config = DEFAULT_CONFIG
    return cli.scan(
        UNIVERSE, config, cli.load_strategies("all", config), tickers=tickers, **kwargs
    )


# ===========================================================================
# 요약 — 표 한 줄로 줄이되 의미를 잃지 않는다
# ===========================================================================


def summary(
    name: str = "minervini",
    verdict: Verdict = Verdict.BUY,
    *,
    score_pct: float | None = 70.0,
    passed: int = 8,
    total: int = 8,
) -> StrategySummary:
    return StrategySummary(
        strategy_name=name,
        verdict=verdict,
        score_pct=score_pct,
        gate_pass_count=passed,
        gate_total=total,
        progress_ratio=passed / total,
        setup_state=SetupState.PIVOT_READY,
    )


def entry(ticker: str, summaries: list[StrategySummary]) -> WatchlistEntry:
    buys = [s.strategy_name for s in summaries if s.verdict is Verdict.BUY]
    total, n_buy = len(summaries), len(buys)
    if n_buy == 0:
        agreement = Agreement.NONE
    elif n_buy == total:
        agreement = Agreement.UNANIMOUS_BUY
    elif n_buy * 2 > total:
        agreement = Agreement.MAJORITY_BUY
    else:
        agreement = Agreement.SPLIT
    return WatchlistEntry(
        ticker=ticker,
        price=100.0,
        as_of=pd.Timestamp("2026-08-18").date(),
        is_bar_complete=True,
        strategies=summaries,
        buy_strategies=buys,
        agreement=agreement,
        best_gate_progress=max((s.progress_ratio for s in summaries), default=0.0),
    )


def test_summary_carries_the_ratio_not_the_raw_score(wired, sample_tickers):
    """만점 척도가 전략·시점마다 달라 절대 점수는 나란히 놓을 수 없다."""
    report = cli.diagnose(
        sample_tickers[0], DEFAULT_CONFIG, cli.load_strategies("all", DEFAULT_CONFIG)
    )
    for verdict in report.strategy_verdicts:
        summarized = summarize_verdict(verdict)
        assert summarized.score_pct == (
            None if verdict.score_pct is None else pytest.approx(verdict.score_pct, abs=1e-4)
        )


def test_rejected_strategy_summary_has_no_score():
    """'채점 안 함'이 요약에서 0%로 둔갑하면 안 된다."""
    with pytest.raises(ValueError, match="채점하지 않는다"):
        summary(verdict=Verdict.REJECTED_BY_GATE, score_pct=0.0, passed=7)


def test_progress_ratio_must_match_the_counts():
    with pytest.raises(ValueError, match="progress_ratio"):
        StrategySummary(
            strategy_name="x",
            verdict=Verdict.WATCH,
            gate_pass_count=4,
            gate_total=8,
            progress_ratio=1.0,
        )


# ===========================================================================
# 조립 — 파생 필드는 컨센서스에서 옮긴다
# ===========================================================================


def test_entry_mirrors_the_diagnosis(wired, sample_tickers):
    ticker = sample_tickers[0]
    report = cli.diagnose(
        ticker, DEFAULT_CONFIG, cli.load_strategies("all", DEFAULT_CONFIG)
    )
    built = build_entry(report)

    assert built.ticker == report.ticker
    assert built.price == report.price
    assert built.as_of == report.as_of
    assert built.stage is report.stage
    assert built.rs_percentile == report.indicators.rs_percentile
    assert built.buy_strategies == report.consensus.buy_strategies
    assert built.agreement is report.consensus.agreement
    assert [s.strategy_name for s in built.strategies] == [
        v.strategy_name for v in report.strategy_verdicts
    ]


def test_entry_derived_fields_are_validated():
    """중복 저장은 validator가 강제한다 (ConsensusSummary와 같은 규율)."""
    with pytest.raises(ValueError, match="buy_strategies"):
        WatchlistEntry(
            ticker="X",
            price=1.0,
            as_of=pd.Timestamp("2026-01-02").date(),
            is_bar_complete=True,
            strategies=[summary(verdict=Verdict.BUY)],
            buy_strategies=[],
            agreement=Agreement.NONE,
            best_gate_progress=1.0,
        )


# ===========================================================================
# 정렬 — 계약이다
# ===========================================================================


def test_buy_entries_come_first():
    entries = [
        entry("NOBUY", [summary(verdict=Verdict.WATCH, passed=8)]),
        entry("BUYIT", [summary(verdict=Verdict.BUY, passed=5, total=8)]),
    ]
    assert [e.ticker for e in sort_entries(entries)] == ["BUYIT", "NOBUY"]


def test_gate_progress_breaks_ties_among_non_buys():
    """게이트에 근접한 종목이 위에 온다 — 내일 조건을 채울 후보이기 때문이다."""
    entries = [
        entry("FAR", [summary(verdict=Verdict.WATCH, passed=4, total=8)]),
        entry("NEAR", [summary(verdict=Verdict.WATCH, passed=7, total=8)]),
    ]
    assert [e.ticker for e in sort_entries(entries)] == ["NEAR", "FAR"]


def test_ties_are_broken_by_ticker_for_stability():
    """같은 데이터로 두 번 돌렸을 때 순서가 흔들리면 어제와 비교할 수 없다."""
    entries = [
        entry("ZZZ", [summary(verdict=Verdict.WATCH)]),
        entry("AAA", [summary(verdict=Verdict.WATCH)]),
    ]
    assert [e.ticker for e in sort_entries(entries)] == ["AAA", "ZZZ"]


def test_report_rejects_unsorted_entries():
    """정렬이 계약이므로 어긴 리포트는 만들어지지 않는다."""
    from core.types import MarketRegime, WatchlistReport

    unsorted_entries = [
        entry("A", [summary(verdict=Verdict.WATCH, passed=4, total=8)]),
        entry("B", [summary(verdict=Verdict.BUY)]),
    ]
    with pytest.raises(ValueError, match="내림차순"):
        WatchlistReport(
            universe=UNIVERSE,
            generated_at=pd.Timestamp("2026-08-18", tz="UTC").to_pydatetime(),
            regime=MarketRegime.RISK_ON,
            entries=unsorted_entries,
            requested=2,
        )


def test_requested_counts_successes_and_failures():
    from core.types import MarketRegime

    report = build_watchlist(
        UNIVERSE,
        [],
        regime=MarketRegime.CAUTION,
        failed=[ScanFailure(ticker="DEAD", reason="상장폐지")],
    )
    assert report.requested == 1
    assert report.entries == []
    assert report.failed[0].ticker == "DEAD"


# ===========================================================================
# 스캔 — 목록과 상세가 같은 말을 하는가
# ===========================================================================


def test_scan_and_diagnose_agree(wired, sample_tickers):
    """가장 중요한 성질.

    스캔이 요약용 계산을 따로 하면 '목록에서는 BUY였는데 눌러 보니 WATCH'가 생긴다.
    스캔은 diagnose()를 그대로 부르므로 판정이 같아야 한다.
    """
    watchlist = run_scan(sample_tickers)
    by_ticker = {e.ticker: e for e in watchlist.entries}

    for ticker in sample_tickers:
        report = cli.diagnose(
            ticker, DEFAULT_CONFIG, cli.load_strategies("all", DEFAULT_CONFIG)
        )
        summarized = by_ticker[report.ticker]
        assert [s.verdict for s in summarized.strategies] == [
            v.verdict for v in report.strategy_verdicts
        ]
        assert summarized.buy_strategies == report.consensus.buy_strategies
        assert summarized.price == report.price


def test_market_data_is_built_once_for_the_whole_scan(wired, sample_tickers):
    """종목마다 다시 만들면 116종목 스캔이 RS 프레임을 116번 계산한다."""
    run_scan(sample_tickers)
    assert wired["universe_calls"] == 1


def test_failed_tickers_are_reported_not_dropped(wired, sample_tickers):
    watchlist = run_scan([*sample_tickers, "NOPE"])
    assert [f.ticker for f in watchlist.failed] == ["NOPE"]
    assert watchlist.requested == len(sample_tickers) + 1
    assert len(watchlist.entries) == len(sample_tickers)


def test_scan_reports_progress(wired, sample_tickers):
    """116종목을 도는 동안 조용하면 사용자는 멈춘 줄 안다."""
    seen: list[tuple[str, int, int]] = []
    run_scan(sample_tickers, progress=lambda label, done, total: seen.append((label, done, total)))

    assert seen, "진행 콜백이 한 번도 불리지 않았다"
    assert any(label.startswith("진단") for label, _, _ in seen)
    assert seen[-1][1] == seen[-1][2] == len(sample_tickers)


def test_scan_carries_the_universe_warnings(wired, sample_tickers):
    from core.types import WarningCode

    watchlist = run_scan(sample_tickers)
    assert WarningCode.RS_UNIVERSE_MISSING in {w.code for w in watchlist.warnings}


def test_scan_entries_are_sorted_on_arrival(wired, sample_tickers):
    watchlist = run_scan(sample_tickers)
    keys = [(len(e.buy_strategies), e.best_gate_progress) for e in watchlist.entries]
    assert keys == sorted(keys, reverse=True)


def test_counts_are_per_ticker_not_per_verdict(wired, sample_tickers):
    """한 종목이 BUY와 AVOID에 동시에 잡힐 수 있다 — 서로 다른 방법론이므로 정상이다."""
    watchlist = run_scan(sample_tickers)
    counts = summarize_counts(watchlist)
    assert sum(counts.values()) >= len(watchlist.entries)
    assert sum(agreement_counts(watchlist).values()) == len(watchlist.entries)


# ===========================================================================
# 렌더러
# ===========================================================================


@pytest.fixture
def output(monkeypatch):
    buffer = StringIO()
    monkeypatch.setattr(
        renderer,
        "console",
        Console(file=buffer, width=200, no_color=True, force_terminal=False, legacy_windows=False),
    )
    return buffer


def test_watchlist_renders_every_shown_ticker(wired, sample_tickers, output):
    watchlist = run_scan(sample_tickers)
    renderer.render_watchlist(watchlist)
    text = output.getvalue()
    for entry_row in watchlist.entries:
        assert entry_row.ticker in text


def test_top_filter_limits_rows_but_says_so(wired, sample_tickers, output):
    watchlist = run_scan(sample_tickers)
    renderer.render_watchlist(watchlist, top=2)
    text = output.getvalue()
    assert f"스캔 {watchlist.requested}" in text
    assert "2종목 표시" in text


def test_verdict_filter_keeps_only_matching_entries(wired, sample_tickers, output):
    watchlist = run_scan(sample_tickers)
    renderer.render_watchlist(watchlist, verdicts={Verdict.BUY})
    text = output.getvalue()
    for entry_row in watchlist.entries:
        if not entry_row.buy_strategies:
            continue
        assert entry_row.ticker in text


def test_rejected_cell_shows_no_score(wired, sample_tickers, output):
    """게이트 탈락 칸에 0%가 찍히면 '낮은 점수'로 읽힌다."""
    watchlist = run_scan(sample_tickers)
    renderer.render_watchlist(watchlist)
    assert "GATE" in output.getvalue()


def test_watchlist_states_that_scores_are_not_comparable(wired, sample_tickers, output):
    watchlist = run_scan(sample_tickers)
    renderer.render_watchlist(watchlist)
    assert "비교하거나 평균낼 수 없다" in output.getvalue()


# ===========================================================================
# CLI 배선
# ===========================================================================


def test_cli_scan_renders(wired, monkeypatch, sample_tickers, capsys):
    monkeypatch.setattr(cli, "load_universe_tickers", lambda name: sample_tickers)
    assert cli.main(["--scan", UNIVERSE, "--top", "3"]) == 0
    assert "유니버스" in capsys.readouterr().out


def test_cli_scan_json_is_valid(wired, monkeypatch, sample_tickers, capsys):
    import json

    monkeypatch.setattr(cli, "load_universe_tickers", lambda name: sample_tickers)
    assert cli.main(["--scan", UNIVERSE, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["universe"] == UNIVERSE
    assert payload["requested"] == len(sample_tickers)


def test_cli_rejects_ticker_and_scan_together(capsys):
    assert cli.main(["AAPL", "--scan", UNIVERSE]) == 2
    assert "함께 쓸 수 없다" in capsys.readouterr().err


def test_cli_requires_a_target(capsys):
    assert cli.main([]) == 2
    assert "--scan" in capsys.readouterr().err


def test_cli_rejects_an_unknown_verdict_filter(capsys):
    assert cli.main(["--scan", UNIVERSE, "--verdict", "NOPE"]) == 2
    assert "알 수 없는 판정" in capsys.readouterr().err


def test_verdict_filter_parses_names():
    assert cli.parse_verdict_filter("buy,watch") == {Verdict.BUY, Verdict.WATCH}
    assert cli.parse_verdict_filter(None) is None


def test_summary_is_much_smaller_than_the_full_diagnosis(wired, sample_tickers):
    """요약 계약이 존재하는 이유를 실측으로 잠근다.

    진단 리포트를 100종목 담으면 1MB를 넘고, 그중 대부분은 표 한 줄에 필요 없는
    설명 문장이다. 실제 데이터에서 몇 배 차이인지 재 둔다.
    """
    from render.json_out import to_json, watchlist_to_json

    ticker = sample_tickers[0]
    report = cli.diagnose(
        ticker, DEFAULT_CONFIG, cli.load_strategies("all", DEFAULT_CONFIG)
    )
    watchlist = build_watchlist(UNIVERSE, [report], regime=report.regime)

    report_size = len(to_json(report))
    entry_size = len(watchlist_to_json(watchlist))
    assert entry_size * 5 < report_size, (
        f"요약 {entry_size}B vs 진단 {report_size}B — 요약이 충분히 작지 않다"
    )
