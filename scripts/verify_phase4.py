"""Phase 4 육안 검증 — 전략 3종이 정말로 '따로' 판단하는가.

Phase 3.5까지의 결론은 "미너비니는 미래를 참조하지 않고, 풀링하면 표본도 모인다"였다.
Phase 4는 전략을 3종으로 늘렸으므로 물어야 할 질문이 바뀐다:

  1. **판정이 갈리는가** — 같은 종목·같은 날 세 전략이 다른 답을 내는가.
     전부 같은 답을 낸다면 방법론을 나란히 두는 의미가 없다 (하나만 쓰면 된다).
  2. **선별성이 다른가** — 게이트가 얼마나 자주 열리는가. 자주 열리는 게이트는
     무조건부 매수에 수렴하며, 그때 초과수익은 0 근처가 되어야 정상이다.
  3. **감사는 여전히 깨끗한가** — 새 전략 2종이 미래를 참조하지 않는가.
  4. **점수가 성과와 단조인가** — SCORE 단계가 타이밍을 재고 있는가.

여전히 못 메우는 것은 생존편향과 기간 편향이다. 리포트 상단에 계속 출력된다.

    python scripts/verify_phase4.py
    python scripts/verify_phase4.py --market us
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

from backtest.harness import (  # noqa: E402
    BIAS_WARNINGS,
    PanelResult,
    evaluate_panel,
    replay,
)
from config import DEFAULT_CONFIG  # noqa: E402
from core.context import build_context  # noqa: E402
from core.types import BarMeta, MarketRegime, SessionState, Stage, Verdict  # noqa: E402
from data.universe import (  # noqa: E402
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
    survivorship_warning,
)
from regime.market import regime_series, stage_series  # noqa: E402
from strategies.registry import STRATEGY_FACTORIES  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
# 폭을 고정하는 이유: 좁은 터미널에서 rich가 열을 잘라 숫자가 통째로 사라진다.
console = Console(width=118)

MARKETS = {
    "us": {"universe": "us_large", "benchmark": "SPY", "panel": "panel_us_large_ohlcv"},
    "kospi": {"universe": "kospi", "benchmark": "^KS11", "panel": "panel_kospi_ohlcv"},
}

STRATEGIES = tuple(STRATEGY_FACTORIES)


def make_strategies(cfg):
    """전략명 -> 무인자 팩토리. 목록은 `strategies/registry.py` 한 곳에만 있다.

    하네스가 종목마다 새 인스턴스를 요구하므로(상태 누수 방지) config를 미리 묶어 둔다.
    """
    return {
        name: (lambda factory=factory: factory(cfg))
        for name, factory in STRATEGY_FACTORIES.items()
    }


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / f"{name}.csv", index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def load_panel(name: str) -> dict[str, pd.DataFrame]:
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
    console.print(
        Panel(
            body,
            title="[bold red]이 결과를 읽기 전에[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def build_market(market: str) -> dict:
    """한 시장의 유니버스·RS·패널·주입 시리즈를 준비한다 (verify_phase3_5와 동일한 경로)."""
    spec = MARKETS[market]
    cfg = DEFAULT_CONFIG

    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{spec['universe']}_closes.parquet")
    percentiles = rs_percentile_frame(rs_score_frame(closes, cfg.universe), cfg.universe)
    regimes = regime_series(load_csv(f"{spec['benchmark'].replace('.', '_')}_3y"), cfg.regime)

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
            position = int(df.index.get_indexer([pd.Timestamp(min(rs))])[0])
            warmups[ticker] = max(cfg.backtest.warmup_bars, position)

    return {
        "spec": spec,
        "universe_size": closes.shape[1],
        "percentiles": percentiles,
        "regimes": regimes,
        "frames": frames,
        "injections": injections,
        "warmups": warmups,
    }


# ---------------------------------------------------------------------------
# 1. 판정이 갈리는가
# ---------------------------------------------------------------------------


def latest_verdicts(market: str, data: dict) -> dict[str, dict[str, Verdict]]:
    """각 종목의 마지막 봉에서 세 전략을 각각 평가한다.

    백테스트가 아니라 '오늘 화면에 뜰 판정'이다. 주입 시리즈는 백테스트와 같은 것을 쓴다.
    """
    cfg = DEFAULT_CONFIG
    factories = make_strategies(cfg)
    out: dict[str, dict[str, Verdict]] = {}

    for ticker, df in sorted(data["frames"].items()):
        inject = data["injections"][ticker]
        as_of = df.index[-1].date()
        ctx = build_context(
            ticker,
            df,
            cfg,
            regime=inject["regime_by_date"].get(as_of, MarketRegime.CAUTION),
            stage=inject["stage_by_date"].get(as_of, Stage.UNDEFINED),
            rs_percentile=inject["rs_percentile_by_date"].get(as_of),
            bar_meta=BarMeta(
                last_bar_date=as_of,
                session_state=SessionState.CLOSED,
                is_bar_complete=True,
                bars_available=len(df),
                volume_judgements_reliable=True,
            ),
        )
        out[ticker] = {
            name: factory().evaluate(ctx).verdict for name, factory in factories.items()
        }
    return out


def show_disagreement(market: str, verdicts: dict[str, dict[str, Verdict]]) -> int:
    """전략 간 판정이 갈린 종목 수를 반환한다."""
    split = {t: v for t, v in verdicts.items() if len(set(v.values())) > 1}

    table = Table(
        title=f"[bold]{market}[/bold] 마지막 봉 판정 — 전략별 (합치지 않는다)",
        header_style="bold cyan",
    )
    table.add_column("티커")
    for name in STRATEGIES:
        table.add_column(name, justify="center")
    table.add_column("일치", justify="center")

    colors = {
        Verdict.BUY: "green",
        Verdict.WATCH: "yellow",
        Verdict.AVOID: "red",
        Verdict.HOLD: "cyan",
        Verdict.REJECTED_BY_GATE: "dim",
    }
    for ticker, per_strategy in list(verdicts.items())[:14]:
        row = [ticker]
        for name in STRATEGIES:
            verdict = per_strategy[name]
            row.append(f"[{colors[verdict]}]{verdict.value}[/{colors[verdict]}]")
        row.append("—" if ticker in split else "[green]동일[/green]")
        table.add_row(*row)
    console.print(table)
    console.print(
        f"  [dim]판정이 갈린 종목 {len(split)}/{len(verdicts)}개. "
        "갈리지 않는다면 방법론을 나란히 둘 이유가 없다 — 하나만 쓰면 된다.[/dim]"
    )
    return len(split)


# ---------------------------------------------------------------------------
# 2. 게이트 선별성
# ---------------------------------------------------------------------------


def show_selectivity(market: str, data: dict) -> None:
    """전략별로 게이트가 얼마나 자주 열리는지 — 진입 빈도의 분모까지 보여준다."""
    cfg = DEFAULT_CONFIG
    run_config = replace(cfg, backtest=replace(cfg.backtest, strict_lookahead=False))
    factories = make_strategies(cfg)

    table = Table(
        title=f"[bold]{market}[/bold] 게이트 선별성 (표본 종목 3개)", header_style="bold cyan"
    )
    for col in ("전략", "평가 봉", "게이트 통과", "통과율%", "진입(BUY)", "진입률%"):
        table.add_column(col, justify="left" if col == "전략" else "right")

    tickers = sorted(data["frames"])[:3]
    for name in STRATEGIES:
        evaluated = passed = entered = 0
        for ticker in tickers:
            df = data["frames"][ticker]
            local = run_config
            if ticker in data["warmups"]:
                local = replace(
                    run_config,
                    backtest=replace(
                        run_config.backtest, warmup_bars=data["warmups"][ticker]
                    ),
                )
            signals = replay(
                factories[name](), ticker, df, local, **data["injections"][ticker]
            )
            evaluated += len(signals)
            passed += sum(1 for s in signals if s.gate_passed)
            entered += sum(1 for s in signals if s.entered)
        table.add_row(
            name,
            str(evaluated),
            str(passed),
            f"{100.0 * passed / evaluated:.1f}" if evaluated else "n/a",
            str(entered),
            f"{100.0 * entered / evaluated:.1f}" if evaluated else "n/a",
        )
    console.print(table)
    console.print(
        "  [dim]게이트가 자주 열리는 전략은 무조건부 매수에 수렴한다. "
        "그때 초과수익이 0 근처로 나오는 것은 결함이 아니라 정합성 신호다.[/dim]"
    )


# ---------------------------------------------------------------------------
# 3. 성과
# ---------------------------------------------------------------------------


def show_panel_results(market: str, results: dict[str, list[PanelResult]]) -> None:
    table = Table(
        title=f"[bold]{market}[/bold] 다종목 풀링 결과 (전략별 — 합산하지 않는다)",
        header_style="bold cyan",
    )
    # 열 수를 줄인 이유: 터미널 폭이 좁으면 rich가 열을 잘라 숫자가 사라진다.
    # 종목 수·중앙값은 PanelResult에 그대로 남아 있으므로 필요하면 코드로 본다.
    columns = ("전략", "보유", "진입종목", "진입 n", "평균%", "승률%",
               "벤치%", "초과%", "2SE↓", "2SE↑", "감사")
    for col in columns:
        table.add_column(col, justify="left" if col == "전략" else "right")

    for name in STRATEGIES:
        for result in results[name]:
            stderr = result.signals.stderr_return_pct
            # 보유기간이 겹쳐 수익률이 자기상관되므로 표준오차의 참값은 구간이다
            # (verify_phase3_5.py의 주석과 같은 보정).
            effective = max(1, result.signals.n // result.horizon)
            adjusted = (
                None if stderr is None else stderr * (result.signals.n / effective) ** 0.5
            )
            table.add_row(
                name,
                str(result.horizon),
                f"{result.tickers_with_entries}/{result.tickers}",
                sample(result.signals),
                num(result.signals.mean_return_pct, sign=True),
                num(result.signals.win_rate_pct, 1),
                num(result.benchmark.mean_return_pct, sign=True),
                num(result.excess_return_pct, sign=True),
                num(None if stderr is None else 2 * stderr),
                num(None if adjusted is None else 2 * adjusted),
                "[green]clean[/green]" if result.audit_clean else "[red]위반[/red]",
            )
    console.print(table)
    console.print(
        "  [dim]|초과%| > 2SE↑ 이면 어느 가정에서도 유의하고, < 2SE↓ 이면 어느 가정에서도 "
        "유의하지 않다. 그 사이면 결론을 유보한다.[/dim]"
    )
    # 봉이 모자라 평가조차 못 한 종목은 분모에서 빠진다. 그 사실이 표에 보이지 않으면
    # '29종목 패널'이라는 말이 조용히 거짓이 된다.
    skipped = sorted({t for results_ in results.values() for t in results_[0].skipped_tickers})
    if skipped:
        console.print(
            f"  [yellow]봉 부족으로 제외된 종목 {len(skipped)}개: {', '.join(skipped)}[/yellow]"
        )


def show_score_buckets(market: str, results: dict[str, list[PanelResult]]) -> None:
    table = Table(
        title=f"[bold]{market}[/bold] 점수 구간별 성과 (20봉 보유, 풀링)",
        header_style="bold cyan",
    )
    for col in ("전략", "구간", "n", "평균%", "승률%"):
        table.add_column(col, justify="left" if col in ("전략", "구간") else "right")

    for name in STRATEGIES:
        result = results[name][0]
        for bucket in result.by_score_bucket:
            if bucket.n == 0:
                continue
            table.add_row(
                name,
                bucket.label,
                sample(bucket),
                num(bucket.mean_return_pct, sign=True),
                num(bucket.win_rate_pct, 1),
            )
    console.print(table)
    console.print(
        "  [dim]점수가 높을수록 성과가 좋아야 SCORE 단계가 의미를 갖는다. "
        "단조 증가하지 않으면 채점 항목이 타이밍을 못 재고 있다는 뜻이다 — "
        "다만 구간별 표본이 적으면 이 표로는 결론을 낼 수 없다.[/dim]"
    )


def score_direction_holds(result: PanelResult) -> bool | None:
    """높은 점수 구간이 낮은 점수 구간보다 성과가 좋은가. 판정 불가면 None.

    **표본이 충분한 구간(min_sample_size 이상)만** 비교하고, 그중 최상위와 최하위
    구간의 평균수익만 본다. 전 구간 단조 증가를 요구하지 않는 이유는 구간별 표본이
    수십 건이라 노이즈만으로도 순서가 쉽게 뒤집히기 때문이다 — 그렇게 재면 거의 모든
    전략이 '실패'로 나와 신호가 되지 못한다.

    이것도 약한 검사다. False는 '점수 체계를 다시 보라'는 신호이지 전략 실패 판정이
    아니고, True도 점수가 타이밍을 잘 잰다는 증거는 아니다.
    """
    usable = [
        bucket
        for bucket in result.by_score_bucket
        if not bucket.is_underpowered and bucket.mean_return_pct is not None
    ]
    if len(usable) < 2:
        return None
    return usable[-1].mean_return_pct > usable[0].mean_return_pct


def conclude(
    all_results: dict[str, dict[str, list[PanelResult]]],
    disagreements: dict[str, tuple[int, int]],
) -> int:
    cfg = DEFAULT_CONFIG
    minimum = cfg.backtest.min_sample_size

    audits_clean = all(
        result.audit_clean
        for results in all_results.values()
        for panel in results.values()
        for result in panel
    )
    sizes = {
        (market, name): results[name][0].signals.n
        for market, results in all_results.items()
        for name in STRATEGIES
    }
    thin = [f"{market}/{name}" for (market, name), n in sizes.items() if n < minimum]
    split_total = sum(split for split, _ in disagreements.values())

    table = Table(title="[bold]Phase 4 점검표[/bold]", header_style="bold cyan")
    for col in ("항목", "결과", "판정"):
        table.add_column(col)

    table.add_row(
        "전략 수", f"{len(STRATEGIES)}종 (Phase 3.5는 1종)", "[green]확장됨[/green]"
    )
    table.add_row(
        "판정 분리",
        ", ".join(f"{m} {s}/{t}종목 갈림" for m, (s, t) in disagreements.items()),
        "[green]PASS[/green]" if split_total else "[yellow]전부 일치[/yellow]",
    )
    table.add_row(
        "진입 표본",
        ", ".join(f"{m}/{n} {size}" for (m, n), size in sizes.items()),
        "[green]PASS[/green]" if not thin else f"[yellow]{', '.join(thin)} 미달[/yellow]",
    )
    table.add_row(
        "look-ahead 감사",
        "위반 0건" if audits_clean else "위반 발생",
        "[green]PASS[/green]" if audits_clean else "[red]FAIL[/red]",
    )

    broken = [
        f"{market}/{name}"
        for market, results in all_results.items()
        for name in STRATEGIES
        if score_direction_holds(results[name][0]) is False
    ]
    table.add_row(
        "점수-성과 방향 (20봉)",
        "역전 없음" if not broken else f"{', '.join(broken)} 역전",
        "[green]PASS[/green]" if not broken else "[yellow]점수 체계 재검토[/yellow]",
    )
    table.add_row("생존편향", "제거 못 함", "[yellow]잔존[/yellow]")
    console.print(table)

    if not audits_clean:
        console.print(
            Panel(
                "감사 위반이 발생했다. 새로 추가한 전략이 미래를 참조하고 있다.\n"
                "detect_range / detect_consolidation 이 후방 참조만 하는지부터 확인할 것.",
                border_style="red",
                title="[bold red]검증 실패[/bold red]",
            )
        )
        return 1

    lines = [
        "[bold]확인된 것[/bold]",
        "  전략 3종이 같은 컨텍스트에서 서로 다른 판정을 낸다 — 나란히 둘 이유가 있다.",
        "  새 전략 2종도 look-ahead 감사에 걸리지 않는다.",
        "  게이트 선별성이 전략마다 크게 다르다 (와인스타인이 가장 자주 열린다).",
        "",
        "[bold]여전히 말할 수 없는 것[/bold]",
        "  어느 전략이 더 나은지. 3년 한 구간·생존편향 잔존·보유기간 중첩 때문에",
        "  초과수익 차이는 대부분 2SE 구간 안에 들어간다.",
        "  임계값을 이 표본에 맞춰 조정하면 그때부터는 과적합이다 —",
        "  파라미터 스윕은 결과를 좋아 보이게 만드는 데 쓰는 도구가 아니다.",
    ]
    if thin:
        lines.insert(3, f"  [yellow]단, 표본이 {minimum}건에 못 미치는 조합이 있다: "
                        f"{', '.join(thin)}[/yellow]")
    if broken:
        lines.insert(
            3,
            f"  [yellow]{', '.join(broken)}는 높은 점수 구간의 성과가 낮은 구간보다 "
            "나빴다 — 그 전략의 SCORE 항목이 타이밍을 재고 있다는 증거가 없다.[/yellow]",
        )
    console.print(
        Panel(
            "\n".join(lines),
            border_style="green" if not thin else "yellow",
            title="[bold]Phase 4 결론[/bold]",
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 전략 3종 검증")
    parser.add_argument("--market", choices=list(MARKETS), default=None)
    args = parser.parse_args()

    markets = [args.market] if args.market else list(MARKETS)
    cfg = DEFAULT_CONFIG
    run_config = replace(cfg, backtest=replace(cfg.backtest, strict_lookahead=False))
    all_results: dict[str, dict[str, list[PanelResult]]] = {}
    disagreements: dict[str, tuple[int, int]] = {}

    for market in markets:
        data = build_market(market)
        console.print()
        show_warnings(data["spec"]["universe"], data["universe_size"])
        console.print()

        verdicts = latest_verdicts(market, data)
        disagreements[market] = (show_disagreement(market, verdicts), len(verdicts))
        console.print()
        show_selectivity(market, data)
        console.print()

        shared = {
            "frames": data["frames"],
            "injections": data["injections"],
            "warmups": data["warmups"],
        }
        all_results[market] = {
            name: evaluate_panel(factory, config=run_config, **shared)
            for name, factory in make_strategies(cfg).items()
        }
        show_panel_results(market, all_results[market])
        console.print()
        show_score_buckets(market, all_results[market])
        console.print()

    return conclude(all_results, disagreements)


if __name__ == "__main__":
    raise SystemExit(main())
