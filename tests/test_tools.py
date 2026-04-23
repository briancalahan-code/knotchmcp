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
    client.resolve_company_domains = AsyncMock(return_value=[("Stripe", "stripe.com")])
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
    mock_apollo.resolve_company_domains.assert_called_once_with("Stripe", limit=3)
    assert result.match_method == "exact"
    assert result.confidence == "high"


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
    mock_apollo.people_search.return_value = ([], 0)
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
    assert "no_match_after_fallback" in result.gaps


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
    mock_clay.enrich_contact.return_value = {
        "status": "submitted",
        "correlationId": "corr-test",
    }
    mock_clay.peek_result = lambda cid: {
        "creditsUsed": 1,
        "phone": "+14155551234",
    }
    mock_clay.get_result = lambda cid: {
        "creditsUsed": 1,
        "phone": "+14155551234",
    }
    from knotch_mcp.tools import _clay_enrich

    result = await _clay_enrich(
        "Jane", "Smith", "stripe.com", ["phone", "email"], mock_clay
    )
    assert isinstance(result, ClayEnrichResult)
    assert result.task_status == "completed"
    assert "phone" in result.enriched_fields


# ── Fallback cascade tests ─────────────────────────────────────────


def _make_person(first="Jane", last="Smith", org_name="Stripe", domain="stripe.com"):
    return {
        "id": "abc123",
        "first_name": first,
        "last_name": last,
        "title": "VP Engineering",
        "organization": {"name": org_name, "primary_domain": domain},
        "email": f"{first.lower()}@{domain}",
        "email_status": "verified",
        "linkedin_url": f"https://linkedin.com/in/{first.lower()}{last.lower()}",
        "city": "San Francisco",
        "state": "California",
        "country": "United States",
        "phone_numbers": [],
    }


@pytest.mark.asyncio
async def test_nickname_fallback(mock_apollo, mock_hubspot, mock_clay):
    call_count = 0
    robert = _make_person(first="Robert")

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("first_name") == "Robert":
            return robert
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Bob", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    assert result.name == "Robert Smith"
    assert result.match_method == "nickname"


@pytest.mark.asyncio
async def test_email_fallback(mock_apollo, mock_hubspot, mock_clay):
    person = _make_person()

    async def match_side_effect(**kwargs):
        if kwargs.get("first_name"):
            return None
        if kwargs.get("email") == "jane@stripe.com":
            return person
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jaane", "Smith", "Stripe", "jane@stripe.com", None, mock_apollo, mock_hubspot
    )
    assert result.name == "Jane Smith"
    assert result.match_method == "email"


@pytest.mark.asyncio
async def test_keyword_search_fallback(mock_apollo, mock_hubspot, mock_clay):
    person = _make_person(
        first="Amy", last="Holmes", org_name="GrowthZone", domain="growthzone.com"
    )

    async def match_side_effect(**kwargs):
        if kwargs.get("apollo_id") == "kw1":
            return person
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.people_search.return_value = (
        [{"id": "kw1", "first_name": "Amy", "last_name": "Holmes"}],
        1,
    )
    mock_apollo.resolve_company_domains.return_value = [
        ("GrowthZone", "growthzone.com")
    ]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Amy", "Holms", "Growthzone", None, None, mock_apollo, mock_hubspot
    )
    assert result.name == "Amy Holmes"
    assert result.match_method == "keyword_search"


@pytest.mark.asyncio
async def test_alternate_domain_fallback(mock_apollo, mock_hubspot, mock_clay):
    person = _make_person(org_name="Acme Corp", domain="acme.io")

    async def match_side_effect(**kwargs):
        if kwargs.get("domain") == "acme.io":
            return person
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [
        ("Acme Inc", "acme.com"),
        ("Acme Corp", "acme.io"),
    ]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Acme", None, None, mock_apollo, mock_hubspot
    )
    assert result.name == "Jane Smith"
    assert result.match_method == "alternate_domain"


