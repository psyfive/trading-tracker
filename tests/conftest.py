"""pytest 공통 설정.

가장 중요한 장치는 `_block_network`다. **기본 실행에서 yfinance 호출을 물리적으로 차단한다.**
테스트가 네트워크를 타면 인터넷 상태나 API 변경으로 깨지고, 그러면 실패 원인이
지표 버그인지 외부 문제인지 구분할 수 없다. 네트워크가 필요한 테스트는
@pytest.mark.network를 붙이고 `--run-network`로만 실행한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="네트워크를 실제로 타는 테스트(@pytest.mark.network)를 실행한다",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "network: 실제 yfinance 호출. 기본 실행에서 제외된다")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="네트워크 테스트 — 실행하려면 --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """network 마커가 없는 테스트에서 yfinance 호출을 예외로 만든다.

    실수로 네트워크를 타는 테스트가 추가되면 조용히 느려지는 게 아니라 즉시 실패한다.
    """
    if "network" in request.keywords:
        return

    import yfinance

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "테스트에서 yfinance를 호출했다. 고정 픽스처를 쓰거나 "
            "@pytest.mark.network를 붙일 것"
        )

    monkeypatch.setattr(yfinance, "download", _forbidden)


def fixture_files() -> list[Path]:
    """tests/fixtures/의 3년치 CSV 목록."""
    return sorted(FIXTURE_DIR.glob("*_3y.csv"))


def load_fixture(name: str) -> pd.DataFrame:
    """고정 CSV를 fetcher가 내놓는 것과 동일한 형태로 읽는다."""
    df = pd.read_csv(FIXTURE_DIR / name, index_col=0, parse_dates=True)
    df.index.name = "date"
    return df.astype(float)


@pytest.fixture
def aapl() -> pd.DataFrame:
    """미국 거래소 고정 픽스처 (3년치 일봉)."""
    return load_fixture("AAPL_3y.csv")


@pytest.fixture
def samsung() -> pd.DataFrame:
    """KRX 고정 픽스처 (3년치 일봉)."""
    return load_fixture("005930_KS_3y.csv")
