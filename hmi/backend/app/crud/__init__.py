"""CRUD 레이어.

라우터는 ORM 세션을 직접 만지지 않고 이 패키지만 호출한다. 그래야
'존재하지 않는 대상 → 404' 같은 규칙이 한 곳에만 있고, 서비스 로직이 붙을 때
같은 함수를 재사용할 수 있다.

사용: ``from app import crud`` → ``crud.patrol.create_node(...)``
"""

from app.crud import equipment, events, ids, maps, patrol, robots, system

__all__ = ["equipment", "events", "ids", "maps", "patrol", "robots", "system"]