@pytest.mark.asyncio
async def test_multiple_candidates(mock_apollo, mock_hubspot, mock_clay):
    p1 = _make_person(
        first="Jane", last="Smith", org_name="Stripe", domain="stripe.com"
    )
    p2 = _make_person(first="Jane", last="Smith", org_name="Acme", domain="acme.com")

    mock_apollo.people_match = AsyncMock(side_effect=[None, p1, p2])
    mock_apollo.people_search.return_value = (
        [
            {"id": "kw1", "first_name": "Jane", "last_name": "Smith"},
            {"id": "kw2", "first_name": "Jane", "last_name": "Smith"},
        ],
        2,
    )
    mock_apollo.resolve_company_domains.return_value = [("Stripe", "stripe.com")]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    assert result.match_method == "keyword_search"
    assert result.alternate_matches is not None
    assert len(result.alternate_matches) == 1
    assert result.alternate_matches[0]["company"] == "Acme"
    assert "email" in result.alternate_matches[0]
    assert "linkedin_url" in result.alternate_matches[0]
    assert "lookup_contact" in result.next_step
    assert "alternate" in result.next_step


@pytest.mark.asyncio
async def test_exact_match_no_fallback(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    assert result.match_method == "exact"
    mock_apollo.people_search.assert_not_called()


# ── Bug 1: Phantom detection ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_phantom_rejected_at_tier1(mock_apollo, mock_hubspot, mock_clay):
    """Phantom record (ID but no data) should not be returned as exact match."""
    mock_apollo.people_match = AsyncMock(
        return_value={
            "id": "phantom1",
            "first_name": None,
            "last_name": None,
            "email": None,
            "title": None,
            "linkedin_url": None,
            "organization": {"name": None, "primary_domain": None},
            "phone_numbers": [],
        }
    )
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Zebulon", "Thornwhistle", "Salesforce", None, None, mock_apollo, mock_hubspot
    )
    assert result.match_method is None
    assert "no_match_after_fallback" in result.gaps


@pytest.mark.asyncio
async def test_phantom_rejected_at_tier2(mock_apollo, mock_hubspot, mock_clay):
    """Phantom from relaxed match (email lookup) should be skipped."""
    phantom = {
        "id": "phantom2",
        "first_name": None,
        "last_name": None,
        "email": None,
        "title": None,
        "linkedin_url": None,
        "organization": {"name": None},
        "phone_numbers": [],
    }

    async def match_side_effect(**kwargs):
        if kwargs.get("email") == "z@salesforce.com":
            return phantom
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Zeb",
        "Thorn",
        "Salesforce",
        "z@salesforce.com",
        None,
        mock_apollo,
        mock_hubspot,
    )
    assert result.match_method is None
    assert result.confidence == "low"


# ── Bug 2: Company filter ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_company_mismatch_continues_cascade(mock_apollo, mock_hubspot, mock_clay):
    """Person at wrong company should not be returned as exact match."""
    wrong_company = _make_person(
        first="John", last="Smith", org_name="OpenAI", domain="openai.com"
    )
    right_person = _make_person(
        first="John", last="Smith", org_name="Google", domain="google.com"
    )

    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return wrong_company
        if kwargs.get("apollo_id") == "kw1":
            return right_person
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [("Google", "google.com")]
    mock_apollo.people_search.return_value = (
        [{"id": "kw1", "first_name": "John", "last_name": "Smith"}],
        1,
    )
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "John", "Smith", "Google", None, None, mock_apollo, mock_hubspot
    )
    assert result.company == "Google"
    assert result.match_method == "keyword_search"


@pytest.mark.asyncio
async def test_company_mismatch_in_alternate_matches(
    mock_apollo, mock_hubspot, mock_clay
):
    """Wrong-company stash should appear in alternate_matches."""
    wrong_company = _make_person(
        first="John", last="Smith", org_name="OpenAI", domain="openai.com"
    )
    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return wrong_company
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [("Google", "google.com")]
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "John", "Smith", "Google", None, None, mock_apollo, mock_hubspot
    )
    assert result.match_method is None
    assert result.confidence == "low"


# ── Bug 3: find_contacts_by_role ──────────────────────────────────────


