import pytest
import httpx
import respx

from knotch_mcp.clients.clay import ClayClient


@pytest.fixture
def clay():
    return ClayClient(api_key="test-key")


@respx.mock
@pytest.mark.asyncio
async def test_find_and_enrich_contacts(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-abc"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "results": [
                    {
                        "contactName": "Jane Smith",
                        "email": "jane@stripe.com",
                        "phone": "+14155551234",
                        "linkedinUrl": "https://linkedin.com/in/janesmith",
                    }
                ],
                "creditsUsed": 3,
            },
        )
    )
    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Jane Smith", "companyIdentifier": "stripe.com"}],
    )
    assert result["status"] == "completed"
    assert len(result["results"]) == 1
    assert result["results"][0]["email"] == "jane@stripe.com"


@respx.mock
@pytest.mark.asyncio
async def test_find_contacts_at_company(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-contacts-at-company").mock(
        return_value=httpx.Response(200, json={"taskId": "task-xyz"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-xyz").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "results": [{"contactName": "John Doe", "title": "CTO"}],
                "creditsUsed": 2,
            },
        )
    )
    result = await clay.find_contacts_at_company(
        company_domain="stripe.com", job_title_keywords=["CTO"]
    )
    assert result["status"] == "completed"
    assert result["results"][0]["title"] == "CTO"


@respx.mock
@pytest.mark.asyncio
async def test_poll_task_timeout(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-slow"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-slow").mock(
        return_value=httpx.Response(200, json={"status": "processing"})
    )
    clay._poll_timeout = 0.5
    clay._poll_initial_delay = 0.1
    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Slow Person", "companyIdentifier": "slow.com"}],
    )
    assert result["status"] == "timeout"


@respx.mock
@pytest.mark.asyncio
async def test_find_and_enrich_with_custom_data_points(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-phone"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-phone").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "results": [{"contactName": "Jane Smith", "phone": "+14155551234"}],
                "creditsUsed": 5,
            },
        )
    )
    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Jane Smith", "companyIdentifier": "stripe.com"}],
        contact_data_points=[
            {"type": "Email"},
            {
                "type": "Custom",
                "dataPointName": "Phone Number",
                "dataPointDescription": "Find direct phone number",
            },
        ],
    )
    assert result["status"] == "completed"
