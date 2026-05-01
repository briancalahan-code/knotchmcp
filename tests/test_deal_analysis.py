"""Tests for the deal_analysis data assembler and write tools."""

import pytest
from unittest.mock import AsyncMock

from knotch_mcp.deal_analysis import (
    _deal_analysis,
    _resolve_stage_key,
    _build_contact_profile,
    STAGE_REQUIREMENTS,
)
from knotch_mcp.tools import _update_object, _associate_contact_to_deal
from knotch_mcp.models import (
    DealAnalysisResult,
    MeetingDetail,
    EmailDetail,
    AttendeeInfo,
    UpdateResult,
    AssociateResult,
)


# ── Fixtures ──────────────────────────────────────────────────────


def _make_deal(
    deal_id="100",
    name="Acme Corp K1 2026",
    stage="qualificationlead",
    pipeline="default",
    amount="50000",
):
    return {
        "id": deal_id,
        "properties": {
            "dealname": name,
            "dealstage": stage,
            "pipeline": pipeline,
            "amount": amount,
            "closedate": "2026-06-30",
            "hubspot_owner_id": "owner-1",
            "hs_lastmodifieddate": "2026-04-20T10:00:00Z",
            "num_associated_contacts": "3",
            "description": "Enterprise deal",
            "notes_last_updated": "2026-04-15",
            "num_contacted_notes": "5",
            "createdate": "2026-01-15T00:00:00Z",
        },
    }


def _make_contact(
    contact_id="201",
    first="Jane",
    last="Smith",
    title="VP Marketing",
    email="jane@acme.com",
    buying_role=None,
    notes_count="3",
):
    return {
        "id": contact_id,
        "properties": {
            "firstname": first,
            "lastname": last,
            "email": email,
            "jobtitle": title,
            "company": "Acme Corp",
            "hs_buying_role": buying_role,
            "hs_persona": None,
            "seniority_level__knotch_": None,
            "hubspot_owner_id": "owner-1",
            "notes_last_updated": "2026-04-10",
            "num_notes": notes_count,
            "hs_linkedin_url": f"https://linkedin.com/in/{first.lower()}{last.lower()}",
            "hs_lead_status": "OPEN",
            "lifecyclestage": "opportunity",
            "phone": "+14155550100",
        },
    }


def _make_meeting(meeting_id="301", title="Discovery Call"):
    return {
        "id": meeting_id,
        "properties": {
            "hs_meeting_title": title,
            "hs_meeting_start_time": "2026-04-10T14:00:00Z",
            "hs_meeting_outcome": "COMPLETED",
            "hs_meeting_body": "Discussion about budget and pricing with the key decision-maker.",
        },
    }


def _make_email(email_id="401", subject="Re: Proposal", from_email="jane@acme.com"):
    return {
        "id": email_id,
        "properties": {
            "hs_email_subject": subject,
            "hs_timestamp": "2026-04-18T09:00:00Z",
            "hs_email_direction": "INCOMING_EMAIL",
            "hs_email_from_email": from_email,
            "hs_email_to_email": "rep@knotch.com",
            "hs_email_text": "Really hoping there is an opportunity to bring Knotch on board.",
        },
    }


def _make_company(company_id="501", name="Acme Corp"):
    return {
        "id": company_id,
        "properties": {
            "name": name,
            "domain": "acme.com",
            "industry": "Technology",
            "numberofemployees": "5000",
            "annualrevenue": "500000000",
            "hubspot_owner_id": "owner-1",
            "description": "Enterprise tech company",
        },
    }