@pytest.mark.asyncio
async def test_role_search_hubspot_error_isolated(mock_apollo, mock_hubspot, mock_clay):
    """One candidate's HubSpot failure should not crash the whole search."""
    call_count = 0

    async def search_email_side_effect(email):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("HubSpot 429")
        return []

    mock_hubspot.search_contacts_by_email = AsyncMock(
        side_effect=search_email_side_effect
    )
    from knotch_mcp.tools import _find_contacts_by_role

    result = await _find_contacts_by_role(
        ["VP Engineering"], "Stripe", None, 3, mock_apollo, mock_hubspot
    )
    assert len(result.candidates) >= 1
    statuses = [c.hubspot_status for c in result.candidates]
    assert "error" in statuses


@pytest.mark.asyncio
async def test_role_search_filters_by_company(mock_apollo, mock_hubspot, mock_clay):
    """Candidates from wrong companies should be filtered out."""
    wrong_co = _make_person(
        first="Bob", last="Jones", org_name="Acme", domain="acme.com"
    )
    right_co = _make_person(
        first="Jane", last="Smith", org_name="Stripe", domain="stripe.com"
    )

    async def match_side_effect(**kwargs):
        if kwargs.get("apollo_id") == "p1":
            return right_co
        if kwargs.get("apollo_id") == "p2":
            return wrong_co
        return right_co

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.people_search.return_value = (
        [
            {"id": "p1", "first_name": "Jane", "last_name": "Smith"},
            {"id": "p2", "first_name": "Bob", "last_name": "Jones"},
        ],
        2,
    )
    from knotch_mcp.tools import _find_contacts_by_role

    result = await _find_contacts_by_role(
        ["VP Engineering"], "Stripe", None, 3, mock_apollo, mock_hubspot
    )
    companies = [c.company for c in result.candidates]
    assert "Stripe" in companies


@pytest.mark.asyncio
async def test_role_search_empty_when_total_zero(mock_apollo, mock_hubspot, mock_clay):
    """total_available=0 should return empty candidates immediately."""
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contacts_by_role

    result = await _find_contacts_by_role(
        ["VP Engineering"], "Stripe", None, 3, mock_apollo, mock_hubspot
    )
    assert result.candidates == []
    assert result.total_available == 0


# ── Bug 4: Thin records ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thin_continues_cascade(mock_apollo, mock_hubspot, mock_clay):
    """Tier 1 thin record should not stop the cascade."""
    thin = {
        "id": "thin1",
        "first_name": "Roland",
        "last_name": "B",
        "email": "roland@usertesting.com",
        "title": None,
        "linkedin_url": None,
        "organization": {"name": "UserTesting", "primary_domain": "usertesting.com"},
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }
    full = _make_person(
        first="Roland", last="B", org_name="UserTesting", domain="usertesting.com"
    )

    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return thin
        if kwargs.get("apollo_id") == "kw1":
            return full
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [
        ("UserTesting", "usertesting.com")
    ]
    mock_apollo.people_search.return_value = (
        [{"id": "kw1", "first_name": "Roland", "last_name": "B"}],
        1,
    )
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Roland", "B", "UserTesting", None, None, mock_apollo, mock_hubspot
    )
    assert result.match_method == "keyword_search"
    assert result.confidence != "low"


@pytest.mark.asyncio
async def test_thin_used_as_last_resort(mock_apollo, mock_hubspot, mock_clay):
    """If all tiers fail except thin Tier 1, return it with low confidence."""
    thin = {
        "id": "thin2",
        "first_name": "Roland",
        "last_name": "B",
        "email": "roland@usertesting.com",
        "title": None,
        "linkedin_url": None,
        "organization": {"name": "UserTesting", "primary_domain": "usertesting.com"},
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }

    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return thin
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [
        ("UserTesting", "usertesting.com")
    ]
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Roland", "B", "UserTesting", None, None, mock_apollo, mock_hubspot
    )
    assert result.confidence == "low"
    assert any("thin" in w for w in result.warnings)


