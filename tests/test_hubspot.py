import pytest
import httpx
import respx

from knotch_mcp.clients.hubspot import HubSpotClient


@pytest.fixture
def hubspot():
    return HubSpotClient(access_token="test-token", portal_id="12345")


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_by_email(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "results": [
                    {
                        "id": "501",
                        "properties": {
                            "firstname": "Jane",
                            "lastname": "Smith",
                            "email": "jane@stripe.com",
                            "jobtitle": "VP Engineering",
                        },
                    }
                ],
            },
        )
    )
    results = await hubspot.search_contacts_by_email("jane@stripe.com")
    assert len(results) == 1
    assert results[0]["id"] == "501"


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_by_linkedin(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "results": [
                    {
                        "id": "502",
                        "properties": {
                            "hs_linkedin_url": "https://linkedin.com/in/janesmith"
                        },
                    }
                ],
            },
        )
    )
    results = await hubspot.search_contacts_by_linkedin(
        "https://linkedin.com/in/janesmith"
    )
    assert len(results) == 1


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_not_found(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "results": []})
    )
    results = await hubspot.search_contacts_by_email("nobody@fake.com")
    assert len(results) == 0


@respx.mock
@pytest.mark.asyncio
async def test_get_contact(hubspot):
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts/501").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "501",
                "properties": {
                    "firstname": "Jane",
                    "lastname": "Smith",
                    "email": "jane@stripe.com",
                    "jobtitle": "VP Engineering",
                    "phone": "",
                    "hs_linkedin_url": "https://linkedin.com/in/janesmith",
                    "city": "",
                    "state": "",
                    "country": "",
                    "company": "Stripe",
                },
            },
        )
    )
    contact = await hubspot.get_contact("501")
    assert contact["properties"]["email"] == "jane@stripe.com"
    assert contact["properties"]["phone"] == ""


@respx.mock
@pytest.mark.asyncio
async def test_create_contact(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "601",
                "properties": {"email": "new@stripe.com", "firstname": "New"},
            },
        )
    )
    contact = await hubspot.create_contact(
        {"email": "new@stripe.com", "firstname": "New", "lastname": "Person"}
    )
    assert contact["id"] == "601"


@respx.mock
@pytest.mark.asyncio
async def test_update_contact(hubspot):
    respx.patch("https://api.hubapi.com/crm/v3/objects/contacts/501").mock(
        return_value=httpx.Response(
            200, json={"id": "501", "properties": {"jobtitle": "SVP Engineering"}}
        )
    )
    contact = await hubspot.update_contact("501", {"jobtitle": "SVP Engineering"})
    assert contact["properties"]["jobtitle"] == "SVP Engineering"


@respx.mock
@pytest.mark.asyncio
async def test_search_company_by_domain(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "results": [
                    {
                        "id": "801",
                        "properties": {"name": "Stripe", "domain": "stripe.com"},
                    }
                ],
            },
        )
    )
    results = await hubspot.search_companies_by_domain("stripe.com")
    assert len(results) == 1
    assert results[0]["id"] == "801"


@respx.mock
@pytest.mark.asyncio
async def test_associate_contact_company(hubspot):
    respx.put(
        "https://api.hubapi.com/crm/v3/objects/contacts/501/associations/companies/801/default"
    ).mock(return_value=httpx.Response(200, json={}))
    await hubspot.associate_contact_to_company("501", "801")


def test_build_contact_url(hubspot):
    url = hubspot.build_contact_url("501")
    assert url == "https://app.hubspot.com/contacts/12345/contact/501"
