"""Pydantic models for all MCP tool inputs and outputs."""

from __future__ import annotations

from pydantic import BaseModel


# ── Shared / Core ──────────────────────────────────────────────────


class ContactResult(BaseModel):
    """Unified contact record returned by discovery and enrichment tools."""

    name: str
    title: str | None = None
    company: str | None = None
    email: str | None = None
    email_status: str | None = None
    linkedin_url: str | None = None
    location: str | None = None
    apollo_id: str | None = None
    phone: str | None = None
    phone_type: str | None = None
    company_domain: str | None = None
    match_method: str | None = None
    alternate_matches: list[dict] | None = None
    sources: list[str] = []
    hubspot_status: str = "not_checked"
    hubspot_contact_id: str | None = None
    hubspot_url: str | None = None
    gaps: list[str] = []
    suggested_actions: list[str] = []
    next_step: str | None = None
    confidence: str | None = None
    warnings: list[str] = []


# ── Tool Inputs ────────────────────────────────────────────────────


class FindContactInput(BaseModel):
    """Input for find_contact tool — search by name + company."""

    first_name: str
    last_name: str
    company: str
    email: str | None = None
    linkedin_url: str | None = None


class FindContactsByRoleInput(BaseModel):
    """Input for find_contacts_by_role tool — search by titles at a company."""

    titles: list[str]
    company: str
    seniority: str | None = None
    limit: int = 3


class FindPhoneInput(BaseModel):
    """Input for find_phone tool — accepts any identifier to look up a phone."""

    apollo_id: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    name: str | None = None


class EnrichContactInput(BaseModel):
    """Input for enrich_contact tool — enrich an existing HubSpot contact."""

    hubspot_contact_id: str


class AddToHubSpotInput(BaseModel):
    """Input for add_to_hubspot tool — create or update a HubSpot contact."""

    first_name: str
    last_name: str
    email: str | None = None
    title: str | None = None
    company: str | None = None
    company_domain: str | None = None
    linkedin_url: str | None = None
    location: str | None = None
    phone: str | None = None
    apollo_id: str | None = None


class ClayEnrichInput(BaseModel):
    """Input for clay_enrich tool — enrich via Clay API."""

    first_name: str
    last_name: str
    company_domain: str
    requested_data: list[str] = ["phone", "email"]


# ── Tool Outputs ───────────────────────────────────────────────────


class FindContactsResult(BaseModel):
    """Output for find_contact and find_contacts_by_role tools."""

    candidates: list[ContactResult]
    total_available: int
    warnings: list[str] = []
    next_step: str | None = None


class FindPhoneResult(BaseModel):
    """Output for find_phone tool."""

    phone: str | None = None
    phone_type: str | None = None
    source: str = "apollo"
    confidence: str | None = None
    found: bool = False
    suggested_action: str | None = None


class EnrichContactResult(BaseModel):
    """Output for enrich_contact tool."""

    filled: dict[str, str] = {}
    already_populated: list[str] = []
    not_found: list[str] = []
    sources_used: list[str] = []


class AddToHubSpotResult(BaseModel):
    """Output for add_to_hubspot tool."""

    hubspot_contact_id: str = ""
    hubspot_url: str = ""
    action: str = ""
    company_associated: bool = False
    company_created: bool = False
    company_name: str | None = None
    error: str | None = None


class ClayEnrichResult(BaseModel):
    """Output for clay_enrich tool."""

    enriched_fields: dict[str, str] = {}
    source: str = "clay"
    credits_used: int = 0
    task_status: str = "completed"
    next_step: str | None = None
    warnings: list[str] = []


# ── Deal Analysis ─────────────────────────────────────────────────


class DealContactProfile(BaseModel):
    contact_id: str
    name: str
    title: str | None = None
    email: str | None = None
    company: str | None = None
    current_buying_role: str | None = None
    persona: str | None = None
    seniority: str | None = None
    linkedin_url: str | None = None
    owner_id: str | None = None
    lead_status: str | None = None
    lifecycle_stage: str | None = None
    notes_count: int = 0
    last_activity: str | None = None
    engagement_level: str = "unknown"
    on_deal: bool = True
    is_internal: bool = False


class AttendeeInfo(BaseModel):
    contact_id: str
    name: str
    title: str | None = None
    email: str | None = None
    on_deal: bool = False


class MeetingDetail(BaseModel):
    id: str
    title: str | None = None
    start_time: str | None = None
    outcome: str | None = None
    attendee_ids: list[str] = []
    attendees: list[AttendeeInfo] = []
    body: str = ""


class EmailDetail(BaseModel):
    id: str
    subject: str | None = None
    timestamp: str | None = None
    direction: str | None = None
    from_email: str | None = None
    to_emails: list[str] = []
    associated_contact_ids: list[str] = []
    associated_contacts: list[AttendeeInfo] = []
    body: str = ""


class DealAnalysisResult(BaseModel):
    deal_id: str
    deal_name: str
    deal_stage: str
    stage_label: str
    pipeline: str | None = None
    amount: str | None = None
    close_date: str | None = None
    owner_id: str | None = None
    deal_age_days: int | None = None
    deal_url: str | None = None
    deal_description: str | None = None
    company_name: str | None = None
    company_id: str | None = None
    other_open_deals: list[dict] = []
    stage_requirements: dict = {}
    deal_contacts: list[DealContactProfile] = []
    gap_contacts: list[DealContactProfile] = []
    internal_emails: list[str] = []
    meetings: list[MeetingDetail] = []
    emails: list[EmailDetail] = []
    activity_summary: dict = {}
    warnings: list[str] = []


# ── Write Tool Results ────────────────────────────────────────────


class UpdateResult(BaseModel):
    object_type: str
    object_id: str
    updated_properties: list[str] = []
    success: bool = True
    error: str | None = None


class AssociateResult(BaseModel):
    from_type: str
    from_id: str
    to_type: str
    to_id: str
    success: bool = True
    error: str | None = None
