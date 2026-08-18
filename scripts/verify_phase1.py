"""Phase 1 육안 검증 스크립트.

고정 픽스처(tests/fixtures/*_3y.csv)의 최근 5봉에 대해 전 지표를 표로 찍고,
원본 OHLCV와 나란히 놓아 사람이 직접 대조할 수 있게 한다.
마지막으로 IndicatorSnapshot을 DiagnosisReport에 실어 Phase 0 계약이
실제 데이터로도 성립하는지 확인한다.

    python scripts/verify_phase1.py                  # 기본: AAPL 픽스처
    python scripts/verify_phase1.py --ticker 005930.KS
    python scripts/verify_phase1.py --live           # yfinance 재수집본과 대조 (네트워크 필요)

--live는 픽스처가 오염되지 않았는지 확인할 때만 쓴다. 테스트는 절대 네트워크를 타지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from core.types import (  # noqa: E402
    BarMeta,
    ConsensusSummary,
    DiagnosisReport,
    IndicatorSnapshot,
    MarketRegime,
    SessionState,
    Stage,
)
from indicators.core import (  # noqa: E402
    adr_pct,
    atr,
    bollinger,
    ema,
    macd,
    rolling_high,
    rolling_low,
    rsi,
    slope_pct,
    sma,
    volume_sma,
)
from indicators.snapshot import build_indicator_snapshot  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
IND = DEFAULT_CONFIG.indicators
console = Console()

DEFAULT_BARS = 5


def fixture_path(ticker: str) -> Path:
    return FIXTURE_DIR / f"{ticker.upper().replace('.', '_')}_3y.csv"


def load(ticker: str) -> pd.DataFrame:
    path = fixture_path(ticker)
    if not path.exists():
        available = ", ".join(
            p.stem.replace("_3y", "") for p in sorted(FIXTURE_DIR.glob("*_3y.csv"))
        )
        raise SystemExit(f"픽스처 없음: {path.name}\n사용 가능: {available}")
    df = pd.read_csv(path, index_col=0, parse_dates=True).astype(float)
    df.index.name = "date"
    return df


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "[dim]None[/dim]"
    return f"{value:,.{digits}f}"


def compact(value: float | None) -> str:
    """큰 금액/주식수를 단위 접미사로 줄인다. 표가 잘리는 것을 막기 위함."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "[dim]None[/dim]"
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= scale:
            return f"{value / scale:,.2f}{suffix}"
    return f"{value:,.0f}"


def show_raw_bars(df: pd.DataFrame, ticker: str, bars: int) -> None:
    """원본 OHLCV. 픽스처 CSV는 yfinance 출력을 가공 없이 저장한 것이다."""
    table = Table(
        title=f"[bold]{ticker}[/bold] 최근 {bars}봉 원본 OHLCV (yfinance auto_adjust=True 그대로)",
        header_style="bold cyan",
    )
    table.add_column("date")
    for col in ("open", "high", "low", "close"):
        table.add_column(col, justify="right")
    table.add_column("volume", justify="right")

    for ts, row in df.tail(bars).iterrows():
        table.add_row(
            ts.date().isoformat(),
            fmt(row["open"]),
            fmt(row["high"]),
            fmt(row["low"]),
            f"[bold]{fmt(row['close'])}[/bold]",
            compact(row["volume"]),
        )
    console.print(table)


def show_indicators(df: pd.DataFrame, ticker: str, bars: int) -> None:
    """지표를 행, 날짜를 열로 놓는다. 같은 지표의 날짜별 추이를 눈으로 좇기 쉽다."""
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    bb_u, bb_m, bb_l = bollinger(close, IND.bb_period, IND.bb_std, IND.bb_ddof)
    macd_line, macd_sig, macd_hist = macd(close, IND.macd_fast, IND.macd_slow, IND.macd_signal)
    high52 = rolling_high(high, IND.high_low_lookback)
    low52 = rolling_low(low, IND.high_low_lookback)
    atr14 = atr(high, low, close, IND.atr_period)
    avg_vol = volume_sma(volume, IND.volume_avg_period)

    # digits=None 인 행은 compact() 로 렌더링한다 (거래량/거래대금)
    rows: list[tuple[str, pd.Series, int | None]] = [
        ("close(원본)", close, 2),
        ("sma20", sma(close, 20), 2),
        ("sma50", sma(close, 50), 2),
        ("sma150", sma(close, 150), 2),
        ("sma200", sma(close, 200), 2),
        ("ema10", ema(close, 10), 2),
        ("ema21", ema(close, 21), 2),
        ("sma50_slope_10d_pct", slope_pct(sma(close, 50), IND.slope_lookback_short), 3),
        ("sma150_slope_20d_pct", slope_pct(sma(close, 150), IND.slope_lookback_long), 3),
        ("sma200_slope_20d_pct", slope_pct(sma(close, 200), IND.slope_lookback_long), 3),
        ("rsi14 (Wilder)", rsi(close, IND.rsi_period), 2),
        ("atr14 (Wilder)", atr14, 3),
        ("atr_pct", atr14 / close * 100.0, 3),
        ("adr20_pct", adr_pct(high, low, IND.adr_period), 3),
        ("macd", macd_line, 4),
        ("macd_signal", macd_sig, 4),
        ("macd_hist", macd_hist, 4),
        ("bb_upper", bb_u, 2),
        ("bb_mid", bb_m, 2),
        ("bb_lower", bb_l, 2),
        ("bb_width_pct", (bb_u - bb_l) / bb_m * 100.0, 3),
        ("high_52w", high52, 2),
        ("low_52w", low52, 2),
        ("from_52w_high_pct", (close - high52) / high52 * 100.0, 3),
        ("above_52w_low_pct", (close - low52) / low52 * 100.0, 3),
        ("volume", volume, None),
        ("avg_volume_50", avg_vol, None),
        ("volume_ratio", volume / avg_vol, 3),
        ("dollar_volume_50", (close * volume).rolling(IND.volume_avg_period).mean(), None),
    ]

    dates = [ts.date().isoformat() for ts in df.index[-bars:]]
    table = Table(
        title=f"[bold]{ticker}[/bold] 최근 {bars}봉 지표 (외부 TA 라이브러리 없이 직접 계산)",
        header_style="bold cyan",
    )
    table.add_column("indicator", style="bold", no_wrap=True)
    for d in dates:
        table.add_column(d, justify="right", no_wrap=True)

    for label, series, digits in rows:
        render = compact if digits is None else (lambda v, d=digits: fmt(v, d))
        table.add_row(label, *[render(v) for v in series.tail(bars)])
    console.print(table)


