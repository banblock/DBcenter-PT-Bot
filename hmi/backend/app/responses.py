"""공통 응답 래퍼.

API 명세서 상단 규격: ``{"result": "SUCCESS"|"FAIL", "data": {...}, "message": "..."}``

라우터는 dict/모델을 그대로 return 하고, 감싸는 일은 ``ok()`` 혹은
``EnvelopeRoute`` 가 담당한다. 라우터마다 래핑 코드를 반복하지 않기 위함이다.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from app.errors import ErrorCatalog


def ok(data: Any = None, message: str | None = None, status_code: int = 200) -> JSONResponse:
    """성공 봉투. `data` 는 dict/list/None 모두 허용한다."""
    body: dict[str, Any] = {"result": "SUCCESS", "data": data}
    if message is not None:
        body["message"] = message
    return JSONResponse(status_code=status_code, content=jsonable(body))


def fail(
    code: str,
    message: str,
    status_code: int = 400,
    data: Any = None,
) -> JSONResponse:
    """실패 봉투. `code` 는 ErrorCatalog 의 코드 문자열."""
    return JSONResponse(
        status_code=status_code,
        content=jsonable({"result": "FAIL", "code": code, "message": message, "data": data}),
    )


def fail_from(catalog: ErrorCatalog, message: str | None = None, data: Any = None) -> JSONResponse:
    return fail(catalog.code, message or catalog.message, catalog.status, data)


def paginated(items: list[Any], count: int, page: int, size: int) -> JSONResponse:
    """목록 응답 — 명세서 5-9 처럼 count/page/size 를 봉투 최상위에 둔다."""
    return JSONResponse(
        status_code=200,
        content=jsonable(
            {"result": "SUCCESS", "count": count, "page": page, "size": size, "data": items}
        ),
    )


def jsonable(value: Any) -> Any:
    """datetime/Decimal/Enum 등을 JSON 안전 타입으로 낮춘다."""
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum

    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    return value
