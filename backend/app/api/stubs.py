from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.api_route(
    "/api/v1/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def api_not_implemented(full_path: str, request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": "not_found",
                "message": "Эндпоинт не найден",
                "path": f"/api/v1/{full_path}",
            }
        },
        status_code=404,
    )


@router.api_route(
    "/agent/v1/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def agent_not_implemented(full_path: str, request: Request) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "not_found", "message": "Agent API not implemented yet"}},
        status_code=404,
    )