# ── Bug 5: Clay warnings ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clay_warns_on_empty_with_credits(mock_apollo, mock_hubspot, mock_clay):
    """Clay uses credits but returns no data — should warn."""
    mock_clay.enrich_contact.return_value = {
        "status": "submitted",
        "correlationId": "corr-1",
    }
    mock_clay.peek_result = lambda cid: {"creditsUsed": 3}
    mock_clay.get_result = lambda cid: {"creditsUsed": 3}
    from knotch_mcp.tools import _clay_enrich

    result = await _clay_enrich("Nobody", "Here", "fake.com", ["phone"], mock_clay)
    assert result.task_status == "completed"
    assert len(result.warnings) == 1
    assert "credit" in result.warnings[0].lower()


@pytest.mark.asyncio
async def test_clay_no_warning_on_success(mock_apollo, mock_hubspot, mock_clay):
    """Clay returns data — no warnings expected."""
    mock_clay.enrich_contact.return_value = {
        "status": "submitted",
        "correlationId": "corr-2",
    }
    mock_clay.peek_result = lambda cid: {
        "creditsUsed": 1,
        "phone": "+14155551234",
    }
    mock_clay.get_result = lambda cid: {
        "creditsUsed": 1,
        "phone": "+14155551234",
    }
    from knotch_mcp.tools import _clay_enrich

    result = await _clay_enrich("Jane", "Smith", "stripe.com", ["phone"], mock_clay)
    assert result.warnings == []
    assert "phone" in result.enriched_fields


# ── Bug 6: Email-to-company inference ─────────────────────────────────


def test_company_inferred_from_email():
    """When org.name is null but email has company domain, infer company."""
    from knotch_mcp.tools import _extract_contact

    person = {
        "id": "inf1",
        "first_name": "Bob",
        "last_name": "Iger",
        "email": "bob@disney.com",
        "email_status": "verified",
        "title": "CEO",
        "linkedin_url": None,
        "organization": {"name": None, "primary_domain": None},
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }
    result = _extract_contact(person)
    assert result.company == "Disney"
    assert result.company_domain == "disney.com"


def test_email_domain_overrides_org_domain():
    """When email domain differs from org.primary_domain, prefer email domain."""
    from knotch_mcp.tools import _extract_contact

    person = {
        "id": "dom1",
        "first_name": "Catie",
        "last_name": "Ivey",
        "email": "catie.ivey@growthzone.com",
        "email_status": "verified",
        "title": "CRO",
        "linkedin_url": None,
        "organization": {
            "name": "GrowthZone AMS",
            "primary_domain": "micronetonline.com",
        },
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }
    result = _extract_contact(person)
    assert result.company_domain == "growthzone.com"


def test_freemail_not_inferred():
    """Freemail domains should not be used to infer company."""
    from knotch_mcp.tools import _extract_contact

    person = {
        "id": "inf2",
        "first_name": "Bob",
        "last_name": "Iger",
        "email": "bob@gmail.com",
        "email_status": "verified",
        "title": None,
        "linkedin_url": None,
        "organization": {"name": None, "primary_domain": None},
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }
    result = _extract_contact(person)
    assert result.company is None


# ── Bug 7: Conditional next_step ──────────────────────────────────────


@pytest.mark.asyncio
async def test_next_step_high_confidence(mock_apollo, mock_hubspot, mock_clay):
    """Exact match should get normal HubSpot prompt."""
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    assert result.confidence == "high"
    assert result.next_step is not None
    assert "HubSpot" in result.next_step


@pytest.mark.asyncio
async def test_next_step_low_confidence_warns(mock_apollo, mock_hubspot, mock_clay):
    """Low confidence result should ask for verification, not HubSpot push."""
    thin = {
        "id": "thin3",
        "first_name": "Zack",
        "last_name": "Nobody",
        "email": "zack@fake.com",
        "title": None,
        "linkedin_url": None,
        "organization": {"name": "FakeCo", "primary_domain": "fake.com"},
        "phone_numbers": [],
        "city": None,
        "state": None,
        "country": None,
    }

    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return thin
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.resolve_company_domains.return_value = [("FakeCo", "fake.com")]
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Zack", "Nobody", "FakeCo", None, None, mock_apollo, mock_hubspot
    )
    assert result.confidence == "low"
    assert (
        "verify" in result.next_step.lower()
        or "low confidence" in result.next_step.lower()
    )