def show_contract_check(df: pd.DataFrame, ticker: str) -> None:
    """실제 데이터로 만든 스냅샷이 Phase 0 계약을 통과하는지."""
    snap = build_indicator_snapshot(df, IND)
    last_date = df.index[-1].date()

    report = DiagnosisReport(
        ticker=ticker.upper(),
        as_of=last_date,
        generated_at=datetime.now(UTC),
        price=float(df["close"].iloc[-1]),
        is_bar_complete=True,
        bar_meta=BarMeta(
            last_bar_date=last_date,
            session_state=SessionState.CLOSED,
            is_bar_complete=True,
            bars_available=len(df),
            volume_judgements_reliable=True,
        ),
        regime=MarketRegime.CAUTION,
        stage=Stage.UNDEFINED,
        indicators=snap,
        strategy_verdicts=[],
        consensus=ConsensusSummary(total_strategies=0),
    )
    restored = DiagnosisReport.model_validate_json(report.model_dump_json())

    filled = sum(getattr(snap, f) is not None for f in IndicatorSnapshot.model_fields)
    total = len(IndicatorSnapshot.model_fields)
    none_fields = [f for f in IndicatorSnapshot.model_fields if getattr(snap, f) is None]

    table = Table(title="[bold]Phase 0 계약 검증[/bold] (실제 데이터)", header_style="bold cyan")
    table.add_column("항목")
    table.add_column("결과")
    table.add_row("IndicatorSnapshot 생성", "[green]OK[/green]")
    table.add_row("DiagnosisReport 검증", "[green]OK[/green]")
    table.add_row(
        "JSON 왕복 동일성", "[green]OK[/green]" if restored == report else "[red]FAIL[/red]"
    )
    table.add_row("채워진 지표 필드", f"{filled} / {total}")
    table.add_row("None인 필드", ", ".join(none_fields) if none_fields else "[dim]없음[/dim]")
    console.print(table)

    console.print(
        "  [dim]rs_percentile / rs_line_new_high가 None인 것은 정상이다 — "
        "RS는 유니버스가 필요하므로 Phase 3.5다.[/dim]\n"
        "  [dim]이 None이 나중에 게이트에서 UNAVAILABLE로 이어진다 (FAIL이 아니다).[/dim]"
    )


def show_live_diff(df: pd.DataFrame, ticker: str, bars: int) -> None:
    """yfinance 재수집본과 픽스처를 대조. 픽스처가 오염되지 않았는지 확인용."""
    from data.fetcher import DataError, fetch_ohlcv

    try:
        live = fetch_ohlcv(ticker, DEFAULT_CONFIG.data)
    except DataError as exc:
        console.print(f"[yellow]--live 실패: {exc}[/yellow]")
        console.print(
            "[dim]TLS 인터셉션 환경이면 SSL_CERT_FILE에 "
            "사내 루트 CA 번들을 지정할 것[/dim]"
        )
        return

    table = Table(
        title=f"[bold]{ticker}[/bold] 픽스처 vs yfinance 재수집 (종가)", header_style="bold cyan"
    )
    for col in ("date", "픽스처", "yfinance", "차이"):
        table.add_column(col, justify="right" if col != "date" else "left")

    for ts in df.index[-bars:]:
        fixture_close = float(df.loc[ts, "close"])
        if ts in live.index:
            live_close = float(live.loc[ts, "close"])
            diff = live_close - fixture_close
            style = "green" if abs(diff) < 0.01 else "yellow"
            table.add_row(
                ts.date().isoformat(),
                fmt(fixture_close),
                fmt(live_close),
                f"[{style}]{diff:+.4f}[/{style}]",
            )
        else:
            table.add_row(ts.date().isoformat(), fmt(fixture_close), "[dim]없음[/dim]", "-")
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 지표/데이터 레이어 육안 검증")
    parser.add_argument("--ticker", default="AAPL", help="픽스처 티커 (기본: AAPL)")
    parser.add_argument("--live", action="store_true", help="yfinance 재수집본과 대조 (네트워크)")
    parser.add_argument(
        "--bars",
        type=int,
        default=DEFAULT_BARS,
        help=f"표시할 최근 봉 수 (기본 {DEFAULT_BARS}). 터미널이 좁으면 줄일 것",
    )
    args = parser.parse_args()


    df = load(args.ticker)
    console.print(
        f"\n[bold]{args.ticker}[/bold]  "
        f"{len(df)}봉  {df.index[0].date()} ~ {df.index[-1].date()}  "
        f"[dim](고정 픽스처, 네트워크 미사용)[/dim]\n"
    )

    show_raw_bars(df, args.ticker, args.bars)
    console.print()
    show_indicators(df, args.ticker, args.bars)
    console.print()
    show_contract_check(df, args.ticker)

    if args.live:
        console.print()
        show_live_diff(df, args.ticker, args.bars)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
