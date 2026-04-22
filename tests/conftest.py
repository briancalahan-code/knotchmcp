import os

os.environ.setdefault("MCP_AUTH_TOKEN", "test-token")
os.environ.setdefault("APOLLO_API_KEY", "test-key")
os.environ.setdefault("CLAY_API_KEY", "test-key")
os.environ.setdefault("HUBSPOT_PRIVATE_APP_TOKEN", "test-key")

import pytest
from knotch_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(
        mcp_auth_token="test-token",
        apollo_api_key="test-apollo-key",
        clay_api_key="test-clay-key",
        hubspot_private_app_token="test-hubspot-token",
        hubspot_portal_id="12345",
        apollo_rate_limit=45,
        port=8080,
    )
