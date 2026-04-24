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


def test_build_deal_url(hubspot):
    url = hubspot.build_deal_url("100")
    assert url == "https://app.hubspot.com/contacts/12345/deal/100"


@respx.mock
@pytest.mark.asyncio
async def test_get_deal(hubspot):
    respx.get("https://api.hubapi.com/crm/v3/objects/deals/100").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "100",
                "properties": {
                    "dealname": "Acme Corp K1",
                    "dealstage": "qualificationlead",
                    "amount": "50000",
                },
            },
        )
    )
    deal = await hubspot.get_deal("100")
    assert deal["id"] == "100"
    assert deal["properties"]["dealname"] == "Acme Corp K1"


@respx.mock
@pytest.mark.asyncio
async def test_search_deals(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/deals/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "results": [
                    {
                        "id": "100",
                        "properties": {"dealname": "Acme Corp K1"},
                    }
                ],
            },
        )
    )
    results = await hubspot.search_deals("Acme")
    assert len(results) == 1
    assert results[0]["properties"]["dealname"] == "Acme Corp K1"


@respx.mock
@pytest.mark.asyncio
async def test_update_deal(hubspot):
    respx.patch("https://api.hubapi.com/crm/v3/objects/deals/100").mock(
        return_value=httpx.Response(
            200, json={"id": "100", "properties": {"amount": "75000"}}
        )
    )
    deal = await hubspot.update_deal("100", {"amount": "75000"})
    assert deal["properties"]["amount"] == "75000"


@respx.mock
@pytest.mark.asyncio
async def test_get_company(hubspot):
    respx.get("https://api.hubapi.com/crm/v3/objects/companies/801").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "801",
                "properties": {"name": "Acme Corp", "domain": "acme.com"},
            },
        )
    )
    company = await hubspot.get_company("801")
    assert company["properties"]["name"] == "Acme Corp"


@respx.mock
@pytest.mark.asyncio
async def test_update_company(hubspot):
    respx.patch("https://api.hubapi.com/crm/v3/objects/companies/801").mock(
        return_value=httpx.Response(
            200, json={"id": "801", "properties": {"industry": "SaaS"}}
        )
    )
    company = await hubspot.update_company("801", {"industry": "SaaS"})
    assert company["properties"]["industry"] == "SaaS"


@respx.mock
@pytest.mark.asyncio
async def test_get_associations(hubspot):
    respx.get(
        "https://api.hubapi.com/crm/v4/objects/deals/100/associations/contacts"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"toObjectId": "201"},
                    {"toObjectId": "202"},
                ]
            },
        )
    )
    results = await hubspot.get_associations("deals", "100", "contacts")
    assert len(results) == 2


@respx.mock
@pytest.mark.asyncio
async def test_batch_read(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/batch/read").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "201", "properties": {"firstname": "Jane"}},
                    {"id": "202", "properties": {"firstname": "Bob"}},
                ]
            },
        )
    )
    results = await hubspot.batch_read("contacts", ["201", "202"], ["firstname"])
    assert len(results) == 2


@respx.mock
@pytest.mark.asyncio
async def test_batch_read_empty(hubspot):
    results = await hubspot.batch_read("contacts", [], ["firstname"])
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_get_pipelines(hubspot):
    respx.get("https://api.hubapi.com/crm/v3/pipelines/deals").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "default",
                        "label": "Sales Pipeline",
                        "stages": [
                            {"id": "qual", "label": "Qualification"},
                        ],
                    }
                ]
            },
        )
    )
    pipelines = await hubspot.get_pipelines()
    assert len(pipelines) == 1
    assert pipelines[0]["stages"][0]["label"] == "Qualification"


@respx.mock
@pytest.mark.asyncio
async def test_associate_objects(hubspot):
    respx.put(
        "https://api.hubapi.com/crm/v4/objects/contacts/201/associations/deals/100"
    ).mock(return_value=httpx.Response(200, json={}))
    await hubspot.associate_objects("contacts", "201", "deals", "100")


@pytest.mark.asyncio
async def test_associate_objects_rejects_invalid_type(hubspot):
    with pytest.raises(ValueError, match="Write scope limited"):
        await hubspot.associate_objects("contacts", "201", "workflows", "999")
