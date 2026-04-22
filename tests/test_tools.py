import pytest
from unittest.mock import AsyncMock, patch

from knotch_mcp.models import (
    ContactResult,
    FindContactsResult,
    FindPhoneResult,
    EnrichContactResult,
    AddToHubSpotResult,
    ClayEnrichResult,
)


@pytest.fixture
def mock_apollo():
    client = AsyncMock()
    client.resolve_company_domain = AsyncMock(return_value="stripe.com")
    client.people_match = AsyncMock(
        return_value={
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
            "phone_numbers": [],
        }
    )
    client.people_search = AsyncMock(
        return_value=(
            [
                {
                    "id": "p1",
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "title": "VP Engineering",
                },
                {"id": "p2", "first_name": "John", "last_name": "Doe", "title": "CTO"},
            ],
            42,
        )
    )
    return client


@pytest.fixture
def mock_hubspot():
    client = AsyncMock()
    client.search_contacts_by_email = AsyncMock(return_value=[])
    client.search_contacts_by_linkedin = AsyncMock(return_value=[])
    client.build_contact_url = lambda cid: (
        f"https://app.hubspot.com/contacts/12345/contact/{cid}"
    )
    client.get_contact = AsyncMock(
        return_value={
            "id": "501",
            "properties": {
                "firstname": "Jane",
                "lastname": "Smith",
                "email": "jane@stripe.com",
                "jobtitle": "",
                "phone": "",
                "hs_linkedin_url": "",
                "city": "",
                "state": "",
                "country": "",
                "company": "Stripe",
            },
        }
    )
    client.create_contact = AsyncMock(return_value={"id": "601", "properties": {}})
    client.update_contact = AsyncMock(return_value={"id": "501", "properties": {}})
    client.search_companies_by_domain = AsyncMock(
        return_value=[
            {"id": "801", "properties": {"name": "Stripe", "domain": "stripe.com"}},
        ]
    )
    client.associate_contact_to_company = AsyncMock()
    return client


@pytest.fixture
def mock_clay():
    client = AsyncMock()
    client.configured = True
    client.enrich_contact = AsyncMock(
        return_value={
            "status": "completed",
            "results": [{"contactName": "Jane Smith", "phone": "+14155551234"}],
            "creditsUsed": 3,
        }
    )
    return client


@pytest.mark.asyncio
async def test_find_contact_by_details_found_not_in_hubspot(
    mock_apollo, mock_hubspot, mock_clay
):
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane",
        "Smith",
        "Stripe",
        None,
        None,
        mock_apollo,
        mock_hubspot,
    )
    assert isinstance(result, ContactResult)
    assert result.name == "Jane Smith"
    assert result.email == "jane@stripe.com"
    assert result.hubspot_status == "not_found"
    assert "add_to_hubspot" in result.suggested_actions
    assert "phone" in result.gaps
    mock_apollo.resolve_company_domain.assert_called_once_with("Stripe")


@pytest.mark.asyncio
async def test_find_contact_by_details_already_in_hubspot(
    mock_apollo, mock_hubspot, mock_clay
):
    mock_hubspot.search_contacts_by_email.return_value = [
        {"id": "501", "properties": {"email": "jane@stripe.com"}},
    ]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane",
        "Smith",
        "stripe.com",
        "jane@stripe.com",
        None,
        mock_apollo,
        mock_hubspot,
    )
    assert result.hubspot_status == "found"
    assert result.hubspot_contact_id == "501"
    assert "add_to_hubspot" not in result.suggested_actions


@pytest.mark.asyncio
async def test_find_contact_not_found_in_apollo(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = None
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Nobody",
        "Exists",
        "fake.com",
        None,
        None,
        mock_apollo,
        mock_hubspot,
    )
    assert result.name == "Nobody Exists"
    assert result.sources == []
    assert "apollo_returned_no_match" in result.gaps


@pytest.mark.asyncio
async def test_find_contacts_by_role(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _find_contacts_by_role

    result = await _find_contacts_by_role(
        ["VP Engineering", "CTO"],
        "Stripe",
        None,
        3,
        mock_apollo,
        mock_hubspot,
    )
    assert isinstance(result, FindContactsResult)
    assert result.total_available == 42
    assert len(result.candidates) >= 1


@pytest.mark.asyncio
async def test_find_phone_found(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = {
        "id": "abc123",
        "first_name": "Jane",
        "last_name": "Smith",
        "phone_numbers": [{"raw_number": "+14155551234", "type": "mobile"}],
        "email": "jane@stripe.com",
        "title": "VP",
        "organization": {},
        "email_status": None,
        "linkedin_url": None,
        "city": None,
        "state": None,
        "country": None,
    }
    from knotch_mcp.tools import _find_phone

    result = await _find_phone(None, "jane@stripe.com", None, None, mock_apollo)
    assert isinstance(result, FindPhoneResult)
    assert result.found is True
    assert result.phone == "+14155551234"


@pytest.mark.asyncio
async def test_find_phone_not_found(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = {
        "id": "abc123",
        "first_name": "Jane",
        "last_name": "Smith",
        "phone_numbers": [],
        "email": "jane@stripe.com",
        "title": "VP",
        "organization": {},
        "email_status": None,
        "linkedin_url": None,
        "city": None,
        "state": None,
        "country": None,
    }
    from knotch_mcp.tools import _find_phone

    result = await _find_phone(None, "jane@stripe.com", None, None, mock_apollo)
    assert result.found is False
    assert result.suggested_action == "clay_enrich"


@pytest.mark.asyncio
async def test_enrich_contact(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _enrich_contact

    result = await _enrich_contact("501", mock_apollo, mock_hubspot, mock_clay)
    assert isinstance(result, EnrichContactResult)
    assert "jobtitle" in result.filled
    assert "email" in result.already_populated
    assert "apollo" in result.sources_used


@pytest.mark.asyncio
async def test_add_to_hubspot_creates_new(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _add_to_hubspot

    result = await _add_to_hubspot(
        "Jane",
        "Smith",
        "jane@stripe.com",
        "VP Engineering",
        "Stripe",
        "stripe.com",
        "https://linkedin.com/in/janesmith",
        None,
        None,
        None,
        mock_hubspot,
    )
    assert isinstance(result, AddToHubSpotResult)
    assert result.action == "created"
    assert result.hubspot_contact_id == "601"
    assert result.company_associated is True
    assert result.company_name == "Stripe"


@pytest.mark.asyncio
async def test_add_to_hubspot_updates_existing(mock_apollo, mock_hubspot, mock_clay):
    mock_hubspot.search_contacts_by_email.return_value = [
        {"id": "501", "properties": {"email": "jane@stripe.com"}},
    ]
    from knotch_mcp.tools import _add_to_hubspot

    result = await _add_to_hubspot(
        "Jane",
        "Smith",
        "jane@stripe.com",
        "VP Engineering",
        "Stripe",
        "stripe.com",
        None,
        None,
        None,
        None,
        mock_hubspot,
    )
    assert result.action == "updated"
    assert result.hubspot_contact_id == "501"


@pytest.mark.asyncio
async def test_clay_enrich_success(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _clay_enrich

    result = await _clay_enrich(
        "Jane", "Smith", "stripe.com", ["phone", "email"], mock_clay
    )
    assert isinstance(result, ClayEnrichResult)
    assert result.task_status == "completed"
    assert "phone" in result.enriched_fields
