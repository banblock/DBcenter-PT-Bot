"""도메인 서비스 — 라우터보다 아래, CRUD 보다 위 계층.

여러 테이블에 걸친 판단 로직(대조 판정, 우선순위 산정 등)이 여기 산다.
라우터는 HTTP 관심사만, CRUD 는 저장 관심사만 다루도록 하기 위한 분리다.
"""

from app.services import align_engine, node_lock, priority, suppression

__all__ = ["align_engine", "node_lock", "priority", "suppression"]
