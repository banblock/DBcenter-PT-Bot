"""권한 정책 — 로그인 없는 역할 기반(advisory) 모델.

배경
----
본 시스템은 폐쇄망 관제실 단말에서만 쓰이며 로그인 화면을 두지 않기로 했다.
따라서 여기서 하는 일은 *인증(authentication)* 이 아니라 *오조작 방지* 다.

* 1차 방어 = 프론트. 역할에 없는 버튼은 아예 렌더하지 않거나 disabled.
* 2차 방어 = 백엔드. 프론트가 보내는 ``X-Role`` 헤더를 신뢰하고 위험 명령을 막는다.

``X-Role`` 은 위조 가능하므로 **보안 경계가 아니다**. 망 분리·물리 접근 통제가
실제 보안 경계이고, 이 계층은 "관리자만 누르는 버튼을 뷰어가 실수로 호출"하는
사고를 막는 용도다. 이 전제가 바뀌면(외부망 노출 등) 반드시 실제 인증을 붙여야 한다.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from fastapi import Depends, Header

from app.errors import ApiError, E


class Role(str, Enum):
    VIEWER = "VIEWER"  # 조회만
    OPERATOR = "OPERATOR"  # 순찰·급파·로봇 제어 등 일상 운영
    ADMIN = "ADMIN"  # 진압 승인, 마스터 데이터/룰/설정 변경


class Permission(str, Enum):
    """화면·엔드포인트가 요구하는 능력 단위. 프론트 `auth/permissions.ts` 와 동일."""

    VIEW = "VIEW"                        # 대시보드/이력/통계 조회
    ROBOT_CONTROL = "ROBOT_CONTROL"      # 순찰 시작·정지·goto·도킹
    EMERGENCY_STOP = "EMERGENCY_STOP"    # 긴급정지 (뷰어도 눌러야 하는 안전 장치)
    EVENT_HANDLE = "EVENT_HANDLE"        # ACK·급파·재검증·종결
    EVENT_REVIEW = "EVENT_REVIEW"        # 오탐 마킹 등 사람 검수
    SUPPRESSION_REQUEST = "SUPPRESSION_REQUEST"  # 진압 요청
    SUPPRESSION_APPROVE = "SUPPRESSION_APPROVE"  # 진압 승인/중단
    MASTER_EDIT = "MASTER_EDIT"          # 맵·노드·경로·설비·룰 CRUD
    CONFIG_EDIT = "CONFIG_EDIT"          # 진압 모드 등 시스템 설정


#: 역할 → 허용 권한. 프론트 ROLE_PERMISSIONS 와 반드시 동일하게 유지할 것.
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW,
        # 긴급정지는 누구든 눌러야 한다 — 안전 기능은 권한으로 막지 않는다.
        Permission.EMERGENCY_STOP,
    },
    Role.OPERATOR: {
        Permission.VIEW,
        Permission.EMERGENCY_STOP,
        Permission.ROBOT_CONTROL,
        Permission.EVENT_HANDLE,
        Permission.EVENT_REVIEW,
        Permission.SUPPRESSION_REQUEST,
    },
    Role.ADMIN: set(Permission),  # 전체
}

DEFAULT_ROLE = Role.OPERATOR


class Actor:
    """요청 주체. 로그인이 없으므로 '이름표' 수준의 정보만 담는다."""

    __slots__ = ("role", "name")

    def __init__(self, role: Role, name: str) -> None:
        self.role = role
        self.name = name

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]

    def __repr__(self) -> str:  # pragma: no cover - 로깅용
        return f"Actor({self.role.value}, {self.name})"


def get_actor(
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
    x_operator: Annotated[str | None, Header(alias="X-Operator")] = None,
) -> Actor:
    """헤더에서 역할·조작자를 읽는다. 헤더가 없으면 OPERATOR/unknown 으로 본다."""
    if x_role is None:
        role = DEFAULT_ROLE
    else:
        try:
            role = Role(x_role.strip().upper())
        except ValueError as exc:
            raise ApiError(E.UNKNOWN_ROLE, f"알 수 없는 역할입니다: {x_role}") from exc
    return Actor(role, (x_operator or "unknown").strip() or "unknown")


ActorDep = Annotated[Actor, Depends(get_actor)]


def require(permission: Permission):
    """엔드포인트에 붙이는 권한 가드.

    사용: ``@router.post(..., dependencies=[Depends(require(Permission.MASTER_EDIT))])``
    """

    def _guard(actor: ActorDep) -> Actor:
        if not actor.can(permission):
            raise ApiError(
                E.FORBIDDEN,
                f"{actor.role.value} 역할에는 {permission.value} 권한이 없습니다",
                data={"required": permission.value, "role": actor.role.value},
            )
        return actor

    return _guard
