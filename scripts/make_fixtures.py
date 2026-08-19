"""tests/fixtures/ 고정 CSV를 만드는 1회성 스크립트.

**테스트는 절대 이 스크립트를 부르지 않는다.** 네트워크에 의존하는 테스트는
인터넷 상태나 API 변경으로 깨지고, 그러면 실패 원인이 지표 버그인지
네트워크 문제인지 구분할 수 없다.

픽스처를 갱신해야 할 때만 사람이 손으로 실행한다:
    python scripts/make_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG  # noqa: E402
from data.fetcher import fetch_ohlcv  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
# 종목 + 각 시장의 벤치마크 지수.
# 벤치마크는 regime 판정(지수 vs 200일선, 분산일)과 상대강도(RS) 산출에 쓰인다.
# 유니버스 백분위는 Phase 3.5이고, 그 전까지는 지수 대비 상대강도로 근사한다.
TICKERS = ["AAPL", "005930.KS", "SPY", "^KS11"]


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for ticker in TICKERS:
        df = fetch_ohlcv(ticker, DEFAULT_CONFIG.data)
        safe = ticker.replace(".", "_")
        out = FIXTURE_DIR / f"{safe}_3y.csv"
        df.to_csv(out, float_format="%.6f")
        print(
            f"{ticker:12s} {len(df):5d} bars  "
            f"{df.index[0].date()} ~ {df.index[-1].date()}  -> {out.name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
