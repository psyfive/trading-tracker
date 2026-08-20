"""유니버스 기반 상대강도(RS) 백분위.

RS 백분위의 정의는 **'같은 시점에 유니버스 내 다른 종목들과 비교한 순위'**다.
Phase 3.5에서 유니버스를 갖추면서 이 정의대로 계산한다.

## 산출 절차

1. 유니버스 종목들의 종가 행렬 (날짜 x 티커)
2. 각 종목·각 날짜의 RS 점수 = 기간별 수익률 가중합 (IBD 방식, 최근 분기에 큰 가중치)
3. **날짜별 교차단면 순위** = 그 날짜의 점수들을 종목 간에 순위화 -> 0~100

3번이 Phase 3까지 쓰던 근사와 결정적으로 다르다. 근사는 자기 과거와 비교해
'상대강도의 가속도'를 쟀지만, 이것은 다른 종목과 비교해 '상대강도의 수준'을 잰다.
꾸준히 시장을 이기는 종목이 이제 제대로 상위권을 받는다.
(그 근사 함수는 Phase 3.5에서 제거했다 — 두 가지 RS 개념을 남겨 두면 잘못된 쪽을
쓰는 사고가 난다. 유니버스가 없으면 RS는 None이고 게이트는 UNAVAILABLE이다.)

## 시점 정합성

날짜 t의 백분위는 t 시점 각 종목의 점수만으로 계산되고, 각 점수는 t 이하 종가만
쓴다. 따라서 정의상 후방 참조뿐이다. 백테스트 하네스는 이 시리즈를 주입받으며,
하네스의 look-ahead 감사는 시리즈 안의 미래 참조를 잡지 못하므로 시점 정합성은
이 모듈의 책임이다. `tests/test_universe.py`가 이를 잠근다.

## 생존편향 — 제거하지 못했다

유니버스 목록(`data/universes/*.txt`)은 **현재 상장 중인** 종목만 담는다.
과거 시점의 실제 구성종목이 아니므로, 그때 존재했다가 사라진 종목이 분모에서 빠진다.
살아남은 종목끼리만 순위를 매기면 백분위가 낙관 쪽으로 치우친다.
없애려면 시점별 구성종목 데이터베이스가 필요하고 그것은 이 프로젝트 범위 밖이다.
리포트는 이 사실을 항상 함께 출력한다 (`survivorship_warning()`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from config import DataConfig, ExchangeConfig, RegimeConfig, UniverseConfig
from core.types import DiagnosticWarning, Severity, WarningCode

UNIVERSE_DIR = Path(__file__).resolve().parent / "universes"


class UniverseDataError(RuntimeError):
    """유니버스 종가를 하나도 확보하지 못했다.

    조용히 빈 행렬을 돌려주면 RS가 전부 None이 되고, 사용자는 '모든 전략이 게이트에서
    탈락했다'는 화면만 보게 된다 — 원인이 데이터인지 종목인지 구분할 수 없다.
    """


# ---------------------------------------------------------------------------
# 유니버스 목록
# ---------------------------------------------------------------------------


def universe_path(name: str) -> Path:
    return UNIVERSE_DIR / f"{name}.txt"


def load_universe_tickers(name: str) -> list[str]:
    """유니버스 구성 종목 목록. '#' 주석과 빈 줄은 무시한다."""
    path = universe_path(name)
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in UNIVERSE_DIR.glob("*.txt")))
        raise FileNotFoundError(f"유니버스 목록이 없다: {path.name} (사용 가능: {available})")

    return [
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def benchmark_for(ticker: str, regime_config: RegimeConfig, exchange: str | None) -> str:
    """종목이 속한 시장의 벤치마크 지수 티커."""
    return dict(regime_config.benchmark_by_exchange).get(
        exchange or "", regime_config.default_benchmark
    )


def universe_for(exchange: str | None, config: UniverseConfig) -> str:
    """거래소에 대응하는 유니버스 이름."""
    return dict(config.universe_by_exchange).get(exchange or "", config.default_universe)


# ---------------------------------------------------------------------------
# RS 점수 (종목별 시계열)
# ---------------------------------------------------------------------------


def rs_score(closes: pd.Series, config: UniverseConfig) -> pd.Series:
    """한 종목의 RS 원점수 시계열. 기간별 수익률의 가중합.

    가중치는 config.rs_weights (기본 0.4/0.2/0.2/0.2)로 최근 구간에 더 큰 값을 준다.
    구간은 rs_lookback_days를 가중치 개수로 등분한다 (252일 -> 63/126/189/252).

    한 구간이라도 계산 불가면 그 날짜는 NaN이다. 일부 구간만으로 점수를 만들면
    상장 기간이 짧은 종목이 유리해지는 역전이 생긴다.
    """
    periods = len(config.rs_weights)
    step = max(1, config.rs_lookback_days // periods)
    total = pd.Series(0.0, index=closes.index)
    usable = pd.Series(True, index=closes.index)

    for i, weight in enumerate(config.rs_weights, start=1):
        past = closes.shift(step * i)
        change = (closes - past) / past * 100.0
        usable &= change.notna()
        total = total + weight * change.fillna(0.0)

    return total.where(usable)


def rs_score_frame(closes: pd.DataFrame, config: UniverseConfig) -> pd.DataFrame:
    """유니버스 전체의 RS 원점수. 반환: 날짜 x 티커."""
    return closes.apply(lambda column: rs_score(column, config))


# ---------------------------------------------------------------------------
# 종가 수집 — 진단 파이프라인의 선행 조건
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseCloses:
    """유니버스 종가 행렬(날짜 x 티커) + 수집 실패 목록.

    missing이 별도 필드인 이유는 `PanelResult.skipped_tickers`와 같다 — **분모가
    줄어든 사실이 보이지 않으면 '115종목 대비 순위'라는 말이 거짓이 된다.**
    상장폐지·티커 변경으로 목록과 현실이 어긋나는 것은 정상이고, 조용히 넘어가는 것이
    문제다.
    """

    name: str
    closes: pd.DataFrame
    missing: tuple[str, ...]
    from_cache: bool

    @property
    def size(self) -> int:
        return self.closes.shape[1]


def _universe_cache_key(name: str) -> str:
    """유니버스 종가 캐시의 의사(pseudo) 티커 키.

    캐시 무효화 정책(수집 파라미터 변경 / 날짜 경과 / 장중 저장분)을 `data/fetcher.py`
    한 곳에 두기 위해 그쪽 캐시 함수를 그대로 재사용한다. 정책을 여기 복제하면
    '오전에 받은 미완성 봉이 종일 고정되는' 버그가 유니버스 경로에만 되살아난다.
    티커에 쓸 수 없는 문자로 시작해 실제 종목과 충돌하지 않는다.
    """
    return f"__universe_{name}"


def load_universe_closes(
    name: str,
    data_config: DataConfig,
    exchanges: ExchangeConfig,
    *,
    use_cache: bool = True,
    now: datetime | None = None,
) -> UniverseCloses:
    """유니버스 구성종목의 종가 행렬을 확보한다. 캐시 -> (미스 시) 종목별 수집.

    RS 백분위는 '같은 시점 다른 종목들과 비교한 순위'이므로 **유니버스 전체의 종가가
    있어야 한 종목의 진단이 가능하다.** 이 함수가 없으면 rs_percentile이 None이 되고,
    세 전략 모두 RS 게이트가 UNAVAILABLE -> AND 게이트 차단 -> 전 종목이
    REJECTED_BY_GATE가 된다.

    수집 실패한 종목은 예외로 올리지 않고 missing에 모은다 — 티커 하나가 상장폐지됐다고
    진단 전체가 죽으면 안 되지만, 몇 개가 빠졌는지는 반드시 보여야 한다.
    """
    from data.fetcher import DataError, load_ohlcv, read_cache, write_cache

    now = now or datetime.now(UTC)
    tickers = load_universe_tickers(name)
    key = _universe_cache_key(name)
    today_local = now.date()

    if use_cache:
        cached = read_cache(
            key, data_config, today_local=today_local, session_complete_now=False
        )
        if cached is not None:
            return UniverseCloses(name, cached, (), from_cache=True)

    columns: dict[str, pd.Series] = {}
    missing: list[str] = []
    for ticker in tickers:
        try:
            bundle = load_ohlcv(ticker, data_config, exchanges, use_cache=use_cache, now=now)
        except DataError:
            missing.append(ticker)
            continue
        columns[ticker.upper()] = bundle.ohlcv["close"]

    if not columns:
        raise UniverseDataError(
            f"'{name}' 유니버스에서 종가를 하나도 확보하지 못했다 "
            f"({len(tickers)}종목 전부 실패)"
        )

    closes = pd.DataFrame(columns).sort_index()
    closes.index.name = "date"

    if use_cache:
        write_cache(key, closes, data_config, today_local=today_local, bar_complete=True)

    return UniverseCloses(name, closes, tuple(missing), from_cache=False)


def missing_members_warning(name: str, missing: tuple[str, ...], size: int) -> DiagnosticWarning:
    """수집하지 못한 구성종목이 있다는 사실. 분모가 줄었다는 뜻이다."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.WARN,
        message=(
            f"'{name}' 유니버스에서 {len(missing)}종목의 종가를 받지 못해 {size}종목으로 "
            f"백분위를 계산했다 (누락: {', '.join(missing[:5])}"
            f"{' 외 ' + str(len(missing) - 5) + '종목' if len(missing) > 5 else ''}). "
            "분모가 줄어든 만큼 순위의 의미도 달라진다"
        ),
        field="indicators.rs_percentile",
    )


