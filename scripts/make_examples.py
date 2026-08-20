"""examples/*.json 재생성 — 목업이 아니라 **실제 전략 출력**이다.

## 왜 손으로 채우지 않는가

examples는 프론트엔드가 보고 개발하는 참조 문서다. Phase 0에서 손으로 채운 목업은
Phase 3(미너비니 구현)과 Phase 4(와인스타인·Qullamaggie 구현)를 따라가지 못했고,
결국 '스키마 검증은 통과하지만 내용이 거짓인' 파일이 남았다 — 존재하지 않는 게이트
체크 id, 구현이 금지한 게이트 구성, 발생 불가능한 시나리오. 계약 테스트는 **형태**만
보므로 그 드리프트를 잡지 못한다.

그래서 목업을 손으로 고치는 대신 실제 구현으로 생성한다. 드리프트가 구조적으로
불가능해진다 — 전략이 바뀌면 이 스크립트를 다시 돌려야 하고, 돌리면 파일이 바뀐다.
`tests/test_examples.py`가 `--check`와 같은 비교를 CI에서 수행한다.

## 시나리오는 고르는 것이지 지어내는 것이 아니다

세 파일의 성격(BUY / 게이트 근소 탈락 / 미완성 봉)은 고정이지만, 그 상태를 만들어내는
숫자는 픽스처 안에서 **찾는다**. 조건에 맞는 시점이 없으면 파일을 쓰지 않고 실패한다.
'없는 상태를 그럴듯하게 지어내기'가 애초에 문제였기 때문이다.

    python scripts/make_examples.py
    python scripts/make_examples.py --check   # 파일을 쓰지 않고 최신인지만 확인
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from core.context import StockContext, build_context  # noqa: E402
from core.report import build_report  # noqa: E402
from core.types import (  # noqa: E402
    BarMeta,
    MarketRegime,
    SessionState,
    Stage,
    StrategyVerdict,
    Verdict,
)
from core.watchlist import build_watchlist  # noqa: E402
from data.universe import (  # noqa: E402
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
    survivorship_warning,
)
from regime.market import regime_series, stage_series  # noqa: E402
from render.json_out import to_json, watchlist_to_json  # noqa: E402
from risk.planner import build_risk_plans  # noqa: E402
from strategies.registry import ALL, build_strategies  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"
EXAMPLES_DIR = ROOT / "examples"
UNIVERSE = "us_large"

# 생성 시각을 고정한다. 매 실행마다 바뀌면 diff가 항상 더러워져서 '무엇이 실제로
# 달라졌는지'를 볼 수 없다.
GENERATED_AT = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)

# 예시의 리스크 플랜을 '주수까지 채워진' 모습으로 보여주기 위한 계좌 규모다.
# DEFAULT_CONFIG는 account_equity가 None이라 shares/position_value가 전부 null이 되는데,
# 프론트 참조 문서로는 채워진 형태가 필요하다 (None 경로는 유닛 테스트가 잠근다).
EXAMPLE_RISK = replace(DEFAULT_CONFIG.risk, account_equity=100_000.0)

# 지표 워밍업(252봉 52주 + 기울기)이 끝난 뒤부터 훑는다.
SCAN_START = 300
SCAN_STEP = 2


def make_strategies():
    """등록된 전략 전부. 목록은 `strategies/registry.py` 한 곳에만 있다."""
    return build_strategies(ALL, DEFAULT_CONFIG)


def load_panel() -> dict[str, pd.DataFrame]:
    panel = pd.read_parquet(FIXTURE_DIR / f"panel_{UNIVERSE}_ohlcv.parquet")
    frames: dict[str, pd.DataFrame] = {}
    for ticker, group in panel.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date").sort_index()
        frame.index.name = "date"
        frames[str(ticker)] = frame.astype(float)
    return frames


def context_at(
    ticker: str,
    df: pd.DataFrame,
    position: int,
    *,
    regimes,
    stages,
    rs,
    complete: bool = True,
) -> StockContext:
    """시점 position의 컨텍스트. 백테스트와 **같은 경로**로 만든다.

    complete=False면 장중 실행을 재현한다 — 봉이 미완성이므로 거래량 판정이 불가하고,
    전략은 그 사실을 스스로 반영해야 한다 (만점 축소, BUY 보류).
    """
    window = df.iloc[: position + 1]
    as_of = window.index[-1].date()
    return build_context(
        ticker,
        window,
        DEFAULT_CONFIG,
        regime=regimes.get(as_of, MarketRegime.CAUTION),
        stage=stages.get(as_of, Stage.UNDEFINED),
        rs_percentile=rs.get(as_of),
        bar_meta=BarMeta(
            last_bar_date=as_of,
            session_state=SessionState.CLOSED if complete else SessionState.OPEN,
            is_bar_complete=complete,
            bars_available=len(window),
            volume_judgements_reliable=complete,
        ),
    )


def evaluate(ctx: StockContext) -> list[StrategyVerdict]:
    return [strategy.evaluate(ctx) for strategy in make_strategies()]


def scan(frames, regimes, percentiles):
    """(BUY 시점, 게이트 근소 탈락 시점)을 픽스처에서 찾는다.

    - BUY 시점: BUY를 낸 전략이 가장 많은 시점.
    - 근소 탈락 시점: 한 조건만 모자라 탈락한 전략(pass_count == total-1)이 있고,
      동시에 다른 전략은 게이트를 통과한 시점. '같은 차트, 다른 자'를 보여준다.
    """
    best_buy: tuple[int, str, int] | None = None
    best_reject: tuple[str, int] | None = None

    for ticker, df in sorted(frames.items()):
        stages = stage_series(df, DEFAULT_CONFIG.regime)
        rs = rs_percentile_series(ticker, percentiles)
        if not rs:
            continue

        for position in range(SCAN_START, len(df), SCAN_STEP):
            ctx = context_at(ticker, df, position, regimes=regimes, stages=stages, rs=rs)
            if ctx.indicators.rs_percentile is None:
                continue
            verdicts = evaluate(ctx)

            buys = sum(1 for v in verdicts if v.verdict is Verdict.BUY)
            if buys and (best_buy is None or buys > best_buy[0]):
                best_buy = (buys, ticker, position)

            if best_reject is None:
                near_miss = [
                    v
                    for v in verdicts
                    if not v.gate.passed and v.gate.pass_count == v.gate.total - 1
                ]
                if near_miss and any(v.gate.passed for v in verdicts):
                    best_reject = (ticker, position)

    if best_buy is None:
        raise SystemExit("픽스처에서 BUY 시점을 찾지 못했다 — 예시를 지어내지 않는다")
    if best_reject is None:
        raise SystemExit("픽스처에서 게이트 근소 탈락 시점을 찾지 못했다")
    return (best_buy[1], best_buy[2]), best_reject


def load_market() -> dict:
    """픽스처 일체 — 패널·유니버스 RS·시장 국면. 한 번만 읽는다."""
    cfg = DEFAULT_CONFIG
    closes = pd.read_parquet(FIXTURE_DIR / f"universe_{UNIVERSE}_closes.parquet")
    spy = pd.read_csv(FIXTURE_DIR / "SPY_3y.csv", index_col=0, parse_dates=True).astype(float)
    return {
        "frames": load_panel(),
        "regimes": regime_series(spy, cfg.regime),
        "percentiles": rs_percentile_frame(rs_score_frame(closes, cfg.universe), cfg.universe),
        "universe_size": closes.shape[1],
    }


def payload_at(market: dict, ticker: str, position: int, *, complete: bool) -> str:
    """한 시점의 리포트 JSON 본문."""
    df = market["frames"][ticker]
    ctx = context_at(
        ticker,
        df,
        position,
        regimes=market["regimes"],
        stages=stage_series(df, DEFAULT_CONFIG.regime),
        rs=rs_percentile_series(ticker, market["percentiles"]),
        complete=complete,
    )
    verdicts = evaluate(ctx)
    report = build_report(
        ctx,
        verdicts,
        generated_at=GENERATED_AT,
        risk_plans=build_risk_plans(ctx, verdicts, EXAMPLE_RISK),
        warnings=(survivorship_warning(UNIVERSE, market["universe_size"]),),
    )
    return to_json(report) + "\n"


def payload_for_date(market: dict, ticker: str, as_of: date, *, complete: bool) -> str:
    """날짜로 지정한 시점의 리포트 JSON 본문.

    테스트가 쓰는 진입점이다 — 이미 있는 예시의 (티커, as_of)를 그대로 재현하면
    패널 전체를 훑지 않고도 '파일이 현재 구현의 출력과 같은가'를 확인할 수 있다.
    """
    positions = market["frames"][ticker].index.get_indexer([pd.Timestamp(as_of)])
    if positions[0] < 0:
        raise KeyError(f"{ticker} 픽스처에 {as_of} 봉이 없다")
    return payload_at(market, ticker, int(positions[0]), complete=complete)


def watchlist_payload(market: dict, as_of: date) -> str:
    """같은 날짜에 패널 전 종목을 진단해 워치리스트로 조립한다.

    `sample_buy.json`과 **같은 날짜**를 쓴다 — 워치리스트의 한 줄을 펼치면 그 진단
    리포트가 나온다는 관계를 예시로 보여주기 위함이다.
    """
    reports = []
    for ticker, df in sorted(market["frames"].items()):
        positions = df.index.get_indexer([pd.Timestamp(as_of)])
        if positions[0] < 0:
            continue  # 그날 봉이 없는 종목은 스캔에서 빠진다 (거래정지 등)
        ctx = context_at(
            ticker,
            df,
            int(positions[0]),
            regimes=market["regimes"],
            stages=stage_series(df, DEFAULT_CONFIG.regime),
            rs=rs_percentile_series(ticker, market["percentiles"]),
        )
        verdicts = evaluate(ctx)
        reports.append(
            build_report(
                ctx,
                verdicts,
                generated_at=GENERATED_AT,
                risk_plans=build_risk_plans(ctx, verdicts, EXAMPLE_RISK),
            )
        )

    watchlist = build_watchlist(
        UNIVERSE,
        reports,
        regime=reports[0].regime,
        warnings=(survivorship_warning(UNIVERSE, market["universe_size"]),),
        generated_at=GENERATED_AT,
    )
    return watchlist_to_json(watchlist) + "\n"


def build_payloads() -> dict[str, str]:
    """예시 JSON 본문. 시나리오에 맞는 시점을 픽스처에서 찾아 생성한다."""
    market = load_market()
    (buy_ticker, buy_position), (reject_ticker, reject_position) = scan(
        market["frames"], market["regimes"], market["percentiles"]
    )
    buy_as_of = market["frames"][buy_ticker].index[buy_position].date()
    return {
        "sample_watchlist.json": watchlist_payload(market, buy_as_of),
        "sample_buy.json": payload_at(market, buy_ticker, buy_position, complete=True),
        "sample_gate_reject.json": payload_at(
            market, reject_ticker, reject_position, complete=True
        ),
        # 미완성 봉 예시는 BUY 시점과 **같은 봉**이다. 같은 차트에서 '봉이 확정되지
        # 않았다'는 사실 하나만으로 판정이 어떻게 달라지는지 보여주는 것이 목적이다.
        "sample_incomplete_bar.json": payload_at(
            market, buy_ticker, buy_position, complete=False
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="examples/*.json 재생성")
    parser.add_argument(
        "--check",
        action="store_true",
        help="파일을 쓰지 않고 현재 examples/가 구현과 일치하는지만 확인한다",
    )
    args = parser.parse_args()

    payloads = build_payloads()
    stale = []
    for name, payload in payloads.items():
        path = EXAMPLES_DIR / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == payload:
            continue
        stale.append(name)
        if not args.check:
            path.write_text(payload, encoding="utf-8")

    if args.check:
        if stale:
            print("구현과 어긋난 예시: " + ", ".join(stale))
            print("python scripts/make_examples.py 로 재생성할 것")
            return 1
        print("examples/ 는 현재 구현의 출력과 일치한다")
        return 0

    print("갱신: " + (", ".join(stale) if stale else "없음 (이미 최신)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
