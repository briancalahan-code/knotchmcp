import json
import logging
import time
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("tool", "duration_ms", "apis_called", "tool_name"):
            val = getattr(record, key, None)
            if val is not None:
                log_data[key] = val
        return json.dumps(log_data)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class ToolLogContext:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.apis_called: list[str] = []
        self._start = time.monotonic()

    def add_api_call(self, api: str) -> None:
        self.apis_called.append(api)

    def finish(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "apis_called": self.apis_called,
            "duration_ms": int((time.monotonic() - self._start) * 1000),
        }
