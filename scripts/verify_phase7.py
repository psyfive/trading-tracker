"""Phase 7 육안 검증 — 워치리스트가 '오늘 뭘 봐야 하나'에 답하는가.

Phase 5까지는 티커를 하나씩 물어봐야 했다. 매일 쓰는 도구가 되려면 반대 방향이
필요하다: **유니버스를 훑어 볼 만한 것을 추려 주는 것.**

이 스크립트는 네트워크를 타지 않는다. 수집 진입점 두 개만 고정 픽스처로 바꾸고
그 아래(지표·국면·RS·전략·플랜·조립·정렬·렌더)는 전부 실제 코드가 돈다.

확인하는 것:
  1. 실제 워치리스트 출력 (패널 전 종목)
  2. **스캔과 단건 진단이 같은 판정을 내는가** — 목록과 상세가 갈라지면 못 믿는다
  3. 요약 계약이 실제로 얼마나 작은가 (왜 진단 리포트를 그대로 나르지 않는가)
  4. 스캔 비용 (종목당 몇 ms)

    python scripts/verify_phase7.py
    python scripts/verify_phase7.py --market kospi
"""

from __future__ import annotations

import argparse
import sys
import time
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
from core.types import BarMeta, SessionState, WatchlistReport  # noqa: E402
from core.watchlist import agreement_counts, summarize_counts  # noqa: E402
from data.fetcher import InvalidTickerError, OhlcvBundle  # noqa: E402
from data.universe import UniverseCloses  # noqa: E402
from render.json_out import to_json, watchlist_to_json  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console(width=118)

MARKETS = {
    "us": {
        "universe": "us_large",
        "benchmark": ("SPY", "SPY_3y.csv"),
        "panel": "panel_us_large_ohlcv",
    },
    "kospi": {
        "universe": "kospi",
        "benchmark": ("^KS11", "^KS11_3y.csv"),
        "panel": "panel_kospi_ohlcv",
    },
}


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / name, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def load_panel(name: str) -> dict[str, pd.DataFrame]:
    raw = pd.read_parquet(FIXTURE_DIR / f"{name}.parquet")
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in raw.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date").sort_index()
        frame.index.name = "date"
        frames[str(ticker)] = frame.astype(float)
    return frames


@contextmanager
def fixture_data(spec: dict):
    """수집 진입점만 픽스처로 바꾼다. 그 아래는 실제 파이프라인이 돈다."""
    frames = load_panel(spec["panel"])
    frames[spec["benchmark"][0].upper()] = load_csv(spec["benchmark"][1])
    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{spec['universe']}_closes.parquet")

    def load_ohlcv(ticker, data_config, exchanges, *, use_cache=True, now=None):
        upper = ticker.upper()
        if upper not in frames:
            raise InvalidTickerError(f"{ticker}: 픽스처에 OHLCV가 없다")
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

    def load_universe_closes(name, data_config, exchanges, *, use_cache=True, now=None,
                             progress=None):
        return UniverseCloses(name, closes, (), from_cache=True)

    original = (cli.load_ohlcv, cli.load_universe_closes)
    cli.load_ohlcv, cli.load_universe_closes = load_ohlcv, load_universe_closes
    try:
        yield sorted(t for t in frames if t != spec["benchmark"][0].upper())
    finally:
        cli.load_ohlcv, cli.load_universe_closes = original


def show_composition(report: WatchlistReport) -> None:
    """무엇이 얼마나 잡혔는지. '전부 탈락'과 '아무것도 없음'은 다르다."""
    verdicts = summarize_counts(report)
    agreements = agreement_counts(report)

    table = Table(title="[bold]스캔 구성[/bold]", header_style="bold cyan")
    for col in ("구분", "값"):
        table.add_column(col)
    table.add_row("유니버스", f"{report.universe} — 요청 {report.requested}종목")
    table.add_row("진단 성공 / 실패", f"{len(report.entries)} / {len(report.failed)}")
    table.add_row("시장 국면", report.regime.value)
    def joined(counts) -> str:
        return "  ".join(
            f"{key.value} {n}" for key, n in sorted(counts.items(), key=lambda x: x[0].value)
        )

    table.add_row("판정별 종목 수 (중복 셈)", joined(verdicts))
    table.add_row("일치도별 종목 수", joined(agreements))
    table.add_row("BUY를 낸 전략이 있는 종목", str(len(report.buy_entries)))
    console.print(table)
    console.print(
        "  [dim]판정별 수는 종목을 센 것이라 합이 종목 수보다 클 수 있다 — "
        "한 종목이 방법론에 따라 BUY와 AVOID에 동시에 잡히는 것은 정상이다.[/dim]"
    )


def check_scan_matches_diagnose(report: WatchlistReport, sample: int = 5) -> bool:
    """목록의 판정과 개별 진단의 판정이 같은가 — 이 도구의 신뢰가 걸린 성질."""
    config = DEFAULT_CONFIG
    strategies = cli.load_strategies("all", config)
    mismatches: list[str] = []

    for entry in report.entries[:sample]:
        direct = cli.diagnose(entry.ticker, config, strategies)
        listed = [(s.strategy_name, s.verdict) for s in entry.strategies]
        expanded = [(v.strategy_name, v.verdict) for v in direct.strategy_verdicts]
        if listed != expanded or entry.price != direct.price:
            mismatches.append(entry.ticker)

    body = (
        f"표본 {min(sample, len(report.entries))}종목 대조\n"
        + (
            "[green]목록과 상세가 모두 일치한다[/green]"
            if not mismatches
            else f"[red]불일치: {', '.join(mismatches)}[/red]"
        )
        + "\n\n[dim]스캔이 요약용 계산을 따로 했다면 여기서 갈라진다. 지금은 스캔이\n"
        "diagnose()를 그대로 부르므로 같은 판정이 나온다.[/dim]"
    )
    console.print(
        Panel(body, title="[bold]목록 = 상세[/bold]",
              border_style="green" if not mismatches else "red")
    )
    return not mismatches


