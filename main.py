"""CLI 엔트리포인트.

python main.py AAPL
python main.py AAPL --json
python main.py AAPL --equity 100000 --risk-pct 1.0

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

## 전략 하나가 터져도 나머지는 낸다

`WarningCode.STRATEGY_ERROR`의 계약상 의미가 '해당 전략만 실패'다. 예외를 낸 전략은
판정 목록에서 빠지고 경고로 남으며, 컨센서스의 분모(total_strategies)도 그만큼 줄어든다.
빠진 전략을 REJECTED_BY_GATE로 채우면 '그 방법론이 거절했다'는 거짓이 된다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime

import pandas as pd

from config import AppConfig
from core.context import build_context
from core.report import build_report
from core.types import (
    DiagnosisReport,
    DiagnosticWarning,
    MarketRegime,
    Severity,
    WarningCode,
)
from data.fetcher import DataError, load_ohlcv, resolve_session
from data.universe import (
    UniverseDataError,
    benchmark_for,
    load_universe_closes,
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
from render.cli import render_report
from render.json_out import to_json
from risk.planner import build_risk_plans
from strategies.base import Strategy
from strategies.registry import ALL, UnknownStrategyError, build_strategies


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="trading-tracker",
        description="티커를 여러 추세추종 방법론으로 각각 독립 진단한다.",
    )
    parser.add_argument("ticker", help="진단할 티커. 예: AAPL")
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


def _resolve_regime(
    ticker: str,
    config: AppConfig,
    *,
    use_cache: bool,
    now: datetime | None,
    exchange: str | None,
) -> tuple[MarketRegime, list[DiagnosticWarning]]:
    """벤치마크 지수로 시장 국면을 판정한다. 실패하면 CAUTION + 경고.

    CAUTION으로 떨어뜨리는 것은 regime 모듈의 정책과 같다 — 근거가 없으면 낙관도
    비관도 하지 않는다. 다만 '근거가 없어서 CAUTION'과 '재서 CAUTION'은 다른 사실이므로
    경고로 구분한다.
    """
    benchmark = benchmark_for(ticker, config.regime, exchange)
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


def _resolve_rs_percentile(
    ticker: str,
    ohlcv: pd.DataFrame,
    config: AppConfig,
    *,
    use_cache: bool,
    now: datetime | None,
    exchange: str | None,
) -> tuple[float | None, list[DiagnosticWarning]]:
    """유니버스 교차단면 RS 백분위. 실패하면 None + 이유 경고.

    구성종목이면 자기 열의 백분위를, 아니면 유니버스 분포 대비 순위를 낸다
    (두 경로의 규약은 strictly-less로 같다).
    """
    name = universe_for(exchange, config.universe)
    try:
        universe = load_universe_closes(
            name, config.data, config.exchanges, use_cache=use_cache, now=now
        )
    except (UniverseDataError, DataError, FileNotFoundError) as error:
        return None, [
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
        ]

    warnings = [survivorship_warning(name, universe.size)]
    if universe.missing:
        warnings.append(missing_members_warning(name, universe.missing, universe.size))
    if universe.size < config.universe.min_universe_size * 2:
        warnings.append(
            small_universe_warning(universe.size, config.universe.min_universe_size)
        )

    scores = rs_score_frame(universe.closes, config.universe)
    as_of = ohlcv.index[-1].date()
    upper = ticker.upper()

    if upper in universe.closes.columns:
        series = rs_percentile_series(upper, rs_percentile_frame(scores, config.universe))
    else:
        series = rs_percentile_against(ohlcv["close"], scores, config.universe)

    return series.get(as_of), warnings


def diagnose(
    ticker: str,
    config: AppConfig,
    strategies: list[Strategy],
    *,
    use_cache: bool = True,
    now: datetime | None = None,
) -> DiagnosisReport:
    """수집 -> 지표 -> 국면 -> 전략별 평가 -> 리스크 플랜 -> 리포트 조립.

    판정 로직의 유일한 오케스트레이션 지점. CLI와 향후 웹 API가 이 함수를 공유한다.
    """
    bundle = load_ohlcv(ticker, config.data, config.exchanges, use_cache=use_cache, now=now)
    session = resolve_session(ticker, config.exchanges)
    exchange = session.exchange if session is not None else None

    regime, regime_warnings = _resolve_regime(
        ticker, config, use_cache=use_cache, now=now, exchange=exchange
    )
    rs_percentile, rs_warnings = _resolve_rs_percentile(
        ticker, bundle.ohlcv, config, use_cache=use_cache, now=now, exchange=exchange
    )

    ctx = build_context(
        ticker,
        bundle.ohlcv,
        config,
        regime=regime,
        bar_meta=bundle.bar_meta,
        stage=classify_stage(bundle.ohlcv, config.regime),
        rs_percentile=rs_percentile,
        warnings=(*bundle.warnings, *regime_warnings, *rs_warnings),
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


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 데이터 문제는 사용자 메시지로, 그 외 예외는 그대로 올린다."""
    args = build_parser().parse_args(argv)
    config = apply_cli_overrides(
        AppConfig(), equity=args.equity, risk_pct=args.risk_pct
    )

    try:
        strategies = load_strategies(args.strategies, config)
    except UnknownStrategyError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2

    try:
        report = diagnose(args.ticker, config, strategies, use_cache=not args.no_cache)
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
