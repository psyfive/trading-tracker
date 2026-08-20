"""전략 이름 -> 인스턴스 팩토리.

## 왜 main.py가 아니라 여기인가

CLAUDE.md 원칙 2: **전략을 추가할 때 코어(`core/`, `render/`, `main.py`) 수정이 필요하면
설계가 틀린 것이다.** 목록이 CLI 안에 있으면 전략 하나 추가할 때마다 코어를 고치게 되고,
백테스트 스크립트·목업 생성기가 각자 자기 목록을 들고 있으면 '어떤 전략이 도는가'가
호출부마다 달라진다. 목록은 `strategies/` 안에 하나만 둔다.

## 팩토리가 AppConfig를 받고 전략에는 자기 슬라이스만 넘긴다

전략은 자기 config만 받는다(`MinerviniStrategy(config.minervini)`). 팩토리가 그 분배를
맡으므로 호출부는 `AppConfig` 하나만 들고 다니면 되고, 전략은 여전히 남의 임계값을
볼 수 없다.

## 더미는 여기 없다

`strategies/dummy.py`의 3종은 하네스 검증용이며 매매 판단용이 아니다. 등록하면
`--strategies all`에 섞여 사용자 화면에 뜬다.
"""

from __future__ import annotations

from collections.abc import Callable

from config import AppConfig
from strategies.base import Strategy
from strategies.minervini import MinerviniStrategy
from strategies.qullamaggie import QullamaggieStrategy
from strategies.weinstein import WeinsteinStrategy

StrategyFactory = Callable[[AppConfig], Strategy]

# 순서가 곧 리포트·화면의 표시 순서다. 판정을 합치지 않으므로 우열이 아니라 나열 순서일 뿐이다.
STRATEGY_FACTORIES: dict[str, StrategyFactory] = {
    "minervini": lambda config: MinerviniStrategy(config.minervini),
    "weinstein": lambda config: WeinsteinStrategy(config.weinstein),
    "qullamaggie": lambda config: QullamaggieStrategy(config.qullamaggie),
}

ALL = "all"


class UnknownStrategyError(ValueError):
    """등록되지 않은 전략 이름. 조용히 무시하면 사용자는 그 전략이 돈 줄 안다."""


def available_strategies() -> list[str]:
    return list(STRATEGY_FACTORIES)


def build_strategies(names: str | list[str], config: AppConfig) -> list[Strategy]:
    """이름 목록으로 전략 인스턴스를 만든다. 'all'이면 등록된 전부.

    이름이 하나라도 틀리면 예외다. 오타를 무시하고 나머지만 돌리면 사용자는 자기가
    요청한 전략의 판정을 못 본 채 '그 전략은 BUY를 안 냈구나'로 읽는다.
    """
    if isinstance(names, str):
        names = available_strategies() if names == ALL else [n.strip() for n in names.split(",")]

    unknown = [name for name in names if name not in STRATEGY_FACTORIES]
    if unknown:
        raise UnknownStrategyError(
            f"등록되지 않은 전략: {', '.join(unknown)} "
            f"(사용 가능: {', '.join(available_strategies())})"
        )
    return [STRATEGY_FACTORIES[name](config) for name in names]