# ---------------------------------------------------------------------------
# 교차단면 백분위
# ---------------------------------------------------------------------------


def rs_percentile_frame(scores: pd.DataFrame, config: UniverseConfig) -> pd.DataFrame:
    """날짜별 교차단면 순위 -> 0~100.

    규약은 **strictly-less 비율**이다: 백분위 = (자기보다 점수가 낮은 종목 수) / n * 100.
    따라서 최하위는 0이고 값의 범위는 [0, 100)이다.

    `rank(pct=True)`를 쓰지 않는 이유가 여기 있다. 그것은 자기 자신을 포함해 순위를
    매기므로 최하위가 0이 아니라 100/n이 되고, 유니버스 **밖** 종목을 재는
    `rs_percentile_against()`(strictly-less)와 규약이 어긋난다. n=116이면 0.9p,
    KOSPI(50)면 2p의 계통 차이라, RS 70/80 같은 게이트 경계에 걸린 종목의 판정이
    '유니버스 소속 여부'로 뒤집힐 수 있다. 두 경로가 같은 정의를 쓰게 맞춘다.
    (동점은 낮은 쪽으로 본다 — method="min"이 동점자에게 같은 최소 순위를 준다.)

    그 날짜에 점수가 있는 종목이 min_universe_size 미만이면 전부 NaN이다.
    표본이 적으면 백분위 해상도가 떨어지는데, 그것을 그럴듯한 숫자로 내보내면
    게이트 임계값(>= 70)의 의미가 조용히 달라진다.
    """
    available = scores.notna().sum(axis=1)
    # (최소순위 - 1) / n = 자기보다 엄격히 낮은 종목의 비율.
    ranked = (scores.rank(axis=1, method="min") - 1.0).div(available, axis=0) * 100.0
    return ranked.where(available >= config.min_universe_size)


