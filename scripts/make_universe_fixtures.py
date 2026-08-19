"""유니버스 종가 픽스처를 만드는 1회성 스크립트.

**테스트는 절대 이 스크립트를 부르지 않는다.** make_fixtures.py와 같은 원칙이다.

RS 백분위는 종가만 있으면 계산되므로 OHLCV 전체가 아니라 종가 행렬만 저장한다
(116종목 x 3년 OHLCV는 수십 MB지만 종가만이면 수백 KB다).

    python scripts/make_universe_fixtures.py

TLS 인터셉션 환경이면 SSL_CERT_FILE에 사내 루트 CA 번들 경로를 지정할 것.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from data.universe import load_universe_tickers  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UNIVERSES = ["us_large", "kospi"]
BATCH_SIZE = 40

# 다종목 백테스트용 패널. RS는 종가만 있으면 되지만 백테스트는 OHLCV가 전부 필요하다
# (진입가 = 시가, 최대역행폭 = 저가, 지표 = 거래량). 유니버스 전체를 OHLCV로 받으면
# 수십 MB라 커밋할 수 없으므로 **일정 간격으로 골라** 부분집합만 받는다.
# 간격 선택은 성과와 무관한 결정론적 규칙이다 — 잘 나온 종목을 고르면 그 자체가 편향이다.
PANEL_STRIDE = {"us_large": 4, "kospi": 5}


def fetch_closes(tickers: list[str], years: int) -> pd.DataFrame:
    """배치로 나눠 종가만 수집한다. 실패한 티커는 조용히 빠진다."""
    import yfinance as yf

    end = datetime.now(UTC).date() + timedelta(days=1)
    start = end - timedelta(days=int(years * 365.25) + 7)

    frames: list[pd.DataFrame] = []
    for offset in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[offset : offset + BATCH_SIZE]
        raw = yf.download(
            batch,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            actions=False,
            threads=True,
        )
        if raw is None or len(raw) == 0:
            print(f"  배치 {offset // BATCH_SIZE + 1}: 결과 없음", file=sys.stderr)
            continue
        closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        frames.append(closes)
        print(f"  배치 {offset // BATCH_SIZE + 1}: {closes.shape[1]}종목 {len(closes)}봉")

    merged = pd.concat(frames, axis=1)
    merged.index = pd.DatetimeIndex(merged.index).tz_localize(None).normalize()
    merged.index.name = "date"
    return merged.sort_index()


def fetch_panel(tickers: list[str], years: int) -> pd.DataFrame:
    """백테스트용 OHLCV 패널. long 포맷(date, ticker, open..volume)으로 모은다.

    wide 포맷(MultiIndex 컬럼)보다 저장이 단순하고 티커별 결측 처리가 명확하다.
    """
    import yfinance as yf

    end = datetime.now(UTC).date() + timedelta(days=1)
    start = end - timedelta(days=int(years * 365.25) + 7)
    raw = yf.download(
        tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        actions=False,
        threads=True,
    )

    rows: list[pd.DataFrame] = []
    for ticker in tickers:
        try:
            frame = raw.xs(ticker, axis=1, level=1)
        except KeyError:
            continue
        frame = frame.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        frame = frame[frame["close"].notna()].copy()
        if len(frame) < 400:
            continue
        frame["ticker"] = ticker
        rows.append(frame.reset_index().rename(columns={"Date": "date", "index": "date"}))

    panel = pd.concat(rows, ignore_index=True)
    panel["date"] = pd.DatetimeIndex(panel["date"]).tz_localize(None).normalize()
    return panel.sort_values(["ticker", "date"]).reset_index(drop=True)


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    years = DEFAULT_CONFIG.data.history_years

    for name in UNIVERSES:
        tickers = load_universe_tickers(name)
        print(f"{name}: {len(tickers)}종목 요청")
        closes = fetch_closes(tickers, years)

        # 종가가 절반도 없는 티커는 제외한다 — 부분 데이터는 RS 점수를 왜곡한다.
        coverage = closes.notna().mean()
        usable = closes.loc[:, coverage >= 0.5]
        dropped = sorted(set(closes.columns) - set(usable.columns))

        out = FIXTURE_DIR / f"universe_{name}_closes.parquet"
        usable.to_parquet(out)
        print(
            f"  -> {out.name}: {usable.shape[1]}종목 x {len(usable)}봉, "
            f"{out.stat().st_size / 1024:.0f}KB"
        )
        if dropped:
            print(f"  제외(커버리지 50% 미만): {', '.join(dropped)}")

        panel_tickers = sorted(usable.columns)[:: PANEL_STRIDE[name]]
        print(f"  백테스트 패널 {len(panel_tickers)}종목 수집 중...")
        panel = fetch_panel(panel_tickers, years)
        panel_out = FIXTURE_DIR / f"panel_{name}_ohlcv.parquet"
        panel.to_parquet(panel_out)
        print(
            f"  -> {panel_out.name}: {panel['ticker'].nunique()}종목 "
            f"{len(panel):,}행, {panel_out.stat().st_size / 1024:.0f}KB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
