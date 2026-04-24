import asyncio

import pytest
import httpx
import respx

from knotch_mcp.clients.clay import ClayClient


@pytest.fixture
def clay():
    return ClayClient(webhook_url="https://hooks.clay.com/test", webhook_token="tok")


def test_configured_flag():
    assert ClayClient(webhook_url="https://hooks.clay.com/x").configured is True
    assert ClayClient().configured is False


@respx.mock
@pytest.mark.asyncio
async def test_enrich_contact_success(clay):
    respx.post("https://hooks.clay.com/test").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    result = await clay.enrich_contact("Jane", "Smith", "stripe.com")
    assert result["status"] == "submitted"
    assert "correlationId" in result

    cid = result["correlationId"]
    assert clay.peek_result(cid) is None

    clay.receive_callback(
        {
            "correlationId": cid,
            "Phone Number": "+14155551234",
            "creditsUsed": 1,
        }
    )
    stored = clay.peek_result(cid)
    assert stored is not None
    assert stored["Phone Number"] == "+14155551234"


@respx.mock
@pytest.mark.asyncio
async def test_enrich_contact_webhook_error(clay):
    respx.post("https://hooks.clay.com/test").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    result = await clay.enrich_contact("Jane", "Smith", "stripe.com")
    assert result["status"] == "webhook_error"


@pytest.mark.asyncio
async def test_enrich_contact_not_configured():
    client = ClayClient()
    result = await client.enrich_contact("Jane", "Smith", "stripe.com")
    assert result["status"] == "not_configured"


def test_receive_callback_with_correlation_id_always_stored(clay):
    assert clay.receive_callback({"correlationId": "unknown"}) is True
    assert clay.peek_result("unknown") is not None


def test_receive_callback_no_match_without_id(clay):
    assert clay.receive_callback({}) is False
    assert clay.receive_callback({"firstName": "Nobody"}) is False
