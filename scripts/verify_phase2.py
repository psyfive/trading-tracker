"""Phase 2 육안 검증 — 백테스트 하네스가 '자'로서 정상인지 확인한다.

더미 전략 3종의 결과는 **미리 예측된다**. 예측이 맞으면 하네스가 정상이고,
어긋나면 전략이 아니라 하네스를 의심해야 한다.

  1. AlwaysBuy        ~= buy-and-hold — 하네스를 거치지 않고 pandas로 **독립 계산**한
                         무조건부 평균과 비교한다 (하네스 내부 벤치마크와의 비교는
                         같은 계산에서 나오므로 동어반복이라 증명력이 없다)
  2. Random           ~= 시장 평균 (초과수익이 표준오차 범위 안. 보유기간이 겹쳐
                         수익률이 자기상관되므로 유효표본 n/horizon으로 보정)
  3. PerfectHindsight -> look-ahead 감사에 적발

    python scripts/verify_phase2.py
    python scripts/verify_phase2.py --warmup 400   # 빠르게 돌릴 때
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from backtest.harness import (  # noqa: E402
    BIAS_WARNINGS,
    BacktestResult,
    GroupStats,
    evaluate_results,
)
from config import DEFAULT_CONFIG  # noqa: E402
from strategies.dummy import (  # noqa: E402
    AlwaysBuyStrategy,
    PerfectHindsightStrategy,
    RandomStrategy,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console()

TICKERS = ["AAPL", "005930.KS"]
SIGMA = 2.0  # Random 판정에 쓰는 표준오차 배수
EXCESS_HIGHLIGHT_PCT = 5.0  # 표시용 — 초과수익이 이보다 크면 빨간색으로 강조 (판정 아님)


def independent_unconditional_mean_pct(df, config, horizon: int) -> float | None:
    """하네스 코드를 전혀 쓰지 않고 계산한 '매 봉 진입' 평균 수익률.

    예측 1의 기준선이다. 하네스 내부 벤치마크는 시그널과 같은 forward_outcome
    호출에서 나오므로 그것과의 비교는 동어반복이다. 여기서는 진입 규칙
    (시그널 다음 봉 시가 -> horizon-1봉 뒤 종가)을 별도 코드로 다시 구현해서,
    하네스의 오프셋/정렬 배선이 틀리면 실제로 어긋나게 만든다.
    """
    bt = config.backtest
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    returns: list[float] = []
    for position in range(bt.warmup_bars, len(df) - max(bt.horizons) - bt.entry_offset_bars):
        entry_position = position + bt.entry_offset_bars
        exit_position = entry_position + horizon - 1
        if exit_position >= len(df) or opens[entry_position] <= 0.0:
            continue
        entry = opens[entry_position]
        returns.append((closes[exit_position] - entry) / entry * 100.0)
    return statistics.fmean(returns) if returns else None


def load(ticker: str) -> pd.DataFrame:
    path = FIXTURE_DIR / f"{ticker.upper().replace('.', '_')}_3y.csv"
    df = pd.read_csv(path, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def num(value: float | None, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def sample(stats: GroupStats) -> str:
    """표본 수. 부족하면 눈에 띄게 표시한다."""
    if stats.n == 0:
        return "[dim]0[/dim]"
    if stats.is_underpowered:
        return f"[yellow]{stats.n}![/yellow]"
    return str(stats.n)


def show_bias_warnings() -> None:
    """리포트 상단 고정. 예쁜 숫자가 나왔을 때 스스로를 속이지 않기 위한 장치다."""
    body = "\n\n".join(f"[bold]•[/bold] {w}" for w in BIAS_WARNINGS)
    console.print(
        Panel(
            body,
            title="[bold red]이 결과를 읽기 전에[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def show_setup(config, tickers: list[str], frames: dict[str, pd.DataFrame]) -> None:
    bt = config.backtest
    table = Table(title="[bold]실행 조건[/bold]", header_style="bold cyan", show_header=False)
    table.add_column("항목", style="bold")
    table.add_column("값")
    table.add_row("종목", ", ".join(f"{t} ({len(frames[t])}봉)" for t in tickers))
    table.add_row("워밍업", f"{bt.warmup_bars}봉")
    table.add_row("보유기간", ", ".join(f"{h}봉" for h in bt.horizons))
    table.add_row(
        "진입 규칙", f"verdict=BUY일 때만, 시그널 봉의 {bt.entry_offset_bars}봉 뒤 **시가**"
    )
    table.add_row("벤치마크", "같은 종목 무조건부 매수 (지수 대비는 Phase 3.5)")
    table.add_row("표본 경고 기준", f"n < {bt.min_sample_size}")
    console.print(table)


def show_comparison(results: dict[str, list[BacktestResult]], ticker: str) -> None:
    table = Table(
        title=f"[bold]{ticker}[/bold] 더미 전략 비교",
        header_style="bold cyan",
    )
    columns = (
        "전략", "보유", "n", "평균%", "중앙%", "승률%",
        "평균MAE%", "최악MAE%", "벤치%", "초과%", "감사",
    )
    for col in columns:
        table.add_column(col, justify="left" if col == "전략" else "right")

    for name, result_list in results.items():
        for result in result_list:
            s, b = result.signals, result.benchmark
            audit = (
                "[green]clean[/green]"
                if result.lookahead.clean
                else f"[red]적발 {len(result.lookahead.violations)}[/red]"
            )
            excess = result.excess_return_pct
            excess_style = (
                "red" if excess is not None and abs(excess) > EXCESS_HIGHLIGHT_PCT else "white"
            )
            table.add_row(
                name,
                str(result.horizon),
                sample(s),
                num(s.mean_return_pct, sign=True),
                num(s.median_return_pct, sign=True),
                num(s.win_rate_pct, 1),
                num(s.mean_max_adverse_pct, sign=True),
                num(s.worst_max_adverse_pct, sign=True),
                num(b.mean_return_pct, sign=True),
                f"[{excess_style}]{num(excess, sign=True)}[/{excess_style}]",
                audit,
            )
    console.print(table)


def show_gate_effect(results: dict[str, list[BacktestResult]], ticker: str) -> None:
    """진입/미진입/탈락 3집단 직접 비교 — 게이트와 verdict가 각각 무엇을 거르는지."""
    table = Table(
        title=f"[bold]{ticker}[/bold] 진입(BUY) vs 통과-미진입 vs 게이트 탈락 (20봉)",
        header_style="bold cyan",
    )
    columns = ("전략", "진입 n", "진입 평균%", "미진입 n", "미진입 평균%",
               "탈락 n", "탈락 평균%", "진입-탈락%p")
    for col in columns:
        table.add_column(col, justify="left" if col == "전략" else "right")

    for name, result_list in results.items():
        result = result_list[0]
        entered = result.signals
        not_entered = result.gate_passed_not_entered
        rejected = result.gate_rejected
        if entered.mean_return_pct is None or rejected.mean_return_pct is None:
            gap = "[dim]n/a[/dim]"
        else:
            gap = num(entered.mean_return_pct - rejected.mean_return_pct, sign=True)
        table.add_row(
            name,
            sample(entered),
            num(entered.mean_return_pct, sign=True),
            sample(not_entered),
            num(not_entered.mean_return_pct, sign=True),
            sample(rejected),
            num(rejected.mean_return_pct, sign=True),
            gap,
        )
    console.print(table)
    console.print(
        "  [dim]더미 전략은 통과 즉시 BUY라 미진입이 0이고 차이도 0 근처인 것이 정상이다. "
        "진짜 전략이라면 진입군이 유의하게 높아야 한다.[/dim]"
    )


def show_score_buckets(result: BacktestResult) -> None:
    table = Table(
        title=(
            f"[bold]{result.strategy_name}[/bold] / {result.ticker} "
            f"점수 구간별 성과 ({result.horizon}봉)"
        ),
        header_style="bold cyan",
    )
    for col in ("구간", "n", "평균%", "중앙%", "승률%", "평균MAE%"):
        table.add_column(col, justify="left" if col == "구간" else "right")

    for bucket in result.by_score_bucket:
        table.add_row(
            bucket.label,
            sample(bucket),
            num(bucket.mean_return_pct, sign=True),
            num(bucket.median_return_pct, sign=True),
            num(bucket.win_rate_pct, 1),
            num(bucket.mean_max_adverse_pct, sign=True),
        )
    console.print(table)
    console.print(
        f"  [dim]! 표시는 표본 부족(n < {result.signals.min_sample_size}) — 숫자를 믿지 말 것[/dim]"
    )


def check_predictions(
    all_results: dict[str, dict[str, list[BacktestResult]]],
    frames: dict[str, pd.DataFrame],
    config,
) -> list[bool]:
    """세 예측이 맞는지 명시적으로 판정한다. 억지로 맞추지 않는다."""
    table = Table(
        title="[bold]예측 검증[/bold] — 하네스가 자로서 정상인가", header_style="bold cyan"
    )
    for col in ("#", "예측", "종목", "실측", "판정"):
        table.add_column(col, justify="left")

    verdicts: list[bool] = []

    for ticker, results in all_results.items():
        result = results["always_buy"][0]
        # 하네스 내부 벤치마크가 아니라 위에서 pandas로 독립 계산한 값과 비교한다.
        # 내부 벤치마크 대비 초과수익은 같은 계산에서 나와 항상 0이므로 증명력이 없다.
        independent = independent_unconditional_mean_pct(
            frames[ticker], config, result.horizon
        )
        measured = result.signals.mean_return_pct
        if independent is None or measured is None:
            ok, detail = False, "n/a"
        else:
            diff = measured - independent
            ok = abs(diff) < 1e-9
            detail = f"하네스 {measured:+.6f}% vs 독립계산 {independent:+.6f}% (차 {diff:+.2e})"
        verdicts.append(ok)
        table.add_row(
            "1",
            "AlwaysBuy = 독립 계산한 buy-and-hold",
            ticker,
            detail,
            "[green]PASS[/green]" if ok else "[red]FAIL[/red]",
        )

    for ticker, results in all_results.items():
        result = results["random"][0]
        excess = result.excess_return_pct
        stats = result.signals
        # 보유기간이 겹치는 수익률은 자기상관이 강해 iid 가정 SE가 과소평가된다.
        # 유효 표본을 n/horizon으로 보수적으로 잡아 보정한다.
        if excess is None or stats.stdev_return_pct is None:
            ok, detail = False, "n/a"
        else:
            effective_n = max(1, stats.n // result.horizon)
            stderr = stats.stdev_return_pct / math.sqrt(effective_n)
            ok = abs(excess) < SIGMA * stderr
            detail = (
                f"초과 {excess:+.3f}%p vs {SIGMA:.0f}SE {SIGMA * stderr:.3f}%p "
                f"(유효표본 {effective_n})"
            )
        verdicts.append(ok)
        table.add_row(
            "2",
            f"Random = 시장평균 (|초과| < {SIGMA:.0f}SE, 유효표본 보정)",
            ticker,
            detail,
            "[green]PASS[/green]" if ok else "[red]FAIL[/red]",
        )

    for ticker, results in all_results.items():
        result = results["perfect_hindsight"][0]
        audit = result.lookahead
        caught = not audit.clean
        verdicts.append(caught)
        table.add_row(
            "3",
            "PerfectHindsight -> 감사 적발",
            ticker,
            f"{audit.checked_points}개 시점 중 {len(audit.violations)}건 위반",
            "[green]PASS (적발)[/green]" if caught else "[red]FAIL (놓침)[/red]",
        )

    console.print(table)
    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 백테스트 하네스 검증")
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_CONFIG.backtest.warmup_bars,
        help="워밍업 봉 수. 늘리면 빨라지지만 표본이 줄어든다",
    )
    args = parser.parse_args()

    # PerfectHindsight를 예외 없이 끝까지 돌려 '얼마나 말이 안 되는지'까지 보여준다.
    config = replace(
        DEFAULT_CONFIG,
        backtest=replace(
            DEFAULT_CONFIG.backtest, warmup_bars=args.warmup, strict_lookahead=False
        ),
    )

    console.print()
    show_bias_warnings()
    console.print()

    frames = {ticker: load(ticker) for ticker in TICKERS}
    show_setup(config, TICKERS, frames)
    console.print()

    all_results: dict[str, dict[str, list[BacktestResult]]] = {}
    for ticker in TICKERS:
        strategies = [AlwaysBuyStrategy(), RandomStrategy(), PerfectHindsightStrategy()]
        all_results[ticker] = {
            strategy.name: evaluate_results(strategy, ticker, frames[ticker], config)
            for strategy in strategies
        }

    for ticker in TICKERS:
        show_comparison(all_results[ticker], ticker)
        console.print()

    for ticker in TICKERS:
        show_gate_effect(all_results[ticker], ticker)
        console.print()

    show_score_buckets(all_results["AAPL"]["random"][0])
    console.print()

    verdicts = check_predictions(all_results, frames, config)
    console.print()

    if all(verdicts):
        console.print(
            Panel(
                "세 예측이 모두 맞았다. 하네스는 자로서 신뢰할 수 있다.\n"
                "[dim]다만 이것은 '하네스가 정상'이라는 뜻이지 "
                "'전략이 좋다'는 뜻이 아니다 — 아직 진짜 전략은 없다.[/dim]",
                border_style="green",
                title="[bold green]검증 통과[/bold green]",
            )
        )
        return 0

    console.print(
        Panel(
            f"{verdicts.count(False)}건의 예측이 어긋났다. 위 표에서 FAIL을 확인할 것.\n"
            "[dim]하네스에 버그가 있을 가능성이 높다. "
            "숫자를 맞추려 하지 말고 원인을 찾을 것.[/dim]",
            border_style="red",
            title="[bold red]검증 실패[/bold red]",
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
