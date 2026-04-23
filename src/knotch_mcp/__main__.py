import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from knotch_mcp.server import mcp, settings, _clay


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def clay_callback(request: Request) -> JSONResponse:
    data = await request.json()
    accepted = _clay.receive_callback(data)
    if accepted:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "ignored"}, status_code=404)


def main():
    transport = "sse" if "--http" in sys.argv else "stdio"
    if transport == "sse":
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = settings.port

        sse_app = mcp.sse_app()

        app = Starlette(
            routes=[
                Route("/health", health, methods=["GET"]),
                Route("/clay/callback", clay_callback, methods=["POST"]),
            ],
        )
        app.mount("/", sse_app)

        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=settings.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
