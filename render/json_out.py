"""프론트엔드용 직렬화 — 유일한 진입점.

별칭(alias)을 쓰는 필드가 하나도 없으므로 by_alias 같은 특수 인자가 필요 없다.
그래도 진입점을 하나로 두는 이유는 datetime/date 포맷과 enum 표현이
CLI·API·목업 파일에서 갈라지지 않게 하기 위함이다.

이 모듈은 판정하지 않는다. 임계값 비교 코드가 등장하면 로직이 샌 것이다.
model_dump 계열을 이 모듈 밖에서 호출하지 말 것 (계약 테스트만 예외 — CLAUDE.md).
"""

from __future__ import annotations

import json
from typing import Any

from core.types import DiagnosisReport


def to_payload(report: DiagnosisReport) -> dict[str, Any]:
    """JSON 직렬화 가능한 dict (mode='json').

    date -> ISO 문자열, datetime -> ISO 문자열(UTC 오프셋 포함), enum -> 값 문자열.
    """
    return report.model_dump(mode="json")


def to_json(report: DiagnosisReport, *, indent: int | None = 2) -> str:
    """JSON 문자열. 한글 경고 메시지가 있으므로 ensure_ascii=False."""
    return json.dumps(to_payload(report), ensure_ascii=False, indent=indent)


def json_schema() -> dict[str, Any]:
    """프론트엔드에 넘길 JSON Schema. 타입 생성기(quicktype 등)의 입력."""
    return DiagnosisReport.model_json_schema()
