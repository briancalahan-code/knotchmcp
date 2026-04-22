import pytest
from unittest.mock import patch, AsyncMock

from knotch_mcp.config import Settings


def test_settings_load():
    s = Settings(
        mcp_auth_token="tok",
        apollo_api_key="ak",
        clay_api_key="ck",
        hubspot_private_app_token="ht",
    )
    assert s.mcp_auth_token == "tok"
    assert s.port == 8080


def test_auth_check_rejects_bad_token():
    from knotch_mcp.server import _check_auth

    assert _check_auth("wrong-token", "correct-token") is False


def test_auth_check_accepts_good_token():
    from knotch_mcp.server import _check_auth

    assert _check_auth("correct-token", "correct-token") is True


def test_auth_check_accepts_bearer_prefix():
    from knotch_mcp.server import _check_auth

    assert _check_auth("Bearer correct-token", "correct-token") is True


def test_mcp_instance_exists():
    from knotch_mcp.server import mcp

    assert mcp is not None
    assert mcp.name == "KnotchMCP"
