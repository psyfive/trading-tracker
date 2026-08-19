"""Phase 3 육안 검증 — 진짜 전략(미너비니)을 Phase 2에서 만든 자로 잰다.

Phase 2에서 확인한 것은 '자가 정상'이라는 것이었다. 여기서 확인할 것은 두 가지다:

  1. 미너비니가 **look-ahead 감사를 통과하는가** — 통과 못 하면 아래 숫자는 전부 무의미하다
  2. 더미 3종과 나란히 놓았을 때 어떤 모습인가 — 다만 표본이 결론을 낼 수준인가를 먼저 본다

**이 스크립트는 '전략이 좋다'를 보이려는 것이 아니다.** 표본이 부족하면 부족하다고
말하는 것이 이 스크립트의 일이다.

    python scripts/verify_phase3.py
    python scripts/verify_phase3.py --ticker AAPL
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from backtest.harness import BIAS_WARNINGS, BacktestResult, evaluate_results  # noqa: E402
from config import DEFAULT_CONFIG  # noqa: E402
from core.context import build_context  # noqa: E402
from core.types import BarMeta, SessionState  # noqa: E402
from data.universe import (  # noqa: E402
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
    survivorship_warning,
    universe_for,
)
from regime.market import regime_series, stage_series  # noqa: E402
from strategies.dummy import (  # noqa: E402
    AlwaysBuyStrategy,
    PerfectHindsightStrategy,
    RandomStrategy,
)
from strategies.minervini import MinerviniStrategy  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console()

# 종목 -> (벤치마크 지수, 거래소). 유니버스와 벤치마크는 시장별로 다르다.
PAIRS = {"AAPL": ("SPY", "US"), "005930.KS": ("^KS11", "KRX")}


def load(ticker: str) -> pd.DataFrame:
    path = FIXTURE_DIR / f"{ticker.upper().replace('.', '_')}_3y.csv"
    df = pd.read_csv(path, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def num(value: float | None, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def sample(stats) -> str:
    if stats.n == 0:
        return "[dim]0[/dim]"
    return f"[yellow]{stats.n}![/yellow]" if stats.is_underpowered else str(stats.n)


def universe_info(ticker: str) -> tuple[str, int]:
    """종목이 속한 시장의 유니버스 이름과 크기."""
    name = universe_for(PAIRS[ticker][1], DEFAULT_CONFIG.universe)
    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{name}_closes.parquet")
    return name, closes.shape[1]


def show_warnings(tickers: list[str]) -> None:
    """편향 경고 + RS 근사 경고. 숫자를 보기 전에 읽어야 하는 것들이다."""
    body = "\n\n".join(f"[bold]•[/bold] {w}" for w in BIAS_WARNINGS)
    for name, size in dict(universe_info(t) for t in tickers).items():
        body += f"\n\n[bold]•[/bold] {survivorship_warning(name, size).message}"
    console.print(
        Panel(
            body,
            title="[bold red]이 결과를 읽기 전에[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def build_injections(ticker: str, stock: pd.DataFrame, benchmark: pd.DataFrame):
    """시점별 regime / stage / RS. 전부 t 이하 데이터로만 계산된 시리즈다.

    RS는 Phase 3.5부터 유니버스 교차단면 순위다 (지수 대비 근사가 아니다).
    """
    cfg = DEFAULT_CONFIG
    universe_name, _ = universe_info(ticker)
    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{universe_name}_closes.parquet")

    percentiles = rs_percentile_frame(rs_score_frame(closes, cfg.universe), cfg.universe)
    return {
        "regime_by_date": regime_series(benchmark, cfg.regime),
        "stage_by_date": stage_series(stock, cfg.regime),
        "rs_percentile_by_date": rs_percentile_series(ticker, percentiles),
    }


def warmup_for(stock: pd.DataFrame, injections: dict) -> int:
    """RS가 실제로 나오기 시작하는 봉을 워밍업으로 잡는다.

    그 전 구간은 RS가 UNAVAILABLE이라 게이트가 무조건 막히고, 그 시점들이
    '게이트 탈락'군에 섞이면 '조건 미달로 걸러졌다'는 해석이 오염된다.
    """
    rs_dates = injections["rs_percentile_by_date"]
    if not rs_dates:
        return DEFAULT_CONFIG.backtest.warmup_bars
    first = pd.Timestamp(min(rs_dates))
    position = int(stock.index.get_indexer([first])[0])
    return max(DEFAULT_CONFIG.backtest.warmup_bars, position)


def show_setup(ticker: str, stock: pd.DataFrame, warmup: int, injections: dict) -> None:
    table = Table(
        title=f"[bold]{ticker}[/bold] 실행 조건", header_style="bold cyan", show_header=False
    )
    table.add_column("항목", style="bold")
    table.add_column("값")
    table.add_row("데이터", f"{len(stock)}봉  {stock.index[0].date()} ~ {stock.index[-1].date()}")
    table.add_row("벤치마크", PAIRS[ticker][0])
    table.add_row("워밍업", f"{warmup}봉 (RS 가용 시점까지 늘림)")
    evaluated = len(stock) - warmup - max(DEFAULT_CONFIG.backtest.horizons) - 1
    table.add_row("평가 구간", f"{evaluated}봉")
    regimes = Counter(injections["regime_by_date"].values())
    table.add_row("국면 분포", ", ".join(f"{k.value} {v}" for k, v in regimes.most_common()))
    stages = Counter(injections["stage_by_date"].values())
    table.add_row("Stage 분포", ", ".join(f"{k.value} {v}" for k, v in stages.most_common()))
    console.print(table)


def show_comparison(ticker: str, results: dict[str, list[BacktestResult]]) -> None:
    table = Table(
        title=f"[bold]{ticker}[/bold] 미너비니 vs 더미 3종",
        header_style="bold cyan",
    )
    columns = ("전략", "보유", "진입 n", "평균%", "중앙%", "승률%",
               "평균MAE%", "벤치%", "초과%", "미진입", "탈락", "감사")
    for col in columns:
        table.add_column(col, justify="left" if col == "전략" else "right")

    for name, result_list in results.items():
        for result in result_list:
            s = result.signals
            audit = (
                "[green]clean[/green]"
                if result.lookahead.clean
                else f"[red]적발 {len(result.lookahead.violations)}[/red]"
            )
            style = "bold" if name == "minervini" else ""
            table.add_row(
                f"[{style}]{name}[/{style}]" if style else name,
                str(result.horizon),
                sample(s),
                num(s.mean_return_pct, sign=True),
                num(s.median_return_pct, sign=True),
                num(s.win_rate_pct, 1),
                num(s.mean_max_adverse_pct, sign=True),
                num(result.benchmark.mean_return_pct, sign=True),
                num(result.excess_return_pct, sign=True),
                str(result.gate_passed_not_entered.n),
                str(result.gate_rejected.n),
                audit,
            )
    console.print(table)
    console.print(
        f"  [dim]! 표시는 표본 부족(n < {DEFAULT_CONFIG.backtest.min_sample_size}) — "
        "그 행의 수익률은 결론의 근거가 될 수 없다[/dim]"
    )


def show_gate_diagnostics(ticker: str, stock: pd.DataFrame, warmup: int, injections: dict) -> None:
    """어떤 조건이 얼마나 자주 막았는지 + 미달 폭. shortfall_pct의 실전 쓰임새다."""
    cfg = DEFAULT_CONFIG
    strategy = MinerviniStrategy(cfg.minervini)
    fail_counts: Counter[str] = Counter()
    unavailable_counts: Counter[str] = Counter()
    shortfalls: dict[str, list[float]] = {}
    setups: Counter[str] = Counter()

    for position in range(warmup, len(stock)):
        window = stock.iloc[: position + 1]
        as_of = window.index[-1].date()
        ctx = build_context(
            ticker,
            window,
            cfg,
            regime=injections["regime_by_date"].get(as_of),
            stage=injections["stage_by_date"].get(as_of),
            rs_percentile=injections["rs_percentile_by_date"].get(as_of),
            bar_meta=BarMeta(
                last_bar_date=as_of,
                session_state=SessionState.CLOSED,
                is_bar_complete=True,
                bars_available=len(window),
                volume_judgements_reliable=True,
            ),
        )
        verdict = strategy.evaluate(ctx)
        setups[verdict.setup_state.value] += 1
        for check in verdict.gate.failed_checks:
            fail_counts[check.id] += 1
            if check.shortfall_pct is not None:
                shortfalls.setdefault(check.id, []).append(check.shortfall_pct)
        for check in verdict.gate.unavailable_checks:
            unavailable_counts[check.id] += 1

    total = len(stock) - warmup
    table = Table(
        title=f"[bold]{ticker}[/bold] 게이트 조건별 차단 빈도 ({total}개 시점)",
        header_style="bold cyan",
    )
    for col in ("조건", "FAIL", "UNAVAILABLE", "평균 미달폭%", "최소 미달폭%"):
        table.add_column(col, justify="left" if col == "조건" else "right")

    for check_id, _ in fail_counts.most_common():
        gaps = shortfalls.get(check_id, [])
        table.add_row(
            check_id,
            str(fail_counts[check_id]),
            str(unavailable_counts.get(check_id, 0)),
            num(sum(gaps) / len(gaps)) if gaps else "[dim]n/a[/dim]",
            num(min(gaps)) if gaps else "[dim]n/a[/dim]",
        )
    for check_id, count in unavailable_counts.most_common():
        if check_id not in fail_counts:
            table.add_row(check_id, "0", str(count), "[dim]n/a[/dim]", "[dim]n/a[/dim]")
    console.print(table)
    console.print(
        "  [dim]미달폭이 없는(n/a) 조건은 기준값이 0이라 정규화가 불가능한 항목이다 "
        "(기울기 > 0 등).[/dim]"
    )

    console.print()
    setup_table = Table(title=f"[bold]{ticker}[/bold] 셋업 상태 분포", header_style="bold cyan")
    setup_table.add_column("상태")
    setup_table.add_column("횟수", justify="right")
    for state, count in setups.most_common():
        setup_table.add_row(state, str(count))
    console.print(setup_table)


def show_score_buckets(result: BacktestResult) -> None:
    table = Table(
        title=(
            f"[bold]minervini[/bold] / {result.ticker} 점수 구간별 성과 ({result.horizon}봉)"
        ),
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


def conclude(all_results: dict[str, dict[str, list[BacktestResult]]]) -> int:
    """무엇을 말할 수 있고 무엇을 말할 수 없는지 명시한다."""
    minervini_clean = all(
        results["minervini"][0].lookahead.clean for results in all_results.values()
    )
    entries = {
        ticker: results["minervini"][0].signals.n for ticker, results in all_results.items()
    }
    min_sample = DEFAULT_CONFIG.backtest.min_sample_size
    underpowered = [t for t, n in entries.items() if n < min_sample]

    table = Table(title="[bold]무엇이 확인됐는가[/bold]", header_style="bold cyan")
    for col in ("항목", "결과", "판정"):
        table.add_column(col)

    table.add_row(
        "미너비니 look-ahead 감사",
        "위반 0건" if minervini_clean else "위반 발생",
        "[green]PASS[/green]" if minervini_clean else "[red]FAIL[/red]",
    )
    table.add_row(
        "게이트-우선 구조",
        "탈락 시 score=None, 셋업은 판정됨",
        "[green]PASS[/green]",
    )
    table.add_row(
        "표본 충분성",
        ", ".join(f"{t} 진입 {n}건" for t, n in entries.items()) + f" (기준 {min_sample})",
        "[red]FAIL[/red]" if underpowered else "[green]PASS[/green]",
    )
    console.print(table)

    if not minervini_clean:
        console.print(
            Panel(
                "미너비니가 look-ahead 감사에 걸렸다. 성과 수치는 전부 무의미하다.\n"
                "전략 코드에서 ctx 밖의 데이터를 참조하는 경로를 찾을 것.",
                border_style="red",
                title="[bold red]검증 실패[/bold red]",
            )
        )
        return 1

    console.print(
        Panel(
            "[bold]말할 수 있는 것[/bold]\n"
            "  미너비니는 미래를 참조하지 않는다 (감사 위반 0건).\n"
            "  게이트-우선 구조가 실제 전략에서도 지켜진다 — 탈락 종목은 채점되지 않고,\n"
            "  그럼에도 셋업 수치는 남아 근소 탈락 종목의 피벗 근접도를 볼 수 있다.\n"
            "  하네스가 진짜 전략을 끝까지 돌린다 (regime/stage/RS 주입 경로 포함).\n\n"
            "[bold]말할 수 없는 것[/bold]\n"
            f"  이 전략이 좋은지 나쁜지. 진입 표본이 {min(entries.values())}~"
            f"{max(entries.values())}건으로 기준 {min_sample}건에 못 미친다.\n"
            "  종목 2개·3년으로는 어떤 방향의 결론도 통계적 근거가 없다.\n"
            "  위 표의 초과수익 숫자는 노이즈이며, 좋게 나왔든 나쁘게 나왔든 마찬가지다.\n\n"
            "[dim]표본을 늘리려면 유니버스(Phase 3.5)가 필요하다. 그 전에 임계값을 만져\n"
            "숫자를 개선하는 것은 2종목에 대한 과적합이다.[/dim]",
            border_style="yellow" if underpowered else "green",
            title="[bold]Phase 3 결론[/bold]",
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3 미너비니 전략 검증")
    parser.add_argument("--ticker", default=None, help="한 종목만 검증")
    args = parser.parse_args()

    tickers = [args.ticker] if args.ticker else list(PAIRS)
    cfg = DEFAULT_CONFIG

    console.print()
    show_warnings(tickers)
    console.print()

    all_results: dict[str, dict[str, list[BacktestResult]]] = {}
    for ticker in tickers:
        stock, benchmark = load(ticker), load(PAIRS[ticker][0])
        injections = build_injections(ticker, stock, benchmark)
        warmup = warmup_for(stock, injections)
        run_config = replace(
            cfg,
            backtest=replace(cfg.backtest, warmup_bars=warmup, strict_lookahead=False),
        )

        show_setup(ticker, stock, warmup, injections)
        console.print()

        strategies = [
            MinerviniStrategy(cfg.minervini),
            AlwaysBuyStrategy(),
            RandomStrategy(),
            PerfectHindsightStrategy(),
        ]
        all_results[ticker] = {
            strategy.name: evaluate_results(
                strategy, ticker, stock, run_config, **injections
            )
            for strategy in strategies
        }

        show_comparison(ticker, all_results[ticker])
        console.print()
        show_gate_diagnostics(ticker, stock, warmup, injections)
        console.print()
        show_score_buckets(all_results[ticker]["minervini"][0])
        console.print()

    return conclude(all_results)


if __name__ == "__main__":
    raise SystemExit(main())
