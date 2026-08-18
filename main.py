"""CLI 엔트리포인트.

python main.py AAPL
python main.py AAPL --json
python main.py AAPL --equity 100000 --risk-pct 1.0
"""

from __future__ import annotations

import argparse

from config import AppConfig
from core.types import DiagnosisReport
from strategies.base import Strategy


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="trading-tracker",
        description="티커를 여러 추세추종 방법론으로 각각 독립 진단한다.",
    )
    parser.add_argument("ticker", help="진단할 티커. 예: AAPL")
    parser.add_argument("--json", action="store_true", help="rich 대신 JSON 출력")
    parser.add_argument("--equity", type=float, default=None, help="계좌 평가금액 (포지션 사이징용)")
    parser.add_argument("--risk-pct", type=float, default=None, help="트레이드당 감수 리스크 %%")
    parser.add_argument("--no-cache", action="store_true", help="parquet 캐시 무시")
    parser.add_argument(
        "--strategies",
        default="all",
        help="쉼표 구분 전략 목록. 예: minervini,qullamaggie",
    )
    return parser


def load_strategies(names: str, config: AppConfig) -> list[Strategy]:
    """이름으로 전략 플러그인을 조립한다. 코어는 전략 목록을 하드코딩하지 않는다."""
    raise NotImplementedError


def diagnose(ticker: str, config: AppConfig, strategies: list[Strategy]) -> DiagnosisReport:
    """수집 -> 지표 -> 국면 -> 전략별 평가 -> 리스크 플랜 -> 리포트 조립.

    판정 로직의 유일한 오케스트레이션 지점. CLI와 향후 웹 API가 이 함수를 공유한다.
    """
    raise NotImplementedError


def main() -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
