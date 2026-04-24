"""Tests for the deal_analysis tool and write tools."""

import pytest
from unittest.mock import AsyncMock

from knotch_mcp.deal_analysis import (
    _deal_analysis,
    _infer_role,
    _infer_role_from_title,
    _analyze_spiced,
    _assess_spiced_gap,
    _group_edits,
    _parse_signals,
    _resolve_stage_key,
    _build_contact_profile,
    STAGE_REQUIREMENTS,
)
from knotch_mcp.tools import _update_object, _associate_contact_to_deal
from knotch_mcp.models import (
    DealAnalysisResult,
    RecommendedEdit,
    RoleAssignment,
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


def _make_meeting(meeting_id="301", title="Discovery Call", contact_ids=None):
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

    # Default: deal found by ID
    client.get_deal = AsyncMock(return_value=_make_deal())
    client.search_deals = AsyncMock(return_value=[_make_deal()])

    # Pipeline with stages
    client.get_pipelines = AsyncMock(
        return_value=[
            {
                "id": "default",
                "label": "Sales Pipeline",
                "stages": [
                    {"id": "ipm", "label": "IPM Set", "metadata": {}},
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

    # 3 contacts on deal
    client.get_associations = AsyncMock(side_effect=_association_side_effect)

    # Batch read contacts
    client.batch_read = AsyncMock(side_effect=_batch_read_side_effect)

    # Company
    client.get_company = AsyncMock(return_value=_make_company())
    client.search_deals_by_company = AsyncMock(return_value=[])

    # Owners (internal team) — rep@knotch.com is internal
    client.get_owner_emails = AsyncMock(
        return_value={"rep@knotch.com", "manager@knotch.com"}
    )

    # Write methods
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
        # Meeting 301 has contact 201 (on deal) + 204 (gap candidate)
        if object_id == "301":
            return [{"toObjectId": "201"}, {"toObjectId": "204"}]
        # Meeting 302 has contact 202 (on deal)
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


class TestInferRoleFromTitle:
    def test_vp_title(self):
        role, conf = _infer_role_from_title("VP Marketing")
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_cto(self):
        role, conf = _infer_role_from_title("CTO")
        assert role == "Executive Sponsor"
        assert conf == "HIGH"

    def test_cfo(self):
        role, conf = _infer_role_from_title("CFO")
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_director(self):
        role, conf = _infer_role_from_title("Director of Engineering")
        assert role == "Technical Validator"
        assert conf == "MEDIUM"

    def test_director_generic(self):
        role, conf = _infer_role_from_title("Director of Partnerships")
        assert role == "Influencer"
        assert conf == "MEDIUM"

    def test_analyst(self):
        role, conf = _infer_role_from_title("Data Analyst")
        assert role == "End User"
        assert conf == "MEDIUM"

    def test_procurement(self):
        role, conf = _infer_role_from_title("Head of Procurement")
        assert role == "Blocker"
        assert conf == "MEDIUM"

    def test_legal_counsel(self):
        role, conf = _infer_role_from_title("General Counsel")
        assert role == "Blocker"
        assert conf == "MEDIUM"

    def test_it_manager(self):
        role, conf = _infer_role_from_title("IT Manager")
        assert role == "Technical Validator"
        assert conf == "MEDIUM"

    def test_none_title(self):
        role, conf = _infer_role_from_title(None)
        assert role is None
        assert conf == "LOW"

    def test_unknown_title(self):
        role, conf = _infer_role_from_title("Intern")
        assert role is None
        assert conf == "LOW"


class TestInferRole:
    def test_persona_marketing(self):
        role, conf = _infer_role(persona="Marketing Manager")
        assert role == "Influencer"
        assert conf == "HIGH"

    def test_persona_legal(self):
        role, conf = _infer_role(persona="Legal")
        assert role == "Blocker"
        assert conf == "HIGH"

    def test_persona_it_exact(self):
        role, conf = _infer_role(persona="IT")
        assert role == "Technical Validator"
        assert conf == "HIGH"

    def test_persona_technical(self):
        role, conf = _infer_role(persona="Technical")
        assert role == "Technical Validator"
        assert conf == "HIGH"

    def test_persona_sales(self):
        role, conf = _infer_role(persona="Sales")
        assert role == "Champion"
        assert conf == "HIGH"

    def test_seniority_csuite(self):
        role, conf = _infer_role(seniority="C-Suite")
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_seniority_vp(self):
        role, conf = _infer_role(seniority="VP")
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_seniority_manager(self):
        role, conf = _infer_role(seniority="Manager")
        assert role == "Champion"
        assert conf == "HIGH"

    def test_seniority_ic(self):
        role, conf = _infer_role(seniority="Individual Contributor")
        assert role == "End User"
        assert conf == "HIGH"

    def test_seniority_csuite_over_persona(self):
        role, conf = _infer_role(
            title="Marketing Coordinator", persona="Marketing", seniority="C-Suite"
        )
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_persona_over_title(self):
        role, conf = _infer_role(title="Manager of Widgets", persona="Legal")
        assert role == "Blocker"
        assert conf == "HIGH"

    def test_fallback_to_title(self):
        role, conf = _infer_role(title="VP Marketing")
        assert role == "Economic Buyer"
        assert conf == "HIGH"

    def test_no_data(self):
        role, conf = _infer_role()
        assert role is None
        assert conf == "LOW"


class TestParseSignals:
    def test_decision_maker(self):
        signals = _parse_signals("Spoke with the key decision-maker about next steps")
        roles = [s[0] for s in signals]
        assert "Economic Buyer" in roles

    def test_budget_signal(self):
        signals = _parse_signals("Discussed budget allocation for Q3")
        roles = [s[0] for s in signals]
        assert "Economic Buyer" in roles

    def test_technical_signal(self):
        signals = _parse_signals("Need to evaluate API integration capabilities")
        roles = [s[0] for s in signals]
        assert "Technical Validator" in roles

    def test_blocker_signal(self):
        signals = _parse_signals("Legal raised a concern about data residency")
        roles = [s[0] for s in signals]
        assert "Blocker" in roles

    def test_personnel_change(self):
        signals = _parse_signals("Lynn is no longer with the company")
        roles = [s[0] for s in signals]
        assert "PERSONNEL_CHANGE" in roles

    def test_champion_enthusiasm(self):
        signals = _parse_signals(
            "Really hoping there is an opportunity to bring Knotch on board"
        )
        roles = [s[0] for s in signals]
        assert "Champion" in roles

    def test_empty_text(self):
        assert _parse_signals("") == []

    def test_no_signals(self):
        assert _parse_signals("Thanks for the call, talk soon.") == []


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
        assert _resolve_stage_key("IPM Set") == "ipm"

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


# ── Integration tests: full analysis ──────────────────────────────


@pytest.mark.asyncio
async def test_deal_analysis_full(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    assert isinstance(result, DealAnalysisResult)
    assert result.deal_id == "100"
    assert result.deal_name == "Acme Corp K1 2026"
    assert result.stage_label == "Qualification"
    assert result.amount == "50000"
    assert result.deal_url is not None

    # Should have 3 contacts on the deal
    assert result.contact_count == 3
    assert len(result.deal_contacts) == 3

    # Should identify gap candidates (204 from meetings, 205 from emails)
    assert len(result.contacts_to_add) >= 1
    gap_ids = {c.contact_id for c in result.contacts_to_add}
    assert "204" in gap_ids
    assert "205" in gap_ids

    # Should have role assignments
    assert len(result.role_assignments) > 0

    # Should have stage gap analysis
    assert result.stage_gap_analysis is not None
    assert result.stage_gap_analysis.current_stage == "qualification"

    # Should have recommended edits
    assert len(result.recommended_edits) > 0

    # Activity summary
    assert result.activity_summary["meetings"] == 2
    assert result.activity_summary["emails"] == 1
    assert result.company_name == "Acme Corp"


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
    # Should NOT fetch contacts/meetings/emails for closed deals
    mock_hubspot.get_associations.assert_not_called()


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

    assert result.contact_count == 0
    assert result.stage_gap_analysis is not None
    assert result.stage_gap_analysis.single_threaded is True


@pytest.mark.asyncio
async def test_deal_analysis_role_inference(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    role_map = {ra.name: ra.recommended_role for ra in result.role_assignments}
    # VP Marketing -> Economic Buyer
    assert role_map.get("Jane Smith") == "Economic Buyer"
    # CTO -> Executive Sponsor
    assert role_map.get("Bob Jones") == "Executive Sponsor"
    # Analyst -> End User
    assert role_map.get("Alice Chen") == "End User"


@pytest.mark.asyncio
async def test_deal_analysis_gap_candidate_priority(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    for contact in result.contacts_to_add:
        assert contact.priority in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        assert len(contact.evidence) > 0


@pytest.mark.asyncio
async def test_deal_analysis_stage_gap(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)

    gap = result.stage_gap_analysis
    assert gap is not None
    # Qualification requires Champion
    assert "Champion" in gap.required_roles
    assert gap.minimum_contacts >= 2


# ── SPICED analysis tests ────────────────────────────────────────


class TestAnalyzeSpiced:
    def test_pain_from_meeting_text(self):
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=["The team is struggling with manual reporting processes"],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        pain = next(e for e in result.elements if e.element == "P")
        assert pain.status in ("strong", "partial")
        assert len(pain.evidence) > 0

    def test_impact_from_amount(self):
        result = _analyze_spiced(
            deal_props={"amount": "50000"},
            company_name=None,
            meeting_texts=[],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        impact = next(e for e in result.elements if e.element == "I")
        assert any("$50000" in e for e in impact.evidence)

    def test_situation_from_company(self):
        result = _analyze_spiced(
            deal_props={"description": "Enterprise content analytics"},
            company_name="Acme Corp",
            meeting_texts=[],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[{"id": "200"}],
        )
        situation = next(e for e in result.elements if e.element == "S")
        assert situation.status == "strong"
        assert any("Acme" in e for e in situation.evidence)

    def test_decision_process_from_roles(self):
        roles = [
            RoleAssignment(
                contact_id="1",
                name="A",
                recommended_role="Economic Buyer",
                on_deal=True,
            ),
            RoleAssignment(
                contact_id="2",
                name="B",
                recommended_role="Blocker",
                on_deal=True,
            ),
            RoleAssignment(
                contact_id="3",
                name="C",
                recommended_role="Champion",
                on_deal=True,
            ),
        ]
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=[],
            email_texts=[],
            role_assignments=roles,
            deal_age_days=None,
            other_open_deals=[],
        )
        dp = next(e for e in result.elements if e.element == "D")
        assert dp.status == "strong"

    def test_critical_event_from_email_text(self):
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=[],
            email_texts=["We need this done before Q2 deadline"],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        ce = next(e for e in result.elements if e.element == "C")
        assert ce.status in ("strong", "partial")

    def test_overall_score_weak_when_empty(self):
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=[],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        assert result.overall_score == "weak"
        assert result.missing_count == 6

    def test_overall_score_with_mixed_signals(self):
        roles = [
            RoleAssignment(
                contact_id="1",
                name="A",
                recommended_role="Economic Buyer",
                on_deal=True,
            ),
            RoleAssignment(
                contact_id="2",
                name="B",
                recommended_role="Champion",
                on_deal=True,
            ),
            RoleAssignment(
                contact_id="3",
                name="C",
                recommended_role="Technical Validator",
                on_deal=True,
            ),
        ]
        result = _analyze_spiced(
            deal_props={"amount": "75000", "description": "Big deal"},
            company_name="Test Co",
            meeting_texts=[
                "Currently using a manual process that is time-consuming and inefficient"
            ],
            email_texts=["Need to evaluate alternatives before the Q3 deadline"],
            role_assignments=roles,
            deal_age_days=60,
            other_open_deals=[],
        )
        assert result.overall_score in ("strong", "partial")
        assert result.missing_count < 6

    def test_recommendations_for_missing_elements(self):
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=[],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        assert len(result.recommendations) > 0
        assert any("[" in r for r in result.recommendations)

    def test_all_six_elements_present(self):
        result = _analyze_spiced(
            deal_props={},
            company_name=None,
            meeting_texts=[],
            email_texts=[],
            role_assignments=[],
            deal_age_days=None,
            other_open_deals=[],
        )
        elements = [e.element for e in result.elements]
        assert elements == ["S", "P", "I", "C", "E", "D"]


@pytest.mark.asyncio
async def test_deal_analysis_includes_spiced(mock_hubspot):
    result = await _deal_analysis("100", mock_hubspot)
    assert result.spiced_analysis is not None
    assert len(result.spiced_analysis.elements) == 6
    assert result.spiced_analysis.overall_score in ("strong", "partial", "weak")


@pytest.mark.asyncio
async def test_deal_analysis_persona_seniority_in_evidence(mock_hubspot):
    """Contacts with persona/seniority should show them in role evidence."""

    def _batch_with_persona(object_type, ids, properties):
        if object_type == "contacts":
            records = {
                "201": _make_contact("201", "Jane", "Smith", "VP Marketing"),
                "202": _make_contact("202", "Bob", "Jones", "CTO"),
                "203": _make_contact("203", "Alice", "Chen", "Analyst"),
            }
            records["201"]["properties"]["hs_persona"] = "Executive"
            records["201"]["properties"]["seniority_level__knotch_"] = "VP"
            return [records[cid] for cid in ids if cid in records]
        return _batch_read_side_effect(object_type, ids, properties)

    mock_hubspot.batch_read = AsyncMock(side_effect=_batch_with_persona)
    result = await _deal_analysis("100", mock_hubspot)

    jane_role = next(
        (ra for ra in result.role_assignments if ra.name == "Jane Smith"), None
    )
    assert jane_role is not None
    assert jane_role.recommended_role == "Economic Buyer"
    assert jane_role.confidence == "HIGH"
    assert any("Persona" in e or "Seniority" in e for e in jane_role.evidence)


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


# ── Internal contact filtering ────────────────────────────────────


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

    @pytest.mark.asyncio
    async def test_internal_contact_excluded_from_roles(self, mock_hubspot):
        """Internal team members should not get buyer role recommendations."""
        mock_hubspot.get_owner_emails = AsyncMock(return_value={"lee@knotch.com"})

        def custom_batch_read(object_type, ids, properties):
            if object_type == "contacts":
                records = {
                    "201": _make_contact(
                        "201", "Jane", "Smith", "VP Marketing", "jane@acme.com"
                    ),
                    "204": {
                        "id": "204",
                        "properties": {
                            "firstname": "Lee",
                            "lastname": "Fine",
                            "email": "lee@knotch.com",
                            "jobtitle": "Account Executive",
                            "company": "Knotch",
                            "hs_buying_role": None,
                            "hs_persona": None,
                            "seniority_level__knotch_": None,
                            "hubspot_owner_id": "owner-2",
                            "notes_last_updated": "2026-04-20",
                            "num_notes": "413",
                            "hs_linkedin_url": None,
                            "hs_lead_status": None,
                            "lifecyclestage": None,
                            "phone": None,
                        },
                    },
                }
                return [records[cid] for cid in ids if cid in records]
            if object_type == "meetings":
                return [_make_meeting("301")]
            return []

        mock_hubspot.batch_read = AsyncMock(side_effect=custom_batch_read)

        def custom_assoc(object_type, object_id, to_type):
            if object_type == "deals" and to_type == "contacts":
                return [{"toObjectId": "201"}]
            if object_type == "deals" and to_type == "meetings":
                return [{"toObjectId": "301"}]
            if object_type == "meetings" and to_type == "contacts":
                return [{"toObjectId": "201"}, {"toObjectId": "204"}]
            if object_type == "deals" and to_type == "emails":
                return []
            if object_type == "deals" and to_type == "companies":
                return [{"toObjectId": "501"}]
            return []

        mock_hubspot.get_associations = AsyncMock(side_effect=custom_assoc)

        result = await _deal_analysis("100", mock_hubspot)

        role_names = [ra.name for ra in result.role_assignments]
        assert "Lee Fine" not in role_names
        assert "Jane Smith" in role_names

        add_names = [c.name for c in result.contacts_to_add]
        assert "Lee Fine" not in add_names


# ── SPICED gap assessment ──────────────────────────────────────────


class TestSpicedGapAssessment:
    def test_both_deal_and_activity(self):
        gap = _assess_spiced_gap(
            "S", ["Company identified"], ["current state discussed"]
        )
        assert "both support" in gap

    def test_activity_only(self):
        gap = _assess_spiced_gap("P", [], ["pain point identified"])
        assert "deal record doesn't reflect" in gap

    def test_deal_only(self):
        gap = _assess_spiced_gap("I", ["Deal amount: $50000"], [])
        assert "no supporting signals" in gap.lower() or "Validate" in gap

    def test_neither(self):
        gap = _assess_spiced_gap("C", [], [])
        assert "Needs discovery" in gap

    def test_spiced_element_has_separate_evidence(self):
        spiced = _analyze_spiced(
            deal_props={"amount": "50000", "description": "Enterprise deal"},
            company_name="Acme Corp",
            meeting_texts=["We're struggling with manual processes and pain points"],
            email_texts=[],
            role_assignments=[],
            deal_age_days=90,
            other_open_deals=[],
        )
        s_elem = next(e for e in spiced.elements if e.element == "S")
        assert len(s_elem.deal_record_evidence) > 0
        assert s_elem.gap_assessment != ""

        p_elem = next(e for e in spiced.elements if e.element == "P")
        assert len(p_elem.activity_evidence) > 0
        assert "deal record doesn't reflect" in p_elem.gap_assessment

        i_elem = next(e for e in spiced.elements if e.element == "I")
        assert any("$50000" in e for e in i_elem.deal_record_evidence)

    @pytest.mark.asyncio
    async def test_deal_analysis_spiced_has_gap_assessment(self, mock_hubspot):
        result = await _deal_analysis("100", mock_hubspot)
        assert result.spiced_analysis is not None
        for elem in result.spiced_analysis.elements:
            assert elem.gap_assessment != ""
            assert isinstance(elem.deal_record_evidence, list)
            assert isinstance(elem.activity_evidence, list)


# ── Edit grouping ──────────────────────────────────────────────────


class TestEditGrouping:
    def test_groups_associations_and_roles(self):
        edits = [
            RecommendedEdit(
                edit_type="associate_contact",
                target_id="204",
                target_name="Lynn Teo",
                field="deal_association",
                new_value="100",
                reason="6 meetings",
            ),
            RecommendedEdit(
                edit_type="associate_contact",
                target_id="205",
                target_name="Jill Perlberg",
                field="deal_association",
                new_value="100",
                reason="6 meetings",
            ),
            RecommendedEdit(
                edit_type="set_buyer_role",
                target_id="201",
                target_name="Jane Smith",
                field="hs_buying_role",
                new_value="Economic Buyer",
                reason="VP Marketing",
            ),
            RecommendedEdit(
                edit_type="set_buyer_role",
                target_id="202",
                target_name="Bob Jones",
                field="hs_buying_role",
                new_value="Technical Validator",
                reason="CTO",
            ),
        ]
        groups = _group_edits(edits)
        assert len(groups) == 2

        assoc_group = next(g for g in groups if g.category == "associations")
        assert assoc_group.count == 2
        assert "Lynn Teo" in assoc_group.prompt
        assert "2 contacts" in assoc_group.prompt

        role_group = next(g for g in groups if g.category == "role_assignments")
        assert role_group.count == 2
        assert "buyer roles" in role_group.prompt.lower()

    def test_empty_edits_returns_empty_groups(self):
        groups = _group_edits([])
        assert groups == []

    def test_only_roles(self):
        edits = [
            RecommendedEdit(
                edit_type="set_buyer_role",
                target_id="201",
                target_name="Jane Smith",
                field="hs_buying_role",
                new_value="Champion",
                reason="Title",
            ),
        ]
        groups = _group_edits(edits)
        assert len(groups) == 1
        assert groups[0].category == "role_assignments"

    @pytest.mark.asyncio
    async def test_deal_analysis_has_edit_groups(self, mock_hubspot):
        result = await _deal_analysis("100", mock_hubspot)
        assert isinstance(result.edit_groups, list)
        if result.recommended_edits:
            assert len(result.edit_groups) > 0
            for group in result.edit_groups:
                assert group.category in (
                    "associations",
                    "role_assignments",
                    "other_updates",
                )
                assert group.count > 0
                assert group.prompt != ""