@pytest.mark.asyncio
async def test_no_match_has_next_step(mock_apollo, mock_hubspot, mock_clay):
    """No-match path should still have a next_step."""
    mock_apollo.people_match.return_value = None
    mock_apollo.people_search.return_value = ([], 0)
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Nobody", "Exists", "fake.com", None, None, mock_apollo, mock_hubspot
    )
    assert result.next_step is not None
    assert result.confidence == "low"


# ── Validation helpers unit tests ─────────────────────────────────────


def test_is_phantom_true():
    from knotch_mcp.tools import _is_phantom

    assert _is_phantom(None) is True
    assert _is_phantom({}) is True
    assert (
        _is_phantom(
            {
                "id": "x",
                "email": None,
                "title": None,
                "linkedin_url": None,
                "organization": {"name": None},
            }
        )
        is True
    )


def test_is_phantom_false():
    from knotch_mcp.tools import _is_phantom

    assert _is_phantom({"email": "a@b.com"}) is False
    assert _is_phantom({"title": "CEO"}) is False


def test_is_thin_true():
    from knotch_mcp.tools import _is_thin

    assert _is_thin({"email": "a@b.com"}) is True
    assert _is_thin({"email": None, "title": None, "linkedin_url": None}) is True


def test_is_thin_false():
    from knotch_mcp.tools import _is_thin

    assert (
        _is_thin(
            {"email": "a@b.com", "title": "CEO", "linkedin_url": "https://li.com/in/x"}
        )
        is False
    )
    assert _is_thin({"email": "a@b.com", "title": "CEO"}) is False


# ── v3: Company matching (fuzzy) ────────────────────────────────────


def test_significant_words_strips_stopwords():
    from knotch_mcp.tools import _significant_words

    assert _significant_words("The Fox Valley") == {"fox", "valley"}
    assert _significant_words("Association of Realtors") == {"realtors"}


def test_significant_words_strips_punctuation():
    from knotch_mcp.tools import _significant_words

    result = _significant_words("REALTOR® Corp.")
    assert "realtor" in result
    assert "corp" not in result  # stopword


def test_company_matches_word_overlap():
    from knotch_mcp.tools import _company_matches

    person = {
        "organization": {
            "name": "REALTOR® Association of the Fox Valley",
            "primary_domain": "foxvalleyrealtors.com",
        }
    }
    assert _company_matches(person, "Fox Valley Realtors", []) is True


def test_company_matches_no_false_positive_single_word():
    """Word-overlap needs >=2 words, so single shared word alone doesn't match."""
    from knotch_mcp.tools import _company_matches

    # "Valley Inc" vs "Fox Valley Realtors" — only 1 significant word overlap ("valley"),
    # and neither is a substring of the other, so it should NOT match.
    person = {
        "organization": {"name": "Fox Valley Realtors", "primary_domain": "fvr.com"}
    }
    assert _company_matches(person, "Valley Inc", []) is False


def test_company_matches_no_false_positive_unrelated():
    from knotch_mcp.tools import _company_matches

    person = {"organization": {"name": "Goldman Sachs", "primary_domain": "gs.com"}}
    assert _company_matches(person, "JPMorgan Chase", []) is False


def test_company_matches_substring_still_works():
    from knotch_mcp.tools import _company_matches

    person = {"organization": {"name": "Stripe, Inc.", "primary_domain": "stripe.com"}}
    assert _company_matches(person, "Stripe", []) is True


def test_company_matches_domain_still_works():
    from knotch_mcp.tools import _company_matches

    person = {"organization": {"name": "Unknown Co", "primary_domain": "stripe.com"}}
    assert _company_matches(person, "Whatever", [("Stripe", "stripe.com")]) is True


# ── v3: lookup_contact ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_contact_found(mock_apollo, mock_hubspot):
    from knotch_mcp.tools import _lookup_contact

    result = await _lookup_contact("abc123", mock_apollo, mock_hubspot)
    assert isinstance(result, ContactResult)
    assert result.match_method == "lookup"
    assert result.confidence == "high"
    assert result.email == "jane@stripe.com"
    assert result.linkedin_url == "https://linkedin.com/in/janesmith"
    mock_apollo.people_match.assert_called_once_with(apollo_id="abc123")


