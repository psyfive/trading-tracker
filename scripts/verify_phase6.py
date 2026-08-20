"""Phase 6 육안 검증 — 리뷰가 남긴 세 질문에 측정으로 답한다.

Phase 4 리뷰는 세 가지를 '결정이 필요하다'며 남겼고, Phase 5까지는 잴 도구가 없었다.
이 스크립트는 학습/홀드아웃 분할 위에서 파라미터를 흔들어 각 질문에 답한다.

  1. **Qullamaggie의 SCORE가 타이밍을 재는가** — 점수 기준(`buy_min_score_pct`)을 올리면
     성과가 좋아져야 한다. 안 좋아지면 그 점수는 타이밍을 재지 못하는 것이다.
  2. **와인스타인 게이트가 너무 느슨한가** — `min_rs_percentile`을 올리면 선별성이
     올라간다. 초과수익이 따라 올라가는지, 표본만 줄어드는지를 본다.
  3. **PIVOT_READY를 BUY로 볼 것인가** — `require_breakout_for_buy`를 켜면 돌파 확인 후에만
     진입한다. 원전에 충실한 쪽이 실제로 나은지 잰다.

## 이 스크립트는 config를 고치지 않는다

측정하고 보고할 뿐이다. 임계값을 바꾸는 것은 사람의 결정이고, 그 결정의 근거는
**홀드아웃에서 살아남았는가**여야 한다. 학습 구간 최고값을 자동으로 채택하면
그 순간 이 도구는 과적합 기계가 된다.

    python scripts/verify_phase6.py
    python scripts/verify_phase6.py --market kospi
    python scripts/verify_phase6.py --question entry   # 한 질문만
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

from backtest.harness import BIAS_WARNINGS  # noqa: E402
from backtest.sweep import (  # noqa: E402
    SWEEP_WARNINGS,
    Parameter,
    SweepResult,
    build_split,
    sweep,
)
from config import DEFAULT_CONFIG  # noqa: E402
from data.universe import (  # noqa: E402
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
)
from regime.market import regime_series, stage_series  # noqa: E402
from strategies.registry import STRATEGY_FACTORIES  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
console = Console(width=118)

MARKETS = {
    "us": {"universe": "us_large", "benchmark": "SPY", "panel": "panel_us_large_ohlcv"},
    "kospi": {"universe": "kospi", "benchmark": "^KS11", "panel": "panel_kospi_ohlcv"},
}

# 질문 -> (전략, 파라미터, 값 목록). 값은 기본값을 반드시 포함한다 —
# 기준선이 표에 없으면 '개선'이 무엇 대비 개선인지 말할 수 없다.
QUESTIONS = {
    "score": (
        "Qullamaggie의 점수가 타이밍을 재는가",
        "qullamaggie",
        Parameter("qullamaggie", "buy_min_score_pct", [50.0, 60.0, 70.0, 80.0]),
    ),
    "gate": (
        "와인스타인 게이트가 너무 느슨한가",
        "weinstein",
        Parameter("weinstein", "min_rs_percentile", [50.0, 60.0, 70.0, 80.0]),
    ),
    "entry": (
        "PIVOT_READY를 BUY로 볼 것인가 (전략 3종)",
        None,  # 세 전략 전부
        Parameter("", "require_breakout_for_buy", [False, True]),
    ),
}


def load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIXTURE_DIR / f"{name}.csv", index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def build_market(market: str) -> dict:
    """패널·유니버스 RS·국면 시리즈 — verify_phase4와 같은 경로로 만든다."""
    spec = MARKETS[market]
    cfg = DEFAULT_CONFIG

    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{spec['universe']}_closes.parquet")
    percentiles = rs_percentile_frame(rs_score_frame(closes, cfg.universe), cfg.universe)
    regimes = regime_series(load_csv(f"{spec['benchmark'].replace('.', '_')}_3y"), cfg.regime)

    panel = pd.read_parquet(FIXTURE_DIR / f"{spec['panel']}.parquet")
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in panel.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date").sort_index()
        frame.index.name = "date"
        frames[str(ticker)] = frame.astype(float)

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

    return {"frames": frames, "injections": injections, "warmups": warmups}


def show_warnings() -> None:
    body = "\n\n".join(f"[bold]•[/bold] {w}" for w in (BIAS_WARNINGS[0], *SWEEP_WARNINGS))
    console.print(
        Panel(
            body,
            title="[bold red]이 표를 읽기 전에[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def num(value: float | None, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def show_sweep(title: str, result: SweepResult) -> None:
    best = result.best_train
    table = Table(
        title=f"[bold]{title}[/bold] — {result.strategy_name}.{result.parameter.field} "
        f"({result.horizon}봉 보유)",
        header_style="bold cyan",
    )
    for col in ("값", "", "학습 n", "학습 초과%", "홀드아웃 n", "홀드아웃 초과%", "감소%"):
        table.add_column(col, justify="left" if col in ("값", "") else "right")

    for point in result.points:
        marks = []
        if point.value == result.baseline_value:
            marks.append("[dim]기본[/dim]")
        if best is not None and point is best:
            marks.append("[bold green]학습최고[/bold green]")
        if not point.is_selectable:
            marks.append("[yellow]표본부족[/yellow]")

        table.add_row(
            str(point.value),
            " ".join(marks),
            str(point.train.signals.n),
            num(point.train.excess_return_pct, sign=True),
            str(point.holdout.signals.n),
            num(point.holdout.excess_return_pct, sign=True),
            num(point.decay_pct, sign=True),
        )
    console.print(table)
    console.print(f"  [bold]->[/bold] {result.verdict}")
    console.print(
        "  [dim]감소% = 홀드아웃 초과수익 - 학습 초과수익. 크게 음수면 학습에서 본 개선이 "
        "표본 노이즈였다는 뜻이다.[/dim]"
    )


def run_question(key: str, market: dict, config, split) -> list[tuple[str, SweepResult]]:
    """질문 하나에 해당하는 스윕들을 돌린다. 'entry'는 세 전략 전부에 대해 돈다."""
    title, strategy_name, parameter = QUESTIONS[key]
    shared = {
        "injections": market["injections"],
        "warmups": market["warmups"],
        "split": split,
    }

    targets = [strategy_name] if strategy_name else list(STRATEGY_FACTORIES)
    out: list[tuple[str, SweepResult]] = []
    for name in targets:
        factory = STRATEGY_FACTORIES[name]
        # entry 질문은 전략마다 자기 config 섹션을 흔든다 (섹션명 = 전략명).
        section = parameter.section or name
        out.append(
            (
                title,
                sweep(
                    lambda cfg, factory=factory: factory(cfg),
                    Parameter(section, parameter.field, parameter.values),
                    market["frames"],
                    config,
                    **shared,
                ),
            )
        )
    return out


def conclude(results: list[tuple[str, SweepResult]]) -> int:
    table = Table(title="[bold]Phase 6 결론 — 무엇을 바꿀 것인가[/bold]", header_style="bold cyan")
    for col in ("질문", "대상", "학습 최고", "홀드아웃에서", "조치"):
        table.add_column(col)

    for title, result in results:
        best = result.best_train
        if best is None:
            table.add_row(title, result.strategy_name, "[yellow]근거 없음[/yellow]", "—",
                          "[dim]유지[/dim]")
            continue

        survived = (
            best.holdout.excess_return_pct is not None
            and best.holdout.excess_return_pct > 0.0
            and (best.decay_pct is None or best.decay_pct > -2.0)
        )
        if result.best_is_within_noise:
            # argmax가 뽑았지만 2위와의 차이가 노이즈 폭 안이면 '최고'라고 부를 수 없다.
            action = "[yellow]유지 (1·2위 구분 불가)[/yellow]"
        elif best.value == result.baseline_value:
            action = "[dim]유지 (기본값이 최고)[/dim]"
        elif survived:
            action = "[green]변경 후보[/green]"
        else:
            action = "[yellow]유지 (재현 실패)[/yellow]"

        table.add_row(
            title,
            result.strategy_name,
            best.label,
            num(best.holdout.excess_return_pct, sign=True),
            action,
        )
    console.print(table)

    console.print(
        Panel(
            "\n".join(
                [
                    "[bold]이 스윕이 말할 수 있는 것[/bold]",
                    "  학습 구간에서 좋아 보인 값이 홀드아웃에서도 살아남는지 여부.",
                    "  살아남지 못하면 그 '개선'은 표본 노이즈였다 — 유용한 부정 정보다.",
                    "",
                    "[bold]말할 수 없는 것[/bold]",
                    "  최적값. 후보 k개 중 최고를 고르는 절차 자체가 값을 부풀리고,",
                    "  홀드아웃도 한 구간(약 200거래일)이라 표본이 작다.",
                    "  '변경 후보'로 표시된 값도 자동 채택하지 않는다 — config는 사람이 고친다.",
                ]
            ),
            border_style="green",
            title="[bold]Phase 6 결론[/bold]",
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 6 파라미터 스윕 + 홀드아웃 검증")
    parser.add_argument("--market", choices=list(MARKETS), default="us")
    parser.add_argument("--question", choices=list(QUESTIONS), default=None)
    args = parser.parse_args()

    cfg = replace(
        DEFAULT_CONFIG,
        backtest=replace(DEFAULT_CONFIG.backtest, strict_lookahead=False),
    )
    market = build_market(args.market)
    split = build_split(market["frames"], cfg.backtest)

    console.print()
    show_warnings()
    console.print()
    console.print(
        Panel(
            f"{split.describe()}\n"
            f"[dim]학습에서 고르고 홀드아웃으로 확인한다. embargo는 보유기간이 두 구간에 "
            f"걸치는 것을 막는다.[/dim]",
            title=f"[bold]{args.market}[/bold] 기간 분할",
            border_style="cyan",
        )
    )

    results: list[tuple[str, SweepResult]] = []
    for key in [args.question] if args.question else list(QUESTIONS):
        console.print()
        for title, result in run_question(key, market, cfg, split):
            show_sweep(title, result)
            results.append((title, result))
            console.print()

    return conclude(results)


if __name__ == "__main__":
    raise SystemExit(main())
