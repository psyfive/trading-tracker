"""pytest 공통 설정.

pyproject.toml의 pythonpath = ["."] 로 루트 임포트가 해결되지만,
IDE나 직접 실행 경로에서도 동작하도록 보강한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