@pytest.fixture
def mock_hubspot():
    client = AsyncMock()
    client.build_deal_url = lambda did: (
        f"https://app.hubspot.com/contacts/12345/deal/{did}"
    )
    client.build_contact_url = lambda cid: (
        f"https://app.hubspot.com/contacts/12345/contact/{cid}"
    )

    client.get_deal = AsyncMock(return_value=_make_deal())
    client.search_deals = AsyncMock(return_value=[_make_deal()])

    client.get_pipelines = AsyncMock(
        return_value=[
            {
                "id": "default",
                "label": "Sales Pipeline",
                "stages": [
                    {"id": "ipm", "label": "IPM", "metadata": {}},
                    {
                        "id": "qualificationlead",
                        "label": "Qualification",
                        "metadata": {},
                    },
                    {"id": "consensus", "label": "Consensus", "metadata": {}},
                    {"id": "proposal", "label": "Proposal", "metadata": {}},
                    {"id": "procurement", "label": "Procurement", "metadata": {}},
                    {
                        "id": "closedwon",
                        "label": "Closed Won",
                        "metadata": {"isClosed": "true"},
                    },
                    {
                        "id": "closedlost",
                        "label": "Closed Lost",
                        "metadata": {"isClosed": "true"},
                    },
                ],
            }
        ]
    )

    client.get_associations = AsyncMock(side_effect=_association_side_effect)
    client.batch_read = AsyncMock(side_effect=_batch_read_side_effect)
    client.get_company = AsyncMock(return_value=_make_company())
    client.search_deals_by_company = AsyncMock(return_value=[])
    client.get_owner_emails = AsyncMock(
        return_value={"rep@knotch.com", "manager@knotch.com"}
    )

    client.update_contact = AsyncMock(return_value={"id": "201", "properties": {}})
    client.update_deal = AsyncMock(return_value={"id": "100", "properties": {}})
    client.update_company = AsyncMock(return_value={"id": "501", "properties": {}})
    client.associate_objects = AsyncMock(return_value=None)

    return client


def _association_side_effect(object_type, object_id, to_type):
    if object_type == "deals" and to_type == "contacts":
        return [{"toObjectId": "201"}, {"toObjectId": "202"}, {"toObjectId": "203"}]
    if object_type == "deals" and to_type == "meetings":
        return [{"toObjectId": "301"}, {"toObjectId": "302"}]
    if object_type == "deals" and to_type == "emails":
        return [{"toObjectId": "401"}]
    if object_type == "deals" and to_type == "companies":
        return [{"toObjectId": "501"}]
    if object_type == "meetings" and to_type == "contacts":
        if object_id == "301":
            return [{"toObjectId": "201"}, {"toObjectId": "204"}]
        return [{"toObjectId": "202"}]
    if object_type == "emails" and to_type == "contacts":
        return [{"toObjectId": "201"}, {"toObjectId": "205"}]
    return []


def _batch_read_side_effect(object_type, ids, properties):
    if object_type == "contacts":
        records = {
            "201": _make_contact(
                "201", "Jane", "Smith", "VP Marketing", "jane@acme.com", notes_count="5"
            ),
            "202": _make_contact(
                "202", "Bob", "Jones", "CTO", "bob@acme.com", notes_count="3"
            ),
            "203": _make_contact(
                "203", "Alice", "Chen", "Analyst", "alice@acme.com", notes_count="0"
            ),
            "204": _make_contact(
                "204", "Tom", "Davis", "Director of IT", "tom@acme.com", notes_count="1"
            ),
            "205": _make_contact(
                "205",
                "Sara",
                "Lee",
                "Head of Procurement",
                "sara@acme.com",
                notes_count="0",
            ),
        }
        return [records[cid] for cid in ids if cid in records]
    if object_type == "meetings":
        meetings = {
            "301": _make_meeting("301", "Discovery Call"),
            "302": _make_meeting("302", "Technical Deep Dive"),
        }
        return [meetings[mid] for mid in ids if mid in meetings]
    if object_type == "emails":
        emails = {
            "401": _make_email("401"),
        }
        return [emails[eid] for eid in ids if eid in emails]
    return []


# ── Unit tests: helper functions ──────────────────────────────────


