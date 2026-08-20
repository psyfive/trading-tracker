"""HTML 렌더러 테스트.

CLI 렌더러와 같은 것을 잠근다 — 판정을 왜곡하지 않는가, 그리고 HTML 고유의 두 가지:

  - **자기완결**: 데이터가 파일 안에 있어야 서버 없이 열린다
  - **이스케이프**: JSON 안의 `</script>`가 태그를 조기 종료시키면 페이지가 깨진다
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.types import DiagnosisReport, WatchlistReport
from render import html_out
from render.html_out import diagnosis_html, watchlist_html

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="module")
def watchlist() -> WatchlistReport:
    return WatchlistReport.model_validate_json(
        (EXAMPLES_DIR / "sample_watchlist.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def buy_report() -> DiagnosisReport:
    return DiagnosisReport.model_validate_json(
        (EXAMPLES_DIR / "sample_buy.json").read_text(encoding="utf-8")
    )


def payload_of(html: str) -> dict:
    """페이지에 박힌 데이터를 다시 꺼낸다."""
    match = re.search(
        r'<script type="application/json" id="payload">(.*?)</script>', html, re.S
    )
    assert match, "데이터 블록이 없다 — 페이지가 자기완결이 아니다"
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_page_is_self_contained(watchlist):
    """서버도 인터넷도 없이 더블클릭으로 열려야 한다 (폰트만 선택적으로 외부)."""
    html = watchlist_html(watchlist)
    data = payload_of(html)

    assert len(data["watchlist"]["entries"]) == len(watchlist.entries)
    assert "fetch(" not in html, "로컬 파일에서 fetch는 막혀 있다"
    assert "<script src=" not in html, "외부 스크립트에 의존하면 오프라인에서 깨진다"


def test_every_ticker_reaches_the_page(watchlist):
    data = payload_of(watchlist_html(watchlist))
    embedded = {e["ticker"] for e in data["watchlist"]["entries"]}
    assert embedded == {e.ticker for e in watchlist.entries}


def test_entries_keep_the_contract_order(watchlist):
    """정렬은 계약이다. 페이지가 다시 정렬하면 화면과 계약이 갈라진다."""
    data = payload_of(watchlist_html(watchlist))
    assert [e["ticker"] for e in data["watchlist"]["entries"]] == [
        e.ticker for e in watchlist.entries
    ]


def test_details_ride_along_when_given(watchlist, buy_report):
    html = watchlist_html(watchlist, {buy_report.ticker: buy_report})
    data = payload_of(html)
    detail = data["details"][buy_report.ticker]

    assert len(detail["strategy_verdicts"]) == len(buy_report.strategy_verdicts)
    assert detail["risk_plans"], "리스크 플랜이 상세에 실려야 한다"


def test_missing_detail_is_not_faked(watchlist):
    """상세가 없는 종목은 '개별 진단하라'고 말한다 — 없는 것을 지어내지 않는다."""
    html = watchlist_html(watchlist)
    assert payload_of(html)["details"] == {}
    assert "python main.py" in html


def test_script_terminator_inside_data_is_escaped(watchlist):
    """JSON 안의 </script>가 그대로 나가면 페이지가 그 지점에서 깨진다."""
    poisoned = watchlist.model_copy(
        update={"universe": "</script><script>alert(1)</script>"}
    )
    html = watchlist_html(poisoned)
    body = html.split('id="payload">', 1)[1].split("</script>", 1)[0]

    assert "</script>" not in body
    assert payload_of(html)["watchlist"]["universe"] == poisoned.universe


def test_three_gate_states_have_distinct_colors():
    """UNAVAILABLE이 FAIL과 같은 색이면 '데이터 없음'이 '조건 미달'로 둔갑한다."""
    style = html_out._STYLE
    for token in ("--pass:", "--fail:", "--unknown:"):
        assert token in style

    statuses = re.findall(r'\.status\[data-s="(\w+)"\]\s*\{([^}]+)\}', style)
    colors = {name: body for name, body in statuses}
    assert colors["PASS"] != colors["FAIL"] != colors["UNAVAILABLE"]
    assert colors["FAIL"] != colors["UNAVAILABLE"]


def test_both_themes_are_defined():
    """뷰어 테마는 세 상태다 — 명시 선택 둘과 '시스템 따름'."""
    style = html_out._STYLE
    assert "@media (prefers-color-scheme: dark)" in style
    assert ':root[data-theme="dark"]' in style
    assert ':root:not([data-theme="light"])' in style
    assert re.search(r"body\s*\{[^}]*background:\s*var\(--ground\)", style), (
        "body 배경을 토큰으로 칠하지 않으면 호스트 테마가 비쳐 보인다"
    )


def test_renderer_does_not_import_judgement_code():
    """CLI 렌더러와 같은 구조적 잠금 — 계약과 직렬화만 알면 된다."""
    imports = [
        line
        for line in Path(html_out.__file__).read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]
    forbidden = [
        line
        for line in imports
        if any(m in line for m in ("config", "strategies", "indicators", "backtest"))
    ]
    assert forbidden == [], f"HTML 렌더러가 판정 쪽을 import 한다: {forbidden}"


def test_diagnosis_page_is_a_one_ticker_watchlist(buy_report):
    """화면을 둘로 나누면 같은 판정이 두 모습으로 보이게 된다."""
    data = payload_of(diagnosis_html(buy_report))
    entries = data["watchlist"]["entries"]

    assert len(entries) == 1
    assert entries[0]["ticker"] == buy_report.ticker
    assert buy_report.ticker in data["details"]
    assert entries[0]["buy_strategies"] == buy_report.consensus.buy_strategies


def test_warnings_travel_with_the_numbers(watchlist):
    """생존편향 경고가 화면에서 빠지면 숫자만 남는다."""
    data = payload_of(watchlist_html(watchlist))
    assert data["watchlist"]["warnings"], "경고가 페이지에 실려야 한다"
    assert "warning" in html_out._STYLE