def show_payload_sizes(report: WatchlistReport) -> None:
    """요약 계약이 존재하는 이유를 실측으로 보여준다."""
    config = DEFAULT_CONFIG
    strategies = cli.load_strategies("all", config)
    one = cli.diagnose(report.entries[0].ticker, config, strategies)

    diagnosis_bytes = len(to_json(one))
    watchlist_bytes = len(watchlist_to_json(report))
    naive = diagnosis_bytes * len(report.entries)

    table = Table(title="[bold]전송량[/bold]", header_style="bold cyan")
    for col in ("항목", "크기"):
        table.add_column(col, justify="left" if col == "항목" else "right")
    table.add_row("진단 리포트 1종목", f"{diagnosis_bytes:,} B")
    table.add_row(
        f"진단 리포트를 그대로 {len(report.entries)}종목", f"{naive:,} B"
    )
    table.add_row(f"워치리스트 {len(report.entries)}종목 (요약 계약)", f"{watchlist_bytes:,} B")
    table.add_row("비율", f"{watchlist_bytes / naive:.1%}")
    console.print(table)
    console.print(
        "  [dim]워치리스트는 표 한 줄에 필요한 것만 담는다. 상세가 필요하면 그 티커를 "
        "개별 진단한다 — 같은 판정이 나온다.[/dim]"
    )


def conclude(results: dict[str, dict]) -> int:
    table = Table(title="[bold]Phase 7 점검표[/bold]", header_style="bold cyan")
    for col in ("항목", "결과", "판정"):
        table.add_column(col)

    ok = True
    for market, result in results.items():
        table.add_row(
            f"{market} 스캔",
            f"{result['entries']}종목 진단 / 실패 {result['failed']} / "
            f"{result['seconds']:.1f}초 ({result['ms_per_ticker']:.0f}ms per 종목)",
            "[green]PASS[/green]" if result["entries"] else "[red]FAIL[/red]",
        )
        ok &= bool(result["entries"])
        table.add_row(
            f"{market} 목록 = 상세",
            "일치" if result["consistent"] else "불일치",
            "[green]PASS[/green]" if result["consistent"] else "[red]FAIL[/red]",
        )
        ok &= result["consistent"]
        table.add_row(
            f"{market} 정렬",
            "BUY 수 -> 게이트 진행률 내림차순",
            "[green]PASS[/green]" if result["sorted"] else "[red]FAIL[/red]",
        )
        ok &= result["sorted"]
    console.print(table)

    console.print(
        Panel(
            "\n".join(
                [
                    "[bold]확인된 것[/bold]",
                    "  유니버스를 훑어 '볼 만한 것'을 게이트 근접도 순으로 추린다.",
                    "  목록의 판정과 개별 진단의 판정이 같다 (스캔이 diagnose를 그대로 부른다).",
                    "  요약 계약 덕에 전송량이 진단 리포트 나열 대비 크게 줄었다.",
                    "",
                    "[bold]말할 수 없는 것[/bold]",
                    "  이 목록이 좋은 후보인지. 정렬은 '게이트에 가깝다'는 사실일 뿐",
                    "  성과 예측이 아니다 — Phase 6에서 본 대로 점수와 성과의 관계는 약하다.",
                    "  생존편향도 그대로다 (유니버스가 현재 상장 종목만 담는다).",
                ]
            ),
            border_style="green" if ok else "red",
            title="[bold]Phase 7 결론[/bold]",
        )
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7 워치리스트 검증")
    parser.add_argument("--market", choices=list(MARKETS), default=None)
    parser.add_argument("--top", type=int, default=12, help="표에 표시할 종목 수")
    args = parser.parse_args()

    config = DEFAULT_CONFIG
    results: dict[str, dict] = {}

    for market in [args.market] if args.market else list(MARKETS):
        spec = MARKETS[market]
        console.print()
        console.rule(f"[bold]{market}[/bold] — {spec['universe']}")

        with fixture_data(spec) as tickers:
            strategies = cli.load_strategies("all", config)
            started = time.perf_counter()
            report = cli.scan(spec["universe"], config, strategies, tickers=tickers)
            elapsed = time.perf_counter() - started

            cli.render_watchlist(report, top=args.top)
            show_composition(report)
            console.print()
            consistent = check_scan_matches_diagnose(report)
            console.print()
            show_payload_sizes(report)

            keys = [(len(e.buy_strategies), e.best_gate_progress) for e in report.entries]
            results[market] = {
                "entries": len(report.entries),
                "failed": len(report.failed),
                "seconds": elapsed,
                "ms_per_ticker": elapsed * 1000 / max(len(tickers), 1),
                "consistent": consistent,
                "sorted": keys == sorted(keys, reverse=True),
            }
        console.print()

    return conclude(results)


if __name__ == "__main__":
    raise SystemExit(main())