class TestResolveStageKey:
    def test_qualification(self):
        assert _resolve_stage_key("Qualification") == "qualification"

    def test_consensus(self):
        assert _resolve_stage_key("Consensus") == "consensus"

    def test_proposal(self):
        assert _resolve_stage_key("Proposal") == "proposal"

    def test_procurement(self):
        assert _resolve_stage_key("Procurement") == "procurement"

    def test_ipm(self):
        assert _resolve_stage_key("IPM") == "ipm"

    def test_closed_won(self):
        assert _resolve_stage_key("Closed Won") is None

    def test_closed_lost(self):
        assert _resolve_stage_key("Closed Lost") is None

    def test_unknown_defaults_to_qualification(self):
        assert _resolve_stage_key("Some Custom Stage") == "qualification"


class TestBuildContactProfile:
    def test_basic_profile(self):
        record = _make_contact("201", "Jane", "Smith", "VP Marketing", "jane@acme.com")
        profile = _build_contact_profile(record)
        assert profile.contact_id == "201"
        assert profile.name == "Jane Smith"
        assert profile.title == "VP Marketing"
        assert profile.email == "jane@acme.com"
        assert profile.on_deal is True

    def test_engagement_high(self):
        record = _make_contact("201", notes_count="10")
        profile = _build_contact_profile(record)
        assert profile.engagement_level == "high"

    def test_engagement_none(self):
        record = _make_contact("201", notes_count="0")
        record["properties"]["notes_last_updated"] = None
        profile = _build_contact_profile(record)
        assert profile.engagement_level == "none"

    def test_off_deal(self):
        record = _make_contact("204")
        profile = _build_contact_profile(record, on_deal=False)
        assert profile.on_deal is False


class TestInternalContactFiltering:
    def test_build_profile_marks_internal(self):
        record = _make_contact("999", "Lee", "Fine", "AE", "lee@knotch.com")
        profile = _build_contact_profile(
            record, internal_emails={"lee@knotch.com", "manager@knotch.com"}
        )
        assert profile.is_internal is True

    def test_build_profile_external_not_flagged(self):
        record = _make_contact("201", "Jane", "Smith", "VP Marketing", "jane@acme.com")
        profile = _build_contact_profile(record, internal_emails={"lee@knotch.com"})
        assert profile.is_internal is False

    def test_build_profile_no_internal_emails(self):
        record = _make_contact("201", "Jane", "Smith", "VP Marketing", "jane@acme.com")
        profile = _build_contact_profile(record)
        assert profile.is_internal is False


# ── Integration tests: data assembly ──────────────────────────────


