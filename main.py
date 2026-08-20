"""CLI 엔트리포인트.

python main.py AAPL
python main.py AAPL --json
python main.py AAPL --equity 100000 --risk-pct 1.0
python main.py --scan us_large --top 20

## diagnose()가 유일한 오케스트레이션 지점이다

수집 -> 지표 -> 국면/Stage -> RS -> 전략별 평가 -> 리스크 플랜 -> 리포트.
CLI와 향후 웹 API가 이 함수 하나를 공유한다. 여기서 임계값을 비교하거나 판정을
내리지 않는다 — 판정은 전략의 몫이고, 이 함수는 재료를 모아 넘기고 결과를 조립한다.

## RS 유니버스가 없으면 전 종목이 게이트에서 탈락한다

세 전략 모두 게이트에 RS 조건이 있고, RS가 None이면 UNAVAILABLE이며, UNAVAILABLE은
AND 게이트를 막는다. 그래서 이 파이프라인은 **유니버스 전체의 종가**를 먼저 확보한다
(`load_universe_closes`). 첫 실행은 구성종목 수만큼 네트워크를 타고, 이후에는 하루
단위 캐시를 읽는다. 실패해도 진단은 계속하되 이유를 경고로 남긴다 — 조용히 전부
탈락시키면 사용자는 '이 종목이 나쁘다'로 읽는다.

## 스캔은 진단을 여러 번 돌린 것이다

`scan()`은 유니버스의 종목마다 `diagnose()`를 **그대로** 부르고 결과를 요약해
워치리스트로 조립한다. 요약용 별도 계산을 만들지 않는 이유: 그렇게 하면 '목록에서는
BUY였는데 눌러 보니 WATCH'가 언젠가 반드시 생긴다. 판정 경로가 하나여야 목록과
상세가 같은 말을 한다.

시장 공통 재료(국면·유니버스 종가·RS 프레임)는 `MarketData`로 **한 번만** 만들어
종목마다 재사용한다. 종목마다 다시 만들면 116종목 스캔이 RS 프레임을 116번 계산한다.

## 전략 하나가 터져도 나머지는 낸다

`WarningCode.STRATEGY_ERROR`의 계약상 의미가 '해당 전략만 실패'다. 예외를 낸 전략은
판정 목록에서 빠지고 경고로 남으며, 컨센서스의 분모(total_strategies)도 그만큼 줄어든다.
빠진 전략을 REJECTED_BY_GATE로 채우면 '그 방법론이 거절했다'는 거짓이 된다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

import pandas as pd

from config import AppConfig
from core.context import build_context
from core.report import build_report
from core.types import (
    DiagnosisReport,
    DiagnosticWarning,
    MarketRegime,
    ScanFailure,
    Severity,
    Verdict,
    WarningCode,
    WatchlistReport,
)
from core.watchlist import build_watchlist
from data.fetcher import DataError, load_ohlcv, resolve_session
from data.universe import (
    UniverseDataError,
    benchmark_for,
    load_universe_closes,
    load_universe_tickers,
    missing_members_warning,
    rs_percentile_against,
    rs_percentile_frame,
    rs_percentile_series,
    rs_score_frame,
    small_universe_warning,
    survivorship_warning,
    universe_for,
)
from regime.market import classify_regime, classify_stage
from render.cli import render_report, render_watchlist
from render.json_out import to_json, watchlist_to_json
from risk.planner import build_risk_plans
from strategies.base import Strategy
from strategies.registry import ALL, UnknownStrategyError, build_strategies

# 수집·스캔 진행 알림. (라벨, 완료 수, 전체 수). data 레이어가 rich를 모르게 하려고
# 콜백으로 뺐다 — 화면 그리기는 CLI의 몫이다.
ProgressFn = Callable[[str, int, int], None]


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="trading-tracker",
        description="티커를 여러 추세추종 방법론으로 각각 독립 진단한다.",
    )
    parser.add_argument(
        "ticker", nargs="?", default=None, help="진단할 티커. 예: AAPL"
    )
    parser.add_argument(
        "--scan",
        metavar="UNIVERSE",
        default=None,
        help="유니버스 전체를 스캔해 워치리스트를 만든다. 예: --scan us_large",
    )
    parser.add_argument("--json", action="store_true", help="rich 대신 JSON 출력")
    parser.add_argument(
        "--equity", type=float, default=None, help="계좌 평가금액 (포지션 사이징용)"
    )
    parser.add_argument("--risk-pct", type=float, default=None, help="트레이드당 감수 리스크 %%")
    parser.add_argument("--no-cache", action="store_true", help="parquet 캐시 무시")
    parser.add_argument(
        "--strategies",
        default=ALL,
        help="쉼표 구분 전략 목록. 예: minervini,qullamaggie",
    )
    parser.add_argument(
        "--top", type=int, default=None, help="스캔 결과 상위 N종목만 표시 (기본: 전부)"
    )
    parser.add_argument(
        "--verdict",
        default=None,
        help="스캔 결과를 판정으로 거른다. 예: --verdict BUY,WATCH",
    )
    return parser


def load_strategies(names: str, config: AppConfig) -> list[Strategy]:
    """이름으로 전략 플러그인을 조립한다. 코어는 전략 목록을 하드코딩하지 않는다.

    목록은 `strategies/registry.py`에 있다 — 전략 추가가 코어 수정을 요구하면
    플러그인 구조가 아니기 때문이다.
    """
    return build_strategies(names, config)


def apply_cli_overrides(config: AppConfig, *, equity: float | None, risk_pct: float | None):
    """CLI 인자를 config에 얹는다. frozen dataclass이므로 replace로 변형본을 만든다."""
    risk = config.risk
    if equity is not None:
        risk = replace(risk, account_equity=equity)
    if risk_pct is not None:
        risk = replace(risk, risk_pct_per_trade=risk_pct)
    return config if risk is config.risk else replace(config, risk=risk)


# ---------------------------------------------------------------------------
# 시장 공통 재료 — 티커 하나든 116개든 한 번만 만든다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketData:
    """한 시장의 공통 재료: 국면 + 유니버스 종가 + RS 프레임.

    종목마다 다시 만들면 116종목 스캔이 RS 프레임을 116번 계산한다. 한 번 만들어
    돌려 쓰되, **종목별로 달라지는 것(백분위 조회)은 메서드로 그때 계산한다.**
    """

    exchange: str | None
    regime: MarketRegime
    universe_name: str
    closes: pd.DataFrame | None
    scores: pd.DataFrame | None
    percentiles: pd.DataFrame | None
    warnings: tuple[DiagnosticWarning, ...] = ()

    def rs_percentile_for(
        self, ticker: str, ohlcv: pd.DataFrame, config: AppConfig
    ) -> float | None:
        """이 종목의 RS 백분위. 유니버스를 확보하지 못했으면 None.

        구성종목이면 자기 열의 백분위를, 아니면 유니버스 분포 대비 순위를 낸다
        (두 경로의 규약은 strictly-less로 같다).
        """
        if self.closes is None or self.scores is None or self.percentiles is None:
            return None

        upper = ticker.upper()
        as_of = ohlcv.index[-1].date()
        if upper in self.closes.columns:
            series = rs_percentile_series(upper, self.percentiles)
        else:
            series = rs_percentile_against(ohlcv["close"], self.scores, config.universe)
        return series.get(as_of)


def _resolve_regime(
    benchmark: str,
    config: AppConfig,
    *,
    use_cache: bool,
    now: datetime | None,
) -> tuple[MarketRegime, list[DiagnosticWarning]]:
    """벤치마크 지수로 시장 국면을 판정한다. 실패하면 CAUTION + 경고.

    CAUTION으로 떨어뜨리는 것은 regime 모듈의 정책과 같다 — 근거가 없으면 낙관도
    비관도 하지 않는다. 다만 '근거가 없어서 CAUTION'과 '재서 CAUTION'은 다른 사실이므로
    경고로 구분한다.
    """
    try:
        bundle = load_ohlcv(
            benchmark, config.data, config.exchanges, use_cache=use_cache, now=now
        )
    except DataError as error:
        return MarketRegime.CAUTION, [
            DiagnosticWarning(
                code=WarningCode.BENCHMARK_UNAVAILABLE,
                severity=Severity.WARN,
                message=(
                    f"벤치마크 {benchmark} 수집 실패로 시장 국면을 판정하지 못했다 "
                    f"({error}). 국면을 CAUTION으로 두었으나 이는 측정값이 아니다"
                ),
                field="regime",
            )
        ]
    return classify_regime(bundle.ohlcv, config.regime), []


def load_market_data(
    config: AppConfig,
    *,
    exchange: str | None = None,
    universe_name: str | None = None,
    use_cache: bool = True,
    now: datetime | None = None,
    progress: ProgressFn | None = None,
) -> MarketData:
    """국면·유니버스·RS 프레임을 한 번에 확보한다.

    유니버스 수집이 실패해도 예외를 올리지 않는다 — RS가 None이 되고 그 이유가
    CRITICAL 경고로 남을 뿐이다. 진단 자체는 계속되어야 사용자가 '왜 전부 탈락인지'를
    화면에서 읽을 수 있다.
    """
    name = universe_name or universe_for(exchange, config.universe)
    regime, warnings = _resolve_regime(
        benchmark_for("", config.regime, exchange), config, use_cache=use_cache, now=now
    )

    try:
        universe = load_universe_closes(
            name,
            config.data,
            config.exchanges,
            use_cache=use_cache,
            now=now,
            progress=progress,
        )
    except (UniverseDataError, DataError, FileNotFoundError) as error:
        warnings.append(
            DiagnosticWarning(
                code=WarningCode.RS_UNIVERSE_MISSING,
                severity=Severity.CRITICAL,
                message=(
                    f"'{name}' 유니버스 종가를 확보하지 못해 RS 백분위를 계산할 수 없다 "
                    f"({error}). RS 조건은 UNAVAILABLE이 되고, 세 전략 모두 AND 게이트가 "
                    "막혀 판정이 REJECTED_BY_GATE로 나온다 — 종목의 문제가 아니다"
                ),
                field="indicators.rs_percentile",
            )
        )
        return MarketData(exchange, regime, name, None, None, None, tuple(warnings))

    warnings.append(survivorship_warning(name, universe.size))
    if universe.missing:
        warnings.append(missing_members_warning(name, universe.missing, universe.size))
    if universe.size < config.universe.min_universe_size * 2:
        warnings.append(
            small_universe_warning(universe.size, config.universe.min_universe_size)
        )

    scores = rs_score_frame(universe.closes, config.universe)
    return MarketData(
        exchange=exchange,
        regime=regime,
        universe_name=name,
        closes=universe.closes,
        scores=scores,
        percentiles=rs_percentile_frame(scores, config.universe),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# 진단 (단건) / 스캔 (다종목)
# ---------------------------------------------------------------------------


def diagnose(
    ticker: str,
    config: AppConfig,
    strategies: list[Strategy],
    *,
    use_cache: bool = True,
    now: datetime | None = None,
    market: MarketData | None = None,
) -> DiagnosisReport:
    """수집 -> 지표 -> 국면 -> 전략별 평가 -> 리스크 플랜 -> 리포트 조립.

    판정 로직의 유일한 오케스트레이션 지점. CLI와 향후 웹 API가 이 함수를 공유한다.

    market을 넘기면 국면·RS 재료를 다시 만들지 않는다 (스캔이 쓰는 경로). 넘기지
    않으면 이 티커의 거래소에 맞는 재료를 그 자리에서 만든다.
    """
    bundle = load_ohlcv(ticker, config.data, config.exchanges, use_cache=use_cache, now=now)

    if market is None:
        session = resolve_session(ticker, config.exchanges)
        market = load_market_data(
            config,
            exchange=session.exchange if session is not None else None,
            use_cache=use_cache,
            now=now,
        )

    ctx = build_context(
        ticker,
        bundle.ohlcv,
        config,
        regime=market.regime,
        bar_meta=bundle.bar_meta,
        stage=classify_stage(bundle.ohlcv, config.regime),
        rs_percentile=market.rs_percentile_for(ticker, bundle.ohlcv, config),
        warnings=(*bundle.warnings, *market.warnings),
    )

    verdicts = []
    failures: list[DiagnosticWarning] = []
    for strategy in strategies:
        try:
            verdicts.append(strategy.evaluate(ctx))
        except Exception as error:  # noqa: BLE001 — 전략 하나의 실패가 진단 전체를 죽이지 않는다
            failures.append(
                DiagnosticWarning(
                    code=WarningCode.STRATEGY_ERROR,
                    severity=Severity.CRITICAL,
                    message=(
                        f"전략 '{strategy.name}' 평가 중 오류로 판정을 내지 못했다 "
                        f"({type(error).__name__}: {error}). 이 전략은 판정 목록에서 "
                        "빠졌으며 '거절'이 아니다"
                    ),
                    field="strategy_verdicts",
                )
            )

    return build_report(
        ctx,
        verdicts,
        risk_plans=build_risk_plans(ctx, verdicts, config.risk),
        warnings=tuple(failures),
    )


def scan(
    universe_name: str,
    config: AppConfig,
    strategies: list[Strategy],
    *,
    tickers: list[str] | None = None,
    use_cache: bool = True,
    now: datetime | None = None,
    progress: ProgressFn | None = None,
) -> WatchlistReport:
    """유니버스 전체를 진단해 워치리스트로 조립한다.

    **종목마다 `diagnose()`를 그대로 부른다.** 요약용 별도 계산을 만들면 목록과 상세가
    갈라지고, 그것은 이 도구를 못 믿게 만드는 가장 빠른 길이다.

    한 종목의 수집 실패는 스캔을 멈추지 않는다 — `failed`에 이유와 함께 남는다.
    조용히 빠지면 '116종목 스캔'이라는 말이 거짓이 된다.
    """
    exchange = {
        name: code for code, name in config.universe.universe_by_exchange
    }.get(universe_name)

    market = load_market_data(
        config,
        exchange=exchange,
        universe_name=universe_name,
        use_cache=use_cache,
        now=now,
        progress=progress,
    )

    members = tickers if tickers is not None else load_universe_tickers(universe_name)
    reports: list[DiagnosisReport] = []
    failed: list[ScanFailure] = []

    for index, ticker in enumerate(members, start=1):
        if progress is not None:
            progress(f"진단 {ticker}", index, len(members))
        try:
            reports.append(
                diagnose(
                    ticker,
                    config,
                    strategies,
                    use_cache=use_cache,
                    now=now,
                    market=market,
                )
            )
        except DataError as error:
            failed.append(ScanFailure(ticker=ticker, reason=str(error)))

    return build_watchlist(
        universe_name,
        reports,
        regime=market.regime,
        failed=failed,
        warnings=market.warnings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_verdict_filter(raw: str | None) -> set[Verdict] | None:
    """--verdict 인자를 enum 집합으로. 잘못된 이름은 조용히 무시하지 않는다."""
    if raw is None:
        return None
    names = [name.strip().upper() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in Verdict.__members__]
    if unknown:
        raise UnknownStrategyError(
            f"알 수 없는 판정: {', '.join(unknown)} "
            f"(사용 가능: {', '.join(v.value for v in Verdict)})"
        )
    return {Verdict[name] for name in names}


def _cli_progress(console_width: int = 0) -> ProgressFn:
    """진행 상황을 한 줄로 덮어쓴다.

    116종목을 받는 동안 화면이 조용하면 사용자는 멈춘 줄 안다. rich Progress 대신
    단순 캐리지리턴을 쓰는 이유는 stdout이 파이프로 넘어갈 때(--json)도 stderr로
    안전하게 흐르게 하기 위함이다.
    """

    def report(label: str, done: int, total: int) -> None:
        line = f"\r  [{done:>3}/{total}] {label:<28}"
        print(line[: console_width or 60], end="", file=sys.stderr, flush=True)
        if done >= total:
            print(file=sys.stderr)

    return report


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 데이터 문제는 사용자 메시지로, 그 외 예외는 그대로 올린다."""
    args = build_parser().parse_args(argv)
    if not args.ticker and not args.scan:
        print("오류: 티커를 주거나 --scan UNIVERSE 를 지정할 것", file=sys.stderr)
        return 2
    if args.ticker and args.scan:
        print("오류: 티커와 --scan은 함께 쓸 수 없다", file=sys.stderr)
        return 2

    config = apply_cli_overrides(AppConfig(), equity=args.equity, risk_pct=args.risk_pct)

    try:
        strategies = load_strategies(args.strategies, config)
        verdict_filter = parse_verdict_filter(args.verdict)
    except UnknownStrategyError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2

    try:
        if args.scan:
            watchlist = scan(
                args.scan,
                config,
                strategies,
                use_cache=not args.no_cache,
                progress=None if args.json else _cli_progress(),
            )
            if args.json:
                print(watchlist_to_json(watchlist))
            else:
                render_watchlist(watchlist, top=args.top, verdicts=verdict_filter)
            return 0

        report = diagnose(args.ticker, config, strategies, use_cache=not args.no_cache)
    except FileNotFoundError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    except DataError as error:
        print(f"데이터 오류: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(to_json(report))
    else:
        render_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
