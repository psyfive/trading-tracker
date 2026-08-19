"""유니버스 기반 RS 백분위 테스트.

두 가지를 잠근다:

1. **시점 정합성** — 날짜 t의 백분위가 t 이하 데이터로만 계산되는지. 하네스의
   look-ahead 감사는 주입 시리즈 안의 미래 참조를 잡지 못하므로 여기가 유일한 방어선이다.
2. **교차단면 성질** — 진짜 백분위가 '다른 종목 대비 수준'을 재는지. Phase 3까지 쓰던
   근사는 '자기 과거 대비 가속도'를 재서 꾸준한 초과성과 종목이 중간 점수를 받았다.
   그 결함이 되살아나지 않게 못 박는다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from config import DEFAULT_CONFIG
from data.universe import (
    load_universe_tickers,
    rs_line_new_high_series,
    rs_percentile_against,
    rs_percentile_frame,
    rs_percentile_series,
    rs_score,
    rs_score_frame,
    small_universe_warning,
    survivorship_warning,
    universe_for,
    universe_path,
)

UNIVERSE = DEFAULT_CONFIG.universe
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def us_closes() -> pd.DataFrame:
    return pd.read_parquet(FIXTURE_DIR / "universe_us_large_closes.parquet")


@pytest.fixture(scope="module")
def us_scores(us_closes) -> pd.DataFrame:
    return rs_score_frame(us_closes, UNIVERSE)


@pytest.fixture(scope="module")
def us_percentiles(us_scores) -> pd.DataFrame:
    return rs_percentile_frame(us_scores, UNIVERSE)


@pytest.fixture(scope="module")
def kospi_closes() -> pd.DataFrame:
    return pd.read_parquet(FIXTURE_DIR / "universe_kospi_closes.parquet")


# ===========================================================================
# 유니버스 목록
# ===========================================================================


def test_universe_lists_exist_and_parse():
    for name in ("us_large", "kospi"):
        tickers = load_universe_tickers(name)
        assert len(tickers) >= UNIVERSE.min_universe_size, name
        assert all(t and not t.startswith("#") for t in tickers)


def test_universe_list_ignores_comments_and_blanks(tmp_path, monkeypatch):
    import data.universe as universe_module

    path = tmp_path / "tiny.txt"
    path.write_text("# 주석\n\nAAA\n  BBB  \n\n# 또 주석\nCCC\n", encoding="utf-8")
    monkeypatch.setattr(universe_module, "UNIVERSE_DIR", tmp_path)
    assert load_universe_tickers("tiny") == ["AAA", "BBB", "CCC"]


def test_missing_universe_names_what_is_available():
    with pytest.raises(FileNotFoundError, match="사용 가능"):
        load_universe_tickers("does_not_exist")


def test_universe_selection_by_exchange():
    assert universe_for("US", UNIVERSE) == "us_large"
    assert universe_for("KRX", UNIVERSE) == "kospi"
    assert universe_for(None, UNIVERSE) == UNIVERSE.default_universe


def test_universe_file_documents_survivorship_bias():
    """편향을 아는 사람만 아는 상태로 두지 않는다 — 목록 파일 자체에 적혀 있어야 한다."""
    text = universe_path("us_large").read_text(encoding="utf-8")
    assert "생존편향" in text


def test_fixture_universes_are_separated_by_market(us_closes, kospi_closes):
    """시장을 섞으면 거래일이 달라 서로 다른 날짜를 비교하게 된다."""
    assert not set(us_closes.columns) & set(kospi_closes.columns)
    assert all(t.endswith(".KS") for t in kospi_closes.columns)


# ===========================================================================
# 시점 정합성
# ===========================================================================


@pytest.mark.parametrize("position", [300, 500, 700])
def test_percentile_matches_a_truncated_recomputation(us_closes, us_percentiles, position):
    """날짜 t의 백분위가 t에서 잘라낸 데이터로 계산한 값과 같아야 한다."""
    truncated = us_closes.iloc[: position + 1]
    as_of = truncated.index[-1]
    recomputed = rs_percentile_frame(rs_score_frame(truncated, UNIVERSE), UNIVERSE)
    for ticker in ("AAPL", "MSFT", "NVDA"):
        expected, actual = us_percentiles.loc[as_of, ticker], recomputed.loc[as_of, ticker]
        assert pd.isna(expected) == pd.isna(actual)
        if pd.notna(expected):
            assert actual == pytest.approx(expected)


def test_appending_future_bars_does_not_change_past_percentiles(us_closes):
    early = rs_percentile_frame(rs_score_frame(us_closes.iloc[:500], UNIVERSE), UNIVERSE)
    full = rs_percentile_frame(rs_score_frame(us_closes, UNIVERSE), UNIVERSE)
    common = early.dropna(how="all").index
    pd.testing.assert_frame_equal(early.loc[common], full.loc[common, early.columns])


def test_rs_line_new_high_is_point_in_time(aapl, us_closes):
    benchmark = pd.read_csv(
        FIXTURE_DIR / "SPY_3y.csv", index_col=0, parse_dates=True
    )["close"].astype(float)
    full = rs_line_new_high_series(aapl["close"], benchmark, UNIVERSE)
    cut = aapl.iloc[:600]
    as_of = cut.index[-1].date()
    truncated = rs_line_new_high_series(
        cut["close"], benchmark.loc[: cut.index[-1]], UNIVERSE
    )
    assert truncated[as_of] == full[as_of]


# ===========================================================================
# 교차단면 성질 — Phase 3 근사가 실패했던 지점
# ===========================================================================


def test_top_performers_receive_top_percentiles(us_closes, us_percentiles):
    """1년 수익률 상위 종목이 RS 상위권이어야 한다.

    Phase 3의 근사(자기 과거 대비 순위)는 이 성질을 만족하지 못했다 —
    꾸준히 오른 종목이 중간 점수를 받았다. 교차단면 순위는 만족해야 한다.
    """
    last = us_percentiles.dropna(how="all").index[-1]
    lookback = UNIVERSE.rs_lookback_days
    returns = (us_closes.loc[last] / us_closes.iloc[-lookback - 1] - 1.0).dropna()

    top = returns.sort_values(ascending=False).head(5).index
    bottom = returns.sort_values().head(5).index
    top_rs = us_percentiles.loc[last, top].mean()
    bottom_rs = us_percentiles.loc[last, bottom].mean()

    assert top_rs > 80.0, f"수익률 상위 종목의 평균 RS가 {top_rs:.0f}에 그친다"
    assert bottom_rs < 20.0
    assert top_rs > bottom_rs


def test_steady_outperformer_holds_a_high_percentile(us_closes, us_scores):
    """근사에서 실패했던 바로 그 케이스.

    일정한 **복리** 속도로 시장을 이기는 종목은 상위권을 계속 유지해야 한다.
    Phase 3의 근사(자기 과거 대비 순위)에서는 이런 종목의 점수가 시간이 갈수록
    50 근처로 수렴했다 — RS 점수가 평탄해서 자기 과거와 비교하면 차이가 없었기 때문이다.
    교차단면 순위는 다른 종목과 비교하므로 그런 감쇠가 없다.

    선형 상승(가격에 매일 같은 금액을 더하는 것)이 아니라 복리를 쓰는 이유:
    선형은 시간이 갈수록 수익률 자체가 체감하므로 순위가 내려가는 것이 정상이다.
    """
    steady = pd.Series(
        [100.0 * (1.0015**i) for i in range(len(us_closes))],  # 연 +45% 복리
        index=us_closes.index,
    )
    ranks = list(rs_percentile_against(steady, us_scores, UNIVERSE).values())
    assert ranks

    series = pd.Series(ranks)
    assert series.median() > 75.0, f"중앙 백분위가 {series.median():.0f}에 그친다"
    assert series.min() > 55.0, f"최저 백분위가 {series.min():.0f}까지 떨어졌다"
    # 안정성이 근사와의 결정적 차이다. 근사는 시간이 갈수록 50으로 수렴했다.
    assert series.std() < 15.0, "꾸준한 초과성과인데 순위가 요동친다"


def test_faster_compounder_outranks_slower_one(us_closes, us_scores):
    """순위가 성과 수준을 반영하는지 — 교차단면 백분위의 정의 그대로."""
    index = us_closes.index
    fast = pd.Series([100.0 * (1.003**i) for i in range(len(index))], index=index)
    slow = pd.Series([100.0 * (1.0005**i) for i in range(len(index))], index=index)
    fast_ranks = rs_percentile_against(fast, us_scores, UNIVERSE)
    slow_ranks = rs_percentile_against(slow, us_scores, UNIVERSE)
    last = max(fast_ranks)
    assert fast_ranks[last] > slow_ranks[last] + 30.0


def test_percentiles_span_the_full_range(us_percentiles):
    """교차단면 순위이므로 매 날짜 0~100이 고르게 채워져야 한다."""
    row = us_percentiles.dropna(how="all").iloc[-1].dropna()
    assert row.min() < 10.0
    assert row.max() > 90.0


def test_percentile_is_bounded(us_percentiles):
    values = us_percentiles.to_numpy()
    finite = values[~pd.isna(values)]
    assert finite.min() >= 0.0
    assert finite.max() <= 100.0


# ===========================================================================
# 표본 부족 / 결측 처리
# ===========================================================================


def test_small_universe_yields_no_percentile(us_scores):
    """구성종목이 기준 미만이면 그럴듯한 숫자를 내지 않는다."""
    tiny = us_scores.iloc[:, :5]
    assert rs_percentile_frame(tiny, UNIVERSE).notna().sum().sum() == 0


def test_min_universe_size_comes_from_config(us_scores):
    lenient = replace(UNIVERSE, min_universe_size=3)
    tiny = us_scores.iloc[:, :5]
    assert rs_percentile_frame(tiny, UNIVERSE).notna().sum().sum() == 0
    assert rs_percentile_frame(tiny, lenient).notna().sum().sum() > 0


def test_short_history_has_no_rs_score(us_closes):
    """lookback 구간 중 하나라도 없으면 점수 자체가 없다. 부분 계산으로 채우지 않는다."""
    scores = rs_score(us_closes["AAPL"], UNIVERSE)
    assert scores.iloc[: UNIVERSE.rs_lookback_days].isna().all()


def test_percentile_series_omits_missing_dates(us_percentiles):
    """값이 없는 날짜는 키 자체가 없다 -> 게이트가 UNAVAILABLE이 된다."""
    series = rs_percentile_series("AAPL", us_percentiles)
    assert len(series) < len(us_percentiles)
    assert all(0.0 <= v <= 100.0 for v in series.values())


def test_percentile_series_for_unknown_ticker_is_empty(us_percentiles):
    assert rs_percentile_series("NOT_IN_UNIVERSE", us_percentiles) == {}


def test_non_member_ticker_is_ranked_against_the_universe(us_closes, us_scores):
    """유니버스 구성종목이 아니어도 '유니버스 대비 순위'는 성립한다."""
    member = rs_percentile_series("AAPL", rs_percentile_frame(us_scores, UNIVERSE))
    against = rs_percentile_against(us_closes["AAPL"], us_scores, UNIVERSE)
    last = max(against)
    # 자기 자신이 분모에 포함되는지 여부만큼의 차이는 있으나 사실상 같아야 한다
    assert abs(against[last] - member[last]) < 2.0


def test_member_and_non_member_use_the_same_percentile_convention(us_closes, us_scores):
    """두 경로의 규약이 다르면 게이트 경계(RS 70/80)에서 소속 여부로 판정이 뒤집힌다.

    규약은 strictly-less 비율이다. 구성종목 경로가 rank(pct=True)를 쓰면 최하위가
    100/n에서 시작해 계통적으로 후해진다 — 그 차이를 여기서 잠근다.
    """
    percentiles = rs_percentile_frame(us_scores, UNIVERSE)
    row = percentiles.dropna(how="all").iloc[-1].dropna()
    universe_size = len(row)

    assert row.min() == pytest.approx(0.0), "최하위는 0이어야 한다 (strictly-less 규약)"
    assert row.max() < 100.0, "자기 자신은 자기보다 낮지 않다"

    # 같은 종목을 두 경로로 재면 '분모에 자기 자신이 있는가'만큼만 차이나야 한다.
    ticker = str(row.idxmax())
    member = rs_percentile_series(ticker, percentiles)
    against = rs_percentile_against(us_closes[ticker], us_scores, UNIVERSE)
    day = max(against)
    assert abs(against[day] - member[day]) <= 100.0 / universe_size


# ===========================================================================
# 경고 — 숫자와 함께 다녀야 한다
# ===========================================================================


def test_survivorship_warning_states_the_bias():
    warning = survivorship_warning("us_large", 115)
    assert "생존편향" in warning.message or "사라진 종목" in warning.message
    assert "us_large" in warning.message
    assert warning.field == "indicators.rs_percentile"


def test_small_universe_warning_reports_resolution():
    warning = small_universe_warning(40, 30)
    assert "해상도" in warning.message


def test_benchmark_selection_by_exchange():
    from data.universe import benchmark_for

    regime = DEFAULT_CONFIG.regime
    assert benchmark_for("AAPL", regime, "US") == "SPY"
    assert benchmark_for("005930.KS", regime, "KRX") == "^KS11"
    assert benchmark_for("7203.T", regime, None) == regime.default_benchmark
