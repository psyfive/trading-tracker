"""미너비니 전략 테스트.

핵심 검증 대상은 점수가 아니라 **게이트 우선 순서**다:
  - 추세 템플릿 미달 종목은 REJECTED_BY_GATE이고 score가 None이어야 한다.
  - sma200이 None인 신규 상장주는 UNAVAILABLE이어야 한다. FAIL이 아니다.

아직 미구현이므로 전부 skip 상태다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="strategies/minervini.py 미구현 — 구현 단계에서 활성화")


def test_downtrend_stock_is_rejected_by_gate():
    """200일선 아래 종목은 게이트에서 탈락하고 채점되지 않는다."""
    raise NotImplementedError


def test_rejected_stock_has_no_score():
    """게이트 탈락 시 score is None. 0.0이 아니다."""
    raise NotImplementedError


def test_build_score_not_called_when_gate_fails():
    """StrategyBase.evaluate()가 build_score를 호출조차 하지 않아야 한다."""
    raise NotImplementedError


def test_short_history_yields_unavailable_not_fail():
    """상장 6개월 종목의 sma200 조건은 UNAVAILABLE이어야 한다."""
    raise NotImplementedError


def test_incomplete_bar_makes_volume_checks_unavailable():
    """미완성 봉이면 거래량 조건을 FAIL이 아니라 UNAVAILABLE로."""
    raise NotImplementedError


def test_thresholds_come_from_config():
    """MinerviniConfig를 바꾸면 판정이 바뀌어야 한다 (하드코딩 검출)."""
    raise NotImplementedError


def test_gate_check_ids_are_stable():
    """프론트엔드가 id로 키잉하므로 id 집합이 고정되어야 한다."""
    raise NotImplementedError
