import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from knotch_mcp.log import get_logger
from knotch_mcp.server import mcp, settings, _clay

logger = get_logger("knotch_mcp.main")

OPEN_PATHS = {"/health", "/clay/callback"}


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in OPEN_PATHS or not settings.mcp_auth_token:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        token = auth.removeprefix("Bearer ").strip()

        if token != settings.mcp_auth_token:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def clay_callback(request: Request) -> JSONResponse:
    data = await request.json()
    logger.info(
        "clay callback received",
        extra={
            "keys": list(data.keys()),
            "has_correlation_id": "correlationId" in data,
            "stored_results": list(_clay._callback_results.keys()),
            "pending_lookups": list(_clay._pending_lookups.keys()),
        },
    )
    accepted = _clay.receive_callback(data)
    logger.info("clay callback %s", "MATCHED" if accepted else "NO MATCH")
    if accepted:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "ignored"}, status_code=404)


def main():
    transport = "sse" if "--http" in sys.argv else "stdio"
    if transport == "sse":
        mcp.settings.port = settings.port

        sse_app = mcp.sse_app()

        app = Starlette(
            routes=[
                Route("/health", health, methods=["GET"]),
                Route("/clay/callback", clay_callback, methods=["POST"]),
            ],
        )
        app.mount("/", sse_app)
        app = BearerAuthMiddleware(app)

        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