def rs_percentile_series(ticker: str, percentiles: pd.DataFrame) -> dict[date, float]:
    """유니버스 구성종목의 백분위 시계열. 값이 없는 날짜는 키 자체가 없다."""
    if ticker not in percentiles.columns:
        return {}
    return {
        timestamp.date(): float(value)
        for timestamp, value in percentiles[ticker].items()
        if pd.notna(value)
    }


def rs_percentile_against(
    closes: pd.Series, universe_scores: pd.DataFrame, config: UniverseConfig
) -> dict[date, float]:
    """유니버스에 **속하지 않은** 종목의 백분위.

    종목의 RS 점수를 구한 뒤, 각 날짜의 유니버스 점수 분포에서 그 점수가 차지하는
    위치를 찾는다. 구성종목이 아니어도 '유니버스 대비 순위'라는 정의는 그대로
    성립한다 — 자기 과거와 비교하는 것이 아니라 다른 종목들과 비교하기 때문이다.

    규약은 `rs_percentile_frame()`과 같은 strictly-less 비율이다. 분모에 자기 자신이
    없다는 차이(n vs n+1)는 남지만, 그것은 소속 여부가 만드는 불가피한 차이이고
    계통 편차(항상 +100/n)는 아니다.
    """
    scores = rs_score(closes, config).reindex(universe_scores.index)
    available = universe_scores.notna().sum(axis=1)

    out: dict[date, float] = {}
    for timestamp, score in scores.items():
        if pd.isna(score) or available.get(timestamp, 0) < config.min_universe_size:
            continue
        row = universe_scores.loc[timestamp].dropna()
        out[timestamp.date()] = float((row < score).sum()) / len(row) * 100.0
    return out


