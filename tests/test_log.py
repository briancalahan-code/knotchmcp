import json
import logging
from knotch_mcp.log import get_logger, ToolLogContext


def test_logger_outputs_json(capfd):
    logger = get_logger("test")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    from knotch_mcp.log import JsonFormatter

    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("test message", extra={"tool": "find_contact", "duration_ms": 123})
    captured = capfd.readouterr()
    data = json.loads(captured.err)
    assert data["message"] == "test message"
    assert data["tool"] == "find_contact"
    assert data["duration_ms"] == 123
    assert "timestamp" in data


def test_tool_log_context():
    ctx = ToolLogContext(tool_name="find_contact")
    ctx.add_api_call("apollo")
    ctx.add_api_call("hubspot")
    result = ctx.finish()
    assert result["tool_name"] == "find_contact"
    assert result["apis_called"] == ["apollo", "hubspot"]
    assert "duration_ms" in result
    assert result["duration_ms"] >= 0
