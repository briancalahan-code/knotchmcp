import sys
from knotch_mcp.server import mcp, settings


def main():
    transport = "sse" if "--http" in sys.argv else "stdio"
    if transport == "sse":
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = settings.port
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
