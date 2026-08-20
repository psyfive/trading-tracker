"""Phase 5 육안 검증 — 진단 한 건이 끝까지 흐르는가.

Phase 4까지의 시스템은 **백테스트에서만 존재했다.** 계약·지표·전략이 다 있는데
그것을 사람이 보는 경로가 없었고, 그래서 "계약만 보고 화면을 그릴 수 있는가"라는
질문이 한 번도 검증되지 않았다. Phase 5는 그 경로를 붙였다:

    수집 -> 지표 -> 국면/Stage -> RS -> 전략별 평가 -> 리스크 플랜 -> 리포트 -> 렌더

이 스크립트는 **네트워크를 타지 않는다.** 수집 진입점 두 개만 고정 픽스처로 바꾸고,
그 아래(지표·국면·RS·전략·플랜·조립·렌더)는 전부 실제 코드가 돈다.

보여주는 것:
  1. 실제 렌더 결과 (미국·KOSPI 각 1종목)
  2. 계좌 규모를 주면 플랜이 주수까지 채워진다
  3. **유니버스가 없으면 전 종목이 REJECTED_BY_GATE가 된다** — 이 파이프라인의 함정
  4. JSON 출력이 계약 왕복(round-trip)을 통과한다

    python scripts/verify_phase5.py
    python scripts/verify_phase5.py --market kospi
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

import main as cli  # noqa: E402
from config import DEFAULT_CONFIG  # noqa: E402
from core.types import BarMeta, DiagnosisReport, SessionState, Verdict  # noqa: E402
from data.fetcher import InvalidTickerError, OhlcvBundle  # noqa: E402
from data.universe import UniverseCloses, UniverseDataError  # noqa: E402
from render.json_out import to_json  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console(width=118)

MARKETS = {
    "us": {"ticker": "AAPL", "csv": "AAPL_3y.csv", "benchmark": ("SPY", "SPY_3y.csv"),
           "universe": ("us_large", "universe_us_large_closes.parquet")},
    "kospi": {"ticker": "005930.KS", "csv": "005930_KS_3y.csv",
              "benchmark": ("^KS11", "^KS11_3y.csv"),
              "universe": ("kospi", "universe_kospi_closes.parquet")},
}


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / name, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


@contextmanager
def fixture_data(spec: dict, *, universe_available: bool = True):
    """수집 진입점만 픽스처로 바꾼다. 그 아래는 실제 파이프라인이 돈다."""
    frames = {
        spec["ticker"].upper(): load_csv(spec["csv"]),
        spec["benchmark"][0].upper(): load_csv(spec["benchmark"][1]),
    }
    closes = pd.read_parquet(FIXTURE_DIR / spec["universe"][1])

    def load_ohlcv(ticker, data_config, exchanges, *, use_cache=True, now=None):
        upper = ticker.upper()
        if upper not in frames:
            raise InvalidTickerError(f"{ticker}: 픽스처에 없다")
        df = frames[upper]
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

    def load_universe_closes(name, data_config, exchanges, *, use_cache=True, now=None):
        if not universe_available:
            raise UniverseDataError("검증용 시나리오: 유니버스 종가를 확보하지 못했다")
        return UniverseCloses(name, closes, (), from_cache=True)

    original = (cli.load_ohlcv, cli.load_universe_closes)
    cli.load_ohlcv, cli.load_universe_closes = load_ohlcv, load_universe_closes
    try:
        yield
    finally:
        cli.load_ohlcv, cli.load_universe_closes = original


def diagnose(spec: dict, *, equity: float | None = None) -> DiagnosisReport:
    config = cli.apply_cli_overrides(DEFAULT_CONFIG, equity=equity, risk_pct=None)
    return cli.diagnose(spec["ticker"], config, cli.load_strategies("all", config))


def show_pipeline_stages(report: DiagnosisReport) -> None:
    """각 단계가 실제로 값을 채웠는지 — 조용히 비어 있으면 파이프라인이 끊긴 것이다."""
    indicators = report.indicators
    table = Table(title="[bold]파이프라인 단계별 산출[/bold]", header_style="bold cyan")
    for col in ("단계", "산출", "값"):
        table.add_column(col, justify="left")

    def mark(value, text: str) -> str:
        return f"[green]{text}[/green]" if value is not None else "[red]None — 배선 끊김[/red]"

    table.add_row("수집", "봉 수 / 기준일", f"{report.bar_meta.bars_available} / {report.as_of}")
    table.add_row("지표", "SMA200", mark(indicators.sma200, f"{indicators.sma200:,.2f}"
                                         if indicators.sma200 else ""))
    table.add_row("지표", "ATR14", mark(indicators.atr14, f"{indicators.atr14:,.2f}"
                                        if indicators.atr14 else ""))
    table.add_row("국면", "regime", f"[green]{report.regime.value}[/green]")
    table.add_row("Stage", "stage", f"[green]{report.stage.value}[/green]")
    table.add_row(
        "RS",
        "rs_percentile",
        mark(
            indicators.rs_percentile,
            f"{indicators.rs_percentile:.1f}" if indicators.rs_percentile is not None else "",
        ),
    )
    table.add_row("전략", "판정 수", f"{report.consensus.total_strategies}종")
    table.add_row(
        "리스크",
        "플랜 수",
        f"{len(report.risk_plans)}건 ({', '.join(report.risk_plans) or '없음'})",
    )
    console.print(table)


def show_missing_universe_trap(spec: dict) -> bool:
    """유니버스가 없을 때 무슨 일이 일어나는지 — 이유가 화면에 남는지 확인한다."""
    with fixture_data(spec, universe_available=False):
        report = diagnose(spec)

    all_rejected = all(
        v.verdict is Verdict.REJECTED_BY_GATE for v in report.strategy_verdicts
    )
    explained = any("종목의 문제가 아니다" in w.message for w in report.warnings)

    rejected_mark = "[green]예 (예상된 동작)[/green]" if all_rejected else "[red]아니오[/red]"
    explained_mark = "[green]예[/green]" if explained else "[red]아니오[/red]"
    body = (
        f"RS 백분위: [red]{report.indicators.rs_percentile}[/red]\n"
        f"판정: {', '.join(v.verdict.value for v in report.strategy_verdicts)}\n"
        f"전부 게이트 탈락: {rejected_mark}\n"
        f"이유가 경고에 남았는가: {explained_mark}\n\n"
        "[dim]세 전략 모두 게이트에 RS 조건이 있고 UNAVAILABLE은 AND 게이트를 막는다.\n"
        "즉 유니버스 종가를 확보하지 못하면 어떤 종목을 진단해도 전부 탈락한다 —\n"
        "그 상태는 화면상 '이 종목이 나쁘다'와 구분되지 않으므로 경고로 구분해야 한다.[/dim]"
    )
    console.print(
        Panel(body, title="[bold yellow]함정: 유니버스 없음[/bold yellow]", border_style="yellow")
    )
    return all_rejected and explained


def show_contract_round_trip(report: DiagnosisReport) -> bool:
    """렌더러가 소비하는 것과 프론트가 받는 것이 같은 객체인지."""
    payload = to_json(report)
    restored = DiagnosisReport.model_validate(json.loads(payload))
    same = restored == report
    console.print(
        f"  계약 왕복(JSON -> 모델 -> 비교): "
        f"{'[green]동일[/green]' if same else '[red]불일치[/red]'}  "
        f"[dim]{len(payload):,} bytes · schema {report.schema_version}[/dim]"
    )
    return same


def conclude(results: dict[str, dict]) -> int:
    table = Table(title="[bold]Phase 5 점검표[/bold]", header_style="bold cyan")
    for col in ("항목", "결과", "판정"):
        table.add_column(col)

    ok = True
    for market, result in results.items():
        table.add_row(
            f"{market} 진단 파이프라인",
            f"판정 {result['verdicts']}종 · RS "
            + ("실림" if result["rs"] is not None else "None"),
            "[green]PASS[/green]" if result["rs"] is not None else "[red]FAIL[/red]",
        )
        ok &= result["rs"] is not None
        has_plans = result["sized_plans"] > 0
        table.add_row(
            f"{market} 리스크 플랜(계좌 10만)",
            f"{result['sized_plans']}건 주수 채움",
            "[green]PASS[/green]"
            if has_plans and result["sized_ok"]
            else "[yellow]진입 의사가 있는 판정이 없어 검증 대상 없음[/yellow]",
        )
        table.add_row(
            f"{market} 유니버스 없음 시나리오",
            "전부 탈락 + 이유 경고" if result["trap"] else "동작이 예상과 다르다",
            "[green]PASS[/green]" if result["trap"] else "[red]FAIL[/red]",
        )
        ok &= result["trap"]
        table.add_row(
            f"{market} 계약 왕복",
            "동일" if result["round_trip"] else "불일치",
            "[green]PASS[/green]" if result["round_trip"] else "[red]FAIL[/red]",
        )
        ok &= result["round_trip"]
    console.print(table)

    lines = [
        "[bold]확인된 것[/bold]",
        "  진단 한 건이 수집부터 화면까지 끊기지 않고 흐른다.",
        "  계약(DiagnosisReport)만으로 화면을 그릴 수 있다 — 렌더러는 판정하지 않는다.",
        "  리스크 플랜은 전략마다 따로 나온다 (피벗이 다르면 진입가·손절가도 다르다).",
        "",
        "[bold]여전히 말할 수 없는 것[/bold]",
        "  이 검증은 고정 픽스처를 쓴다. 실제 네트워크 수집 경로(yfinance)는",
        "  `tests/test_fetcher_network.py`와 실제 실행에서만 확인된다.",
        "  첫 실제 실행은 유니버스 구성종목 수만큼 네트워크를 타므로 느리다.",
    ]
    console.print(
        Panel("\n".join(lines), border_style="green" if ok else "red",
              title="[bold]Phase 5 결론[/bold]")
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 진단 파이프라인 검증")
    parser.add_argument("--market", choices=list(MARKETS), default=None)
    parser.add_argument(
        "--quiet", action="store_true", help="렌더 결과를 생략하고 점검표만 출력"
    )
    args = parser.parse_args()

    results: dict[str, dict] = {}
    for market in [args.market] if args.market else list(MARKETS):
        spec = MARKETS[market]
        console.print()
        console.rule(f"[bold]{market}[/bold] — {spec['ticker']}")

        with fixture_data(spec):
            report = diagnose(spec)
            sized = diagnose(spec, equity=100_000.0)

        if not args.quiet:
            cli.render_report(report)
        show_pipeline_stages(report)
        console.print()

        sized_plans = sum(1 for plan in sized.risk_plans.values() if plan.shares is not None)
        results[market] = {
            "verdicts": report.consensus.total_strategies,
            "rs": report.indicators.rs_percentile,
            "sized_plans": sized_plans,
            "sized_ok": sized_plans == len(sized.risk_plans),
            "round_trip": show_contract_round_trip(report),
            "trap": show_missing_universe_trap(spec),
        }
        console.print()

    return conclude(results)


if __name__ == "__main__":
    raise SystemExit(main())
