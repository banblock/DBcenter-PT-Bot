"""순찰 노드 점유 락 — 다중 AMR 경로 중복 방지 (B-25 · B-27 · B-28).

설계 메모
--------
노드 점유의 **단일 진실 원천은 활성 미션의 담당 노드 목록**(``Mission.node_order_json``)이다.
별도의 락 테이블을 두면 미션 상태와 락 상태가 어긋날 위험이 있다 — 예컨대 미션은 취소됐는데
락 행이 남아 노드를 영원히 물고 있는 '유령 점유'. 그래서 락을 저장하지 않고 활성 미션에서
그때그때 점유 집합을 **유도**한다. 미션이 끝나면(취소/완료/실패) 그 노드는 자동으로 풀린다
— 별도의 release 호출이 필요 없다. 이 설계는 프로젝트 전반의 "미션이 진실"이라는 원칙과 같다.

"활성"의 정의 = 노드를 실제로 물고 있는 상태 = ``RUNNING`` · ``PENDING`` · ``PREEMPTED``.
(``PREEMPTED`` 은 급파로 잠시 멈췄을 뿐, 중단 지점부터 복귀할 순찰 노드는 그대로 물고 있다.)

경로 겹침 사전 검사(B-28)는 ``claim()`` 하나로 처리한다: 요청한 노드 순서 중 다른 로봇이
이미 점유 중인 노드를 골라내(conflicts) 배분 대상에서 뺀다(granted). 데드락(B-27)은
'같은 노드에 두 로봇이 동시에 배정되는 것'을 원천 차단하는 방식으로 예방한다 — 통로
세그먼트 단위의 실시간 데드락 해소는 Nav2 코스트맵이 필요해 실기 연동 시 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.enums import MissionStatus

#: 노드를 점유한 것으로 보는 미션 상태.
HOLDING_STATUSES: tuple[str, ...] = (
    MissionStatus.RUNNING.value,
    MissionStatus.PENDING.value,
    MissionStatus.PREEMPTED.value,
)


def held_nodes(db: Session, *, exclude_robot_ids: Iterable[str] = ()) -> dict[str, str]:
    """지금 점유 중인 ``{node_id: 점유 로봇 robot_id}`` 매핑.

    ``exclude_robot_ids`` 의 로봇이 물고 있는 노드는 결과에서 뺀다 (자기 재점유 허용).
    한 노드가 여러 미션에 잡혀 있으면(정상적으로는 없어야 함) 먼저 발견한 점유자를 남긴다.
    """
    exclude = set(exclude_robot_ids)
    stmt = select(models.Mission).where(models.Mission.status.in_(HOLDING_STATUSES))
    held: dict[str, str] = {}
    for mission in db.execute(stmt).scalars():
        if mission.robot_id is None or mission.robot_id in exclude:
            continue
        for node_id in mission.node_order_json or []:
            held.setdefault(node_id, mission.robot_id)
    return held


@dataclass(frozen=True)
class Conflict:
    """점유 충돌 1건 — 노드와 그 노드를 물고 있는 로봇."""

    node_id: str
    holder: str

    def to_dict(self) -> dict[str, str]:
        return {"node_id": self.node_id, "holder": self.holder}


@dataclass
class ClaimResult:
    granted: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)

    def conflicts_as_dicts(self) -> list[dict[str, str]]:
        return [c.to_dict() for c in self.conflicts]


def claim(
    db: Session,
    robot_ids: Iterable[str],
    node_order: Iterable[str],
    *,
    exclude_robot_ids: Iterable[str] = (),
) -> ClaimResult:
    """``node_order`` 중 다른 로봇이 점유하지 않은 노드만 ``granted`` 로 통과시킨다.

    배분 대상 로봇(``robot_ids``)이 이미 물고 있던 노드는 충돌로 보지 않는다 — 자기
    미션을 이어받는 재점유는 허용한다. ``node_order`` 의 순서(우선순위 정렬 결과 등)는
    ``granted`` 에 그대로 보존한다.
    """
    exclude = set(exclude_robot_ids) | set(robot_ids)
    held = held_nodes(db, exclude_robot_ids=exclude)
    result = ClaimResult()
    for node_id in node_order:
        holder = held.get(node_id)
        if holder is None:
            result.granted.append(node_id)
        else:
            result.conflicts.append(Conflict(node_id=node_id, holder=holder))
    return result
