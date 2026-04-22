import pytest
import httpx
import respx

from knotch_mcp.clients.apollo import ApolloClient
from knotch_mcp.rate_limit import TokenBucket


@pytest.fixture
def apollo():
    bucket = TokenBucket(rate=100.0, capacity=100)
    return ApolloClient(api_key="test-key", rate_limiter=bucket)


@respx.mock
@pytest.mark.asyncio
async def test_people_match(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "id": "abc123",
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "title": "VP Engineering",
                    "organization": {"name": "Stripe", "primary_domain": "stripe.com"},
                    "email": "jane@stripe.com",
                    "email_status": "verified",
                    "linkedin_url": "https://linkedin.com/in/janesmith",
                    "city": "San Francisco",
                    "state": "California",
                    "country": "United States",
                    "phone_numbers": [{"raw_number": "+14155551234", "type": "mobile"}],
                }
            },
        )
    )
    result = await apollo.people_match(
        first_name="Jane", last_name="Smith", domain="stripe.com"
    )
    assert result["id"] == "abc123"
    assert result["email"] == "jane@stripe.com"
    assert result["title"] == "VP Engineering"


@respx.mock
@pytest.mark.asyncio
async def test_people_match_not_found(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json={"person": None})
    )
    result = await apollo.people_match(
        first_name="Nobody", last_name="Exists", domain="fake.com"
    )
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_people_search(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_people/api_search").mock(
        return_value=httpx.Response(
            200,
            json={
                "people": [
                    {
                        "id": "p1",
                        "first_name": "Jane",
                        "last_name": "Smith",
                        "title": "VP Engineering",
                    },
                    {
                        "id": "p2",
                        "first_name": "John",
                        "last_name": "Doe",
                        "title": "CTO",
                    },
                ],
                "pagination": {"total_entries": 42},
            },
        )
    )
    people, total = await apollo.people_search(
        titles=["VP Engineering", "CTO"], domain="stripe.com", per_page=3
    )
    assert len(people) == 2
    assert total == 42


@respx.mock
@pytest.mark.asyncio
async def test_org_search(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_companies/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organizations": [{"name": "Stripe", "primary_domain": "stripe.com"}]
            },
        )
    )
    domain = await apollo.resolve_company_domain("Stripe")
    assert domain == "stripe.com"


@respx.mock
@pytest.mark.asyncio
async def test_org_search_no_result(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_companies/search").mock(
        return_value=httpx.Response(200, json={"organizations": []})
    )
    domain = await apollo.resolve_company_domain("FakeCompany12345")
    assert domain is None


@respx.mock
@pytest.mark.asyncio
async def test_people_match_with_phone(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {
                    "id": "abc123",
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "title": "VP Engineering",
                    "organization": {"name": "Stripe", "primary_domain": "stripe.com"},
                    "email": "jane@stripe.com",
                    "email_status": "verified",
                    "linkedin_url": None,
                    "city": None,
                    "state": None,
                    "country": None,
                    "phone_numbers": [{"raw_number": "+14155551234", "type": "mobile"}],
                }
            },
        )
    )
    result = await apollo.people_match(
        first_name="Jane",
        last_name="Smith",
        domain="stripe.com",
        reveal_phone_number=True,
    )
    assert result["phone_numbers"][0]["raw_number"] == "+14155551234"
