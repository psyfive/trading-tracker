"""Phase 3.5 육안 검증 — 유니버스를 갖춘 뒤 무엇이 달라졌는가.

Phase 3의 결론은 "미너비니가 미래를 참조하지는 않지만, 표본이 5~24건이라 좋은지
나쁜지는 말할 수 없다"였다. Phase 3.5는 그 두 결손을 메운다:

  1. **진짜 RS 백분위** — 지수 대비 근사가 아니라 유니버스 내 교차단면 순위
  2. **표본 확대** — 39종목 패널로 풀링해 min_sample_size를 넘기는지

여전히 못 메우는 것은 생존편향이다. 유니버스 목록이 현재 상장 종목만 담기 때문이며,
그 사실은 리포트 상단에 계속 출력된다.

    python scripts/verify_phase3_5.py
    python scripts/verify_phase3_5.py --market us      # 한 시장만
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from backtest.harness import BIAS_WARNINGS, PanelResult, evaluate_panel  # noqa: E402
from config import DEFAULT_CONFIG  # noqa: E402
from data.universe import (  # noqa: E402
    load_universe_tickers,
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
    small_universe_warning,
    survivorship_warning,
)
from regime.market import regime_series, stage_series  # noqa: E402
from strategies.dummy import AlwaysBuyStrategy, RandomStrategy  # noqa: E402
from strategies.minervini import MinerviniStrategy  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console()

MARKETS = {
    "us": {"universe": "us_large", "benchmark": "SPY", "panel": "panel_us_large_ohlcv"},
    "kospi": {"universe": "kospi", "benchmark": "^KS11", "panel": "panel_kospi_ohlcv"},
}


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / f"{name}.csv", index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def load_panel(name: str) -> dict[str, pd.DataFrame]:
    """long 포맷 패널을 티커별 OHLCV로 되돌린다."""
    panel = pd.read_parquet(FIXTURE_DIR / f"{name}.parquet")
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in panel.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date").sort_index()
        frame.index.name = "date"
        frames[str(ticker)] = frame.astype(float)
    return frames


def num(value: float | None, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def sample(stats) -> str:
    if stats.n == 0:
        return "[dim]0[/dim]"
    return f"[yellow]{stats.n}![/yellow]" if stats.is_underpowered else f"[green]{stats.n}[/green]"


def show_warnings(universe_name: str, size: int) -> None:
    body = "\n\n".join(f"[bold]•[/bold] {w}" for w in BIAS_WARNINGS[::2])
    body += f"\n\n[bold]•[/bold] {survivorship_warning(universe_name, size).message}"
    if size < DEFAULT_CONFIG.universe.min_universe_size * 2:
        body += (
            f"\n\n[bold]•[/bold] "
            f"{small_universe_warning(size, DEFAULT_CONFIG.universe.min_universe_size).message}"
        )
    console.print(
        Panel(
            body,
            title="[bold red]이 결과를 읽기 전에[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def build_market(market: str):
    """한 시장의 유니버스·RS·패널·주입 시리즈를 모두 준비한다."""
    spec = MARKETS[market]
    cfg = DEFAULT_CONFIG

    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{spec['universe']}_closes.parquet")
    scores = rs_score_frame(closes, cfg.universe)
    percentiles = rs_percentile_frame(scores, cfg.universe)
    benchmark = load_csv(f"{spec['benchmark'].replace('.', '_')}_3y")
    regimes = regime_series(benchmark, cfg.regime)

    frames = load_panel(spec["panel"])
    injections: dict[str, dict] = {}
    warmups: dict[str, int] = {}

    for ticker, df in frames.items():
        rs = rs_percentile_series(ticker, percentiles)
        injections[ticker] = {
            "regime_by_date": regimes,
            "stage_by_date": stage_series(df, cfg.regime),
            "rs_percentile_by_date": rs,
        }
        if rs:
            first = pd.Timestamp(min(rs))
            position = int(df.index.get_indexer([first])[0])
            warmups[ticker] = max(cfg.backtest.warmup_bars, position)

    return {
        "spec": spec,
        "universe_size": closes.shape[1],
        "percentiles": percentiles,
        "frames": frames,
        "injections": injections,
        "warmups": warmups,
    }


def show_universe_summary(market: str, data: dict) -> None:
    cfg = DEFAULT_CONFIG
    spec = data["spec"]
    percentiles = data["percentiles"]
    available = percentiles.dropna(how="all")

    table = Table(
        title=f"[bold]{market}[/bold] 유니버스", header_style="bold cyan", show_header=False
    )
    table.add_column("항목", style="bold")
    table.add_column("값")
    table.add_row("유니버스", f"{spec['universe']} — {data['universe_size']}종목")
    table.add_row("요청 목록", f"{len(load_universe_tickers(spec['universe']))}종목")
    table.add_row("백테스트 패널", f"{len(data['frames'])}종목 (OHLCV 보유)")
    table.add_row("벤치마크", spec["benchmark"])
    table.add_row(
        "RS 가용 구간",
        f"{available.index[0].date()} ~ {available.index[-1].date()} ({len(available)}일)",
    )
    table.add_row("백분위 해상도", f"약 {100.0 / data['universe_size']:.1f}p 단위")
    table.add_row("min_universe_size", str(cfg.universe.min_universe_size))
    console.print(table)


def show_rs_examples(market: str, data: dict) -> None:
    """RS가 실제로 성과 순위를 반영하는지 눈으로 확인."""
    percentiles = data["percentiles"]
    closes = pd.read_parquet(
        FIXTURE_DIR / f"universe_{data['spec']['universe']}_closes.parquet"
    )
    last = percentiles.dropna(how="all").index[-1]
    lookback = DEFAULT_CONFIG.universe.rs_lookback_days
    returns = ((closes.loc[last] / closes.iloc[-lookback - 1] - 1.0) * 100.0).dropna()
    ranked = percentiles.loc[last].dropna().sort_values(ascending=False)

    table = Table(
        title=f"[bold]{market}[/bold] RS 백분위 vs 실제 1년 수익률 ({last.date()})",
        header_style="bold cyan",
    )
    for col in ("구간", "티커", "RS", "1년 수익률%"):
        table.add_column(col, justify="left" if col in ("구간", "티커") else "right")

    for label, rows in (("상위", ranked.head(3)), ("하위", ranked.tail(3))):
        for ticker, rs in rows.items():
            table.add_row(label, ticker, f"{rs:.0f}", num(returns.get(ticker), 1, sign=True))
    console.print(table)
    console.print(
        "  [dim]교차단면 순위이므로 RS가 높은 종목이 실제로 많이 올랐어야 한다. "
        "Phase 3의 근사(자기 과거 대비)는 이 대응이 성립하지 않았다.[/dim]"
    )


def show_panel_results(market: str, results: dict[str, list[PanelResult]]) -> None:
    table = Table(
        title=f"[bold]{market}[/bold] 다종목 풀링 결과",
        header_style="bold cyan",
    )
    columns = ("전략", "보유", "종목", "진입종목", "진입 n", "평균%", "중앙%",
               "승률%", "벤치%", "초과%", "2SE↓", "2SE↑", "감사")
    for col in columns:
        table.add_column(col, justify="left" if col == "전략" else "right")

    for name, panel_list in results.items():
        for result in panel_list:
            stderr = result.signals.stderr_return_pct
            # 보유기간이 겹쳐 수익률이 자기상관되므로 표준오차의 참값은 구간이다.
            #   2SE↓ = iid 가정 (겹침을 무시 — 유의성을 과대평가하는 하한)
            #   2SE↑ = 전 진입이 한 종목에서 연속으로 났다고 보는 보정 (과대 보정한 상한)
            # 실제 진입은 여러 종목에 흩어져 있으므로 참값은 둘 사이다.
            # 초과수익이 2SE↑보다 크면 어느 가정에서도 유의하고,
            # 2SE↓보다 작으면 어느 가정에서도 유의하지 않다.
            effective = max(1, result.signals.n // result.horizon)
            adjusted = (
                None
                if stderr is None
                else stderr * (result.signals.n / effective) ** 0.5
            )
            table.add_row(
                f"[bold]{name}[/bold]" if name == "minervini" else name,
                str(result.horizon),
                str(result.tickers),
                str(result.tickers_with_entries),
                sample(result.signals),
                num(result.signals.mean_return_pct, sign=True),
                num(result.signals.median_return_pct, sign=True),
                num(result.signals.win_rate_pct, 1),
                num(result.benchmark.mean_return_pct, sign=True),
                num(result.excess_return_pct, sign=True),
                num(None if stderr is None else 2 * stderr),
                num(None if adjusted is None else 2 * adjusted),
                "[green]clean[/green]" if result.audit_clean else "[red]위반[/red]",
            )
    console.print(table)
    console.print(
        "  [dim]2SE↓ = iid 가정(겹침 무시), 2SE↑ = 전 진입이 한 종목에서 연속으로 났다고 본 "
        "과대 보정. 진입이 여러 종목에 흩어져 있으므로 참값은 둘 사이다.\n"
        "  |초과%| > 2SE↑ 이면 어느 가정에서도 유의하고, < 2SE↓ 이면 어느 가정에서도 "
        "유의하지 않다. 그 사이면 결론을 유보한다.[/dim]"
    )


def show_entry_distribution(result: PanelResult) -> None:
    entries = [(t, n) for t, n in result.entries_by_ticker if n > 0]
    zero = [t for t, n in result.entries_by_ticker if n == 0]

    table = Table(
        title=f"[bold]minervini[/bold] 종목별 진입 건수 ({result.horizon}봉)",
        header_style="bold cyan",
    )
    table.add_column("티커")
    table.add_column("진입", justify="right")
    for ticker, count in sorted(entries, key=lambda x: -x[1])[:10]:
        table.add_row(ticker, str(count))
    console.print(table)
    console.print(
        f"  [dim]진입이 발생한 종목 {len(entries)}개 / 진입 0건 {len(zero)}개. "
        f"종목당 평균 {result.signals.n / max(len(entries), 1):.1f}건 — "
        "미너비니는 선별적인 방법론이라 종목 하나로는 표본이 모이지 않는다.[/dim]"
    )
    if result.skipped_tickers:
        console.print(
            f"  [yellow]봉 부족으로 제외 {len(result.skipped_tickers)}개: "
            f"{', '.join(result.skipped_tickers)}[/yellow]"
        )


def show_score_buckets(result: PanelResult) -> None:
    table = Table(
        title=f"[bold]minervini[/bold] 점수 구간별 성과 (풀링, {result.horizon}봉)",
        header_style="bold cyan",
    )
    for col in ("구간", "n", "평균%", "중앙%", "승률%"):
        table.add_column(col, justify="left" if col == "구간" else "right")
    for bucket in result.by_score_bucket:
        table.add_row(
            bucket.label,
            sample(bucket),
            num(bucket.mean_return_pct, sign=True),
            num(bucket.median_return_pct, sign=True),
            num(bucket.win_rate_pct, 1),
        )
    console.print(table)
    console.print(
        "  [dim]점수가 높을수록 성과가 좋아야 SCORE 단계가 의미를 갖는다. "
        "단조 증가하지 않으면 채점 항목이 타이밍을 못 재고 있다는 뜻이다.[/dim]"
    )


def conclude(all_results: dict[str, dict[str, list[PanelResult]]]) -> int:
    cfg = DEFAULT_CONFIG
    minimum = cfg.backtest.min_sample_size

    table = Table(title="[bold]Phase 3 대비 무엇이 달라졌는가[/bold]", header_style="bold cyan")
    for col in ("항목", "Phase 3", "Phase 3.5", "판정"):
        table.add_column(col)

    sizes = {
        market: results["minervini"][0].signals.n for market, results in all_results.items()
    }
    enough = all(n >= minimum for n in sizes.values())
    audits_clean = all(
        results["minervini"][0].audit_clean for results in all_results.values()
    )

    table.add_row(
        "RS 백분위",
        "지수 대비 근사 (자기 과거 순위)",
        "유니버스 교차단면 순위",
        "[green]교체됨[/green]",
    )
    table.add_row(
        "진입 표본",
        "종목당 5~24건",
        ", ".join(f"{m} {n}건" for m, n in sizes.items()) + f" (기준 {minimum})",
        "[green]PASS[/green]" if enough else "[yellow]일부 미달[/yellow]",
    )
    table.add_row(
        "look-ahead 감사",
        "위반 0건",
        "위반 0건" if audits_clean else "위반 발생",
        "[green]PASS[/green]" if audits_clean else "[red]FAIL[/red]",
    )
    table.add_row("생존편향", "제거 못 함", "제거 못 함", "[yellow]잔존[/yellow]")
    console.print(table)

    if not audits_clean:
        console.print(
            Panel(
                "감사 위반이 발생했다. 유니버스 RS 주입 경로에 미래 참조가 섞였을 가능성이 높다.\n"
                "data/universe.py의 시점 정합성부터 확인할 것.",
                border_style="red",
                title="[bold red]검증 실패[/bold red]",
            )
        )
        return 1

    lines = [
        "[bold]확인된 것[/bold]",
        "  RS가 '자기 과거 대비 가속도'에서 '유니버스 대비 수준'으로 바뀌었다.",
        "  다종목 풀링으로 진입 표본이 " + ", ".join(f"{m} {n}건" for m, n in sizes.items()) + ".",
        "  유니버스 RS를 주입해도 look-ahead 감사는 여전히 깨끗하다.",
        "",
        "[bold]여전히 말할 수 없는 것[/bold]",
        "  생존편향이 남아 있다. 사라진 종목이 유니버스 분모에서 빠져 있으므로",
        "  RS 백분위와 성과 모두 낙관 쪽으로 치우친다.",
        "  기간 편향도 그대로다 — 3년 한 구간이며 다른 국면의 결과는 알 수 없다.",
    ]
    if not enough:
        lines.insert(
            3, f"  [yellow]단, 표본이 {minimum}건에 못 미치는 시장이 있다.[/yellow]"
        )
    console.print(
        Panel(
            "\n".join(lines),
            border_style="green" if enough else "yellow",
            title="[bold]Phase 3.5 결론[/bold]",
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3.5 유니버스 RS 검증")
    parser.add_argument("--market", choices=list(MARKETS), default=None)
    args = parser.parse_args()

    markets = [args.market] if args.market else list(MARKETS)
    cfg = DEFAULT_CONFIG
    all_results: dict[str, dict[str, list[PanelResult]]] = {}

    for market in markets:
        data = build_market(market)
        console.print()
        show_warnings(data["spec"]["universe"], data["universe_size"])
        console.print()
        show_universe_summary(market, data)
        console.print()
        show_rs_examples(market, data)
        console.print()

        run_config = replace(
            cfg, backtest=replace(cfg.backtest, strict_lookahead=False)
        )
        shared = {
            "frames": data["frames"],
            "injections": data["injections"],
            "warmups": data["warmups"],
        }
        all_results[market] = {
            "minervini": evaluate_panel(
                lambda: MinerviniStrategy(cfg.minervini), config=run_config, **shared
            ),
            "always_buy": evaluate_panel(
                AlwaysBuyStrategy, config=run_config, **shared
            ),
            "random": evaluate_panel(RandomStrategy, config=run_config, **shared),
        }

        show_panel_results(market, all_results[market])
        console.print()
        show_entry_distribution(all_results[market]["minervini"][0])
        console.print()
        show_score_buckets(all_results[market]["minervini"][0])
        console.print()

    return conclude(all_results)


if __name__ == "__main__":
    raise SystemExit(main())