def rs_line_new_high_series(
    stock_close: pd.Series, benchmark_close: pd.Series, config: UniverseConfig
) -> dict[date, bool]:
    """RS 라인(종목/벤치마크)이 lookback 구간 신고가인지.

    백분위와 달리 유니버스가 필요 없다 — 벤치마크 하나와의 비율이면 정의가 완결된다.

    **아직 어디에도 연결되지 않았다.** 진단 파이프라인도 전략 3종도 이 함수를 부르지
    않으므로 `IndicatorSnapshot.rs_line_new_high`는 항상 None이다. RS 라인 신고가를
    실제 판정에 쓰는 방법론은 CANSLIM이며, 연결은 그 Phase의 몫이다. 그전까지 이
    함수는 시점 정합성 테스트(tests/test_universe.py)로만 유지된다 — 죽은 코드로
    두는 것이 아니라 '연결 시점이 정해진 미연결 코드'라는 뜻이다.
    """
    aligned = pd.concat([stock_close, benchmark_close], axis=1, join="inner")
    aligned.columns = ["stock", "benchmark"]
    line = aligned["stock"] / aligned["benchmark"]
    peak = line.rolling(
        window=config.rs_lookback_days, min_periods=config.rs_lookback_days
    ).max()
    return {
        timestamp.date(): bool(value >= high)
        for timestamp, value, high in zip(line.index, line, peak, strict=True)
        if pd.notna(high)
    }


# ---------------------------------------------------------------------------
# 경고 — 숫자와 항상 함께 다녀야 한다
# ---------------------------------------------------------------------------


def survivorship_warning(universe_name: str, size: int) -> DiagnosticWarning:
    """유니버스가 생존편향을 갖는다는 사실."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.INFO,
        message=(
            f"RS 백분위가 '{universe_name}' 유니버스 {size}종목 대비 순위다. "
            "이 목록은 현재 상장 중인 종목만 담고 있어 과거 시점의 실제 구성종목이 아니다 — "
            "그때 존재했다 사라진 종목이 분모에서 빠지므로 백분위가 낙관 쪽으로 치우친다. "
            "시점별 구성종목 데이터가 없는 한 이 편향은 제거되지 않는다"
        ),
        field="indicators.rs_percentile",
    )


def small_universe_warning(size: int, minimum: int) -> DiagnosticWarning:
    """유니버스가 작아 백분위 해상도가 낮다는 경고."""
    return DiagnosticWarning(
        code=WarningCode.RS_UNIVERSE_MISSING,
        severity=Severity.WARN,
        message=(
            f"유니버스가 {size}종목으로 기준 {minimum}종목에 가깝다. 백분위 해상도가 "
            f"약 {100.0 / max(size, 1):.1f}p 단위이므로 임계값 부근의 판정이 거칠어진다"
        ),
        field="indicators.rs_percentile",
    )