@pytest.mark.asyncio
async def test_lookup_contact_not_found(mock_apollo, mock_hubspot):
    mock_apollo.people_match = AsyncMock(return_value=None)
    from knotch_mcp.tools import _lookup_contact

    result = await _lookup_contact("bad_id", mock_apollo, mock_hubspot)
    assert result.confidence == "low"
    assert "lookup_failed" in result.gaps


@pytest.mark.asyncio
async def test_lookup_contact_phantom(mock_apollo, mock_hubspot):
    mock_apollo.people_match = AsyncMock(
        return_value={
            "id": "phantom1",
            "email": None,
            "title": None,
            "linkedin_url": None,
            "organization": {"name": None},
        }
    )
    from knotch_mcp.tools import _lookup_contact

    result = await _lookup_contact("phantom1", mock_apollo, mock_hubspot)
    assert result.confidence == "low"
    assert "lookup_failed" in result.gaps


# ── v3: Rich alternates ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alternate_match_has_rich_fields(mock_apollo, mock_hubspot):
    """Alternates from keyword search should have email, linkedin, company_domain."""
    p1 = _make_person(
        first="Jane", last="Smith", org_name="Stripe", domain="stripe.com"
    )
    p2 = _make_person(first="Jane", last="Smith", org_name="Acme", domain="acme.com")

    mock_apollo.people_match = AsyncMock(side_effect=[None, p1, p2])
    mock_apollo.people_search.return_value = (
        [
            {"id": "kw1", "first_name": "Jane", "last_name": "Smith"},
            {"id": "kw2", "first_name": "Jane", "last_name": "Smith"},
        ],
        2,
    )
    mock_apollo.resolve_company_domains.return_value = [("Stripe", "stripe.com")]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    alt = result.alternate_matches[0]
    assert "email" in alt
    assert "linkedin_url" in alt
    assert "company_domain" in alt
    assert alt["company"] == "Acme"


# ── v3: next_step with alternates ──────────────────────────────────


@pytest.mark.asyncio
async def test_next_step_mentions_alternates_count(mock_apollo, mock_hubspot):
    """When 2 alternates exist, next_step should mention them."""
    p1 = _make_person(
        first="Jane", last="Smith", org_name="Stripe", domain="stripe.com"
    )
    p2 = _make_person(first="Jane", last="Smith", org_name="Acme", domain="acme.com")
    p3 = _make_person(
        first="Jane", last="Smith", org_name="Widgets", domain="widgets.com"
    )

    mock_apollo.people_match = AsyncMock(side_effect=[None, p1, p2, p3])
    mock_apollo.people_search.return_value = (
        [
            {"id": "kw1", "first_name": "Jane", "last_name": "Smith"},
            {"id": "kw2", "first_name": "Jane", "last_name": "Smith"},
            {"id": "kw3", "first_name": "Jane", "last_name": "Smith"},
        ],
        3,
    )
    mock_apollo.resolve_company_domains.return_value = [("Stripe", "stripe.com")]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None, mock_apollo, mock_hubspot
    )
    assert "2 alternate matches" in result.next_step
    assert "lookup_contact" in result.next_step


@pytest.mark.asyncio
async def test_no_match_surfaces_wrong_company_stash(mock_apollo, mock_hubspot):
    """No-match path should surface wrong_company_stash as alternate."""
    wrong_co = _make_person(
        first="Jane", last="Smith", org_name="OpenAI", domain="openai.com"
    )

    call_count = 0

    async def match_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return wrong_co
        return None

    mock_apollo.people_match = AsyncMock(side_effect=match_side_effect)
    mock_apollo.people_search.return_value = ([], 0)
    mock_apollo.resolve_company_domains.return_value = [("Google", "google.com")]
    from knotch_mcp.tools import _find_contact_by_details

    result = await _find_contact_by_details(
        "Jane", "Smith", "Google", None, None, mock_apollo, mock_hubspot
    )
    assert result.alternate_matches is not None
    assert len(result.alternate_matches) == 1
    assert result.alternate_matches[0]["company"] == "OpenAI"
    assert "lookup_contact" in result.next_step