@pytest.mark.asyncio
async def test_deal_analysis_returns_deal_metadata(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert isinstance(result, DealAnalysisResult)
    assert result.deal_id == "100"
    assert result.deal_name == "Acme Corp K1 2026"
    assert result.stage_label == "Qualification"
    assert result.amount == "50000"
    assert result.deal_url is not None
    assert result.deal_description == "Enterprise deal"
    assert result.company_name == "Acme Corp"
    assert result.company_id == "501"


@pytest.mark.asyncio
async def test_deal_analysis_returns_contacts(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert len(result.deal_contacts) == 3
    names = {c.name for c in result.deal_contacts}
    assert "Jane Smith" in names
    assert "Bob Jones" in names
    assert "Alice Chen" in names

    for c in result.deal_contacts:
        assert c.on_deal is True


@pytest.mark.asyncio
async def test_deal_analysis_returns_gap_contacts(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    gap_ids = {c.contact_id for c in result.gap_contacts}
    assert "204" in gap_ids
    assert "205" in gap_ids

    for gc in result.gap_contacts:
        assert gc.on_deal is False


@pytest.mark.asyncio
async def test_deal_analysis_returns_meetings_with_bodies(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert len(result.meetings) == 2
    m1 = next(m for m in result.meetings if m.id == "301")
    assert isinstance(m1, MeetingDetail)
    assert m1.title == "Discovery Call"
    assert m1.start_time is not None
    assert m1.outcome == "COMPLETED"
    assert "budget" in m1.body.lower()


@pytest.mark.asyncio
async def test_deal_analysis_resolves_meeting_attendees(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    m1 = next(m for m in result.meetings if m.id == "301")
    assert len(m1.attendees) == 2
    attendee_names = {a.name for a in m1.attendees}
    assert "Jane Smith" in attendee_names
    assert "Tom Davis" in attendee_names

    jane = next(a for a in m1.attendees if a.name == "Jane Smith")
    assert jane.on_deal is True
    tom = next(a for a in m1.attendees if a.name == "Tom Davis")
    assert tom.on_deal is False


@pytest.mark.asyncio
async def test_deal_analysis_returns_emails_with_bodies(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert len(result.emails) == 1
    e1 = result.emails[0]
    assert isinstance(e1, EmailDetail)
    assert e1.subject == "Re: Proposal"
    assert e1.from_email == "jane@acme.com"
    assert "rep@knotch.com" in e1.to_emails
    assert "Knotch on board" in e1.body


@pytest.mark.asyncio
async def test_deal_analysis_resolves_email_contacts(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    e1 = result.emails[0]
    assert len(e1.associated_contacts) == 2
    contact_names = {c.name for c in e1.associated_contacts}
    assert "Jane Smith" in contact_names
    assert "Sara Lee" in contact_names


@pytest.mark.asyncio
async def test_deal_analysis_includes_stage_requirements(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert result.stage_requirements is not None
    assert "required_roles" in result.stage_requirements
    assert "recommended_roles" in result.stage_requirements
    assert "min_contacts" in result.stage_requirements
    assert "Champion" in result.stage_requirements["required_roles"]


@pytest.mark.asyncio
async def test_deal_analysis_includes_internal_emails(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert isinstance(result.internal_emails, list)
    assert "rep@knotch.com" in result.internal_emails
    assert "manager@knotch.com" in result.internal_emails


@pytest.mark.asyncio
async def test_deal_analysis_includes_activity_summary(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert result.activity_summary["meetings"] == 2
    assert result.activity_summary["contacts_on_deal"] == 3
    assert result.activity_summary["gap_contacts"] == 2


@pytest.mark.asyncio
async def test_deal_analysis_by_name(mock_hubspot):
    result = await _deal_analysis("Acme Corp", mock_hubspot)

    assert isinstance(result, DealAnalysisResult)
    assert result.deal_name == "Acme Corp K1 2026"
    mock_hubspot.search_deals.assert_called_once_with("Acme Corp")


@pytest.mark.asyncio
async def test_deal_analysis_closed_deal(mock_hubspot):
    mock_hubspot.get_deal = AsyncMock(return_value=_make_deal(stage="closedwon"))
    mock_hubspot.get_pipelines = AsyncMock(
        return_value=[
            {
                "id": "default",
                "stages": [
                    {
                        "id": "closedwon",
                        "label": "Closed Won",
                        "metadata": {"isClosed": "true"},
                    }
                ],
            }
        ]
    )

    result = await _deal_analysis("100", mock_hubspot)

    assert "closed" in result.warnings[0].lower() or "Closed" in result.warnings[0]
    assert len(result.meetings) == 0
    assert len(result.emails) == 0


@pytest.mark.asyncio
async def test_deal_analysis_not_found(mock_hubspot):
    mock_hubspot.get_deal = AsyncMock(side_effect=Exception("404 Not Found"))
    mock_hubspot.search_deals = AsyncMock(return_value=[])

    result = await _deal_analysis("nonexistent", mock_hubspot)

    assert len(result.warnings) > 0
    assert "not found" in result.warnings[0].lower()


@pytest.mark.asyncio
async def test_deal_analysis_no_contacts(mock_hubspot):
    mock_hubspot.get_associations = AsyncMock(return_value=[])

    result = await _deal_analysis("100", mock_hubspot)

    assert len(result.deal_contacts) == 0
    assert any("SINGLE-THREADED" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_deal_analysis_single_threaded_warning(mock_hubspot):
    def single_contact_assoc(object_type, object_id, to_type):
        if object_type == "deals" and to_type == "contacts":
            return [{"toObjectId": "201"}]
        if object_type == "deals" and to_type == "companies":
            return [{"toObjectId": "501"}]
        return []

    mock_hubspot.get_associations = AsyncMock(side_effect=single_contact_assoc)

    result = await _deal_analysis("100", mock_hubspot)

    assert any("SINGLE-THREADED" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_deal_analysis_below_stage_minimum_warning(mock_hubspot):
    def single_contact_assoc(object_type, object_id, to_type):
        if object_type == "deals" and to_type == "contacts":
            return [{"toObjectId": "201"}]
        if object_type == "deals" and to_type == "companies":
            return [{"toObjectId": "501"}]
        return []

    mock_hubspot.get_associations = AsyncMock(side_effect=single_contact_assoc)

    result = await _deal_analysis("100", mock_hubspot)

    assert any("below stage minimum" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_deal_analysis_other_open_deals(mock_hubspot):
    mock_hubspot.search_deals_by_company = AsyncMock(
        return_value=[
            _make_deal(),
            _make_deal(deal_id="200", name="Acme Corp K2 2026"),
        ]
    )

    result = await _deal_analysis("100", mock_hubspot)

    assert len(result.other_open_deals) == 1
    assert result.other_open_deals[0]["id"] == "200"


@pytest.mark.asyncio
async def test_deal_analysis_deal_age(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert result.deal_age_days is not None
    assert result.deal_age_days > 0


# ── Write tool tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_contact(mock_hubspot):
    result = await _update_object(
        "contacts", "201", {"hs_buying_role": "Champion"}, mock_hubspot
    )
    assert isinstance(result, UpdateResult)
    assert result.success is True
    assert result.object_type == "contacts"
    assert "hs_buying_role" in result.updated_properties
    mock_hubspot.update_contact.assert_called_once_with(
        "201", {"hs_buying_role": "Champion"}
    )


@pytest.mark.asyncio
async def test_update_deal(mock_hubspot):
    result = await _update_object("deals", "100", {"amount": "75000"}, mock_hubspot)
    assert result.success is True
    assert result.object_type == "deals"
    mock_hubspot.update_deal.assert_called_once_with("100", {"amount": "75000"})


@pytest.mark.asyncio
async def test_update_company(mock_hubspot):
    result = await _update_object(
        "companies", "501", {"industry": "SaaS"}, mock_hubspot
    )
    assert result.success is True
    mock_hubspot.update_company.assert_called_once_with("501", {"industry": "SaaS"})


@pytest.mark.asyncio
async def test_update_rejected_object_type(mock_hubspot):
    result = await _update_object("workflows", "999", {"name": "test"}, mock_hubspot)
    assert result.success is False
    assert "Write access limited" in result.error


@pytest.mark.asyncio
async def test_update_handles_exception(mock_hubspot):
    mock_hubspot.update_contact = AsyncMock(side_effect=Exception("API error"))
    result = await _update_object(
        "contacts", "201", {"email": "new@test.com"}, mock_hubspot
    )
    assert result.success is False
    assert "API error" in result.error


@pytest.mark.asyncio
async def test_associate_contact_to_deal(mock_hubspot):
    result = await _associate_contact_to_deal("204", "100", mock_hubspot)
    assert isinstance(result, AssociateResult)
    assert result.success is True
    assert result.from_type == "contacts"
    assert result.to_type == "deals"
    mock_hubspot.associate_objects.assert_called_once_with(
        "contacts", "204", "deals", "100"
    )


@pytest.mark.asyncio
async def test_associate_handles_exception(mock_hubspot):
    mock_hubspot.associate_objects = AsyncMock(side_effect=Exception("403 Forbidden"))
    result = await _associate_contact_to_deal("204", "100", mock_hubspot)
    assert result.success is False
    assert "403" in result.error
