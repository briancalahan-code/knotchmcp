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

    async def simulate_callback():
        await asyncio.sleep(0.05)
        for cid, evt in list(clay._pending.items()):
            clay.receive_callback(
                {
                    "correlationId": cid,
                    "status": "completed",
                    "results": [{"contactName": "Jane Smith", "phone": "+14155551234"}],
                    "creditsUsed": 3,
                }
            )

    result, _ = await asyncio.gather(
        clay.enrich_contact("Jane", "Smith", "stripe.com"),
        simulate_callback(),
    )
    assert result["status"] == "completed"
    assert result["results"][0]["phone"] == "+14155551234"


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


def test_receive_callback_unknown_id(clay):
    assert clay.receive_callback({"correlationId": "unknown"}) is False
    assert clay.receive_callback({}) is False
