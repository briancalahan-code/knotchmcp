import sys

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from knotch_mcp.server import mcp, settings, _clay

OPEN_PATHS = {"/health", "/clay/callback"}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        if not settings.mcp_auth_token:
            return await call_next(request)
        provided = request.headers.get("authorization", "")
        token = provided.removeprefix("Bearer ").strip()
        if token != settings.mcp_auth_token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def clay_callback(request: Request) -> JSONResponse:
    data = await request.json()
    print(f"[clay_callback] received: {data}")
    print(f"[clay_callback] pending keys: {list(_clay._pending.keys())}")
    accepted = _clay.receive_callback(data)
    print(f"[clay_callback] accepted: {accepted}")
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
            middleware=[Middleware(BearerAuthMiddleware)],
        )
        app.mount("/", sse_app)

        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
