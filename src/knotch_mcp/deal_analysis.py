"""Deal data assembler — parallel HubSpot data fetcher for deal analysis.

Given a HubSpot deal, fetches all associated contacts, meetings (with full
bodies), emails (with full bodies), company context, and gap contacts in
maximally parallel async calls. Returns structured data for the Claude Code
deal-analysis skill to interpret.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from knotch_mcp.clients.hubspot import (
    DEAL_CONTACT_PROPERTIES,
    DEAL_PROPERTIES,
    EMAIL_PROPERTIES,
    MEETING_PROPERTIES,
    HubSpotClient,
)
from knotch_mcp.log import ToolLogContext, get_logger
from knotch_mcp.models import (
    AttendeeInfo,
    DealAnalysisResult,
    DealContactProfile,
    EmailDetail,
    MeetingDetail,
)

logger = get_logger("knotch_mcp.deal_analysis")


# ── Knotch buying committee framework ────────────────────────────

STAGE_REQUIREMENTS: dict[str, dict] = {
    "ipm": {
        "label": "IPM",
        "min_contacts": 1,
        "max_contacts": 2,
        "required_roles": [],
        "recommended_roles": ["Champion"],
    },
    "qualification": {
        "label": "Qualification (Stage 1)",
        "min_contacts": 2,
        "max_contacts": 3,
        "required_roles": ["Champion"],
        "recommended_roles": ["Economic Buyer"],
    },
    "consensus": {
        "label": "Consensus (Stage 2)",
        "min_contacts": 3,
        "max_contacts": 5,
        "required_roles": ["Champion", "Economic Buyer"],
        "recommended_roles": ["Influencer", "Technical Validator"],
    },
    "proposal": {
        "label": "Proposal (Stage 3)",
        "min_contacts": 4,
        "max_contacts": 6,
        "required_roles": [
            "Champion",
            "Economic Buyer",
            "Influencer",
            "Technical Validator",
        ],
        "recommended_roles": ["Blocker", "Executive Sponsor"],
    },
    "procurement": {
        "label": "Procurement (Stage 4)",
        "min_contacts": 5,
        "max_contacts": 8,
        "required_roles": [
            "Champion",
            "Economic Buyer",
            "Influencer",
            "Technical Validator",
            "Blocker",
        ],
        "recommended_roles": ["Executive Sponsor", "End User"],
    },
}

_STAGE_NAME_MAP: dict[str, str] = {
    "ipm": "ipm",
    "qualification": "qualification",
    "stage 1": "qualification",
    "consensus": "consensus",
    "stage 2": "consensus",
    "proposal": "proposal",
    "stage 3": "proposal",
    "procurement": "procurement",
    "stage 4": "procurement",
}

_CLOSED_STAGES = {"closedwon", "closedlost", "closed won", "closed lost"}


def _resolve_stage_key(stage_label: str) -> str | None:
    normalized = stage_label.lower().strip()
    if normalized in _CLOSED_STAGES:
        return None
    for pattern, key in _STAGE_NAME_MAP.items():
        if pattern in normalized:
            return key
    return "qualification"


# ── Contact profile builder ─────────────────────────────────────


def _build_contact_profile(
    record: dict, on_deal: bool = True, internal_emails: set[str] | None = None
) -> DealContactProfile:
    props = record.get("properties", {})
    first = props.get("firstname", "") or ""
    last = props.get("lastname", "") or ""
    name = f"{first} {last}".strip()

    notes_count = 0
    raw_notes = props.get("num_notes") or props.get("num_contacted_notes") or "0"
    try:
        notes_count = int(raw_notes)
    except (ValueError, TypeError):
        pass

    last_activity = props.get("notes_last_updated")

    engagement = "none"
    if notes_count > 5:
        engagement = "high"
    elif notes_count > 1:
        engagement = "medium"
    elif notes_count > 0 or last_activity:
        engagement = "low"

    is_internal = False
    contact_email = (props.get("email") or "").lower()
    if internal_emails and contact_email and contact_email in internal_emails:
        is_internal = True

    return DealContactProfile(
        contact_id=record.get("id", ""),
        name=name or f"Contact {record.get('id', '?')}",
        title=props.get("jobtitle"),
        email=props.get("email"),
        company=props.get("company"),
        current_buying_role=props.get("hs_buying_role"),
        persona=props.get("hs_persona"),
        seniority=props.get("seniority_level__knotch_"),
        linkedin_url=props.get("hs_linkedin_url"),
        owner_id=props.get("hubspot_owner_id"),
        lead_status=props.get("hs_lead_status"),
        lifecycle_stage=props.get("lifecyclestage"),
        notes_count=notes_count,
        last_activity=last_activity,
        engagement_level=engagement,
        on_deal=on_deal,
        is_internal=is_internal,
    )


def _build_attendee(
    contact_id: str,
    contact_map: dict[str, DealContactProfile],
    deal_contact_ids: set[str],
) -> AttendeeInfo:
    profile = contact_map.get(contact_id)
    if profile:
        return AttendeeInfo(
            contact_id=contact_id,
            name=profile.name,
            title=profile.title,
            email=profile.email,
            on_deal=contact_id in deal_contact_ids,
        )
    return AttendeeInfo(
        contact_id=contact_id,
        on_deal=contact_id in deal_contact_ids,
        name=f"Contact {contact_id}",
    )


# ── Main data assembler ──────────────────────────────────────────


async def _deal_analysis(
    deal_id: str,
    hubspot: HubSpotClient,
) -> DealAnalysisResult:
    log_ctx = ToolLogContext("deal_analysis")

    # ── Phase 1: Resolve the deal ──
    deal = await _resolve_deal(deal_id, hubspot, log_ctx)
    if "error" in deal:
        logger.info("tool completed (deal not found)", extra=log_ctx.finish())
        return DealAnalysisResult(
            deal_id=deal_id,
            deal_name="",
            deal_stage="",
            stage_label="",
            warnings=[deal["error"]],
        )

    props = deal.get("properties", {})
    deal_name = props.get("dealname", "")
    deal_stage_id = props.get("dealstage", "")
    pipeline_id = props.get("pipeline", "")
    resolved_id = deal.get("id", deal_id)

    # ── Phase 2: Parallel — pipelines, owners, all 4 association types ──
    (
        stage_result,
        internal_emails,
        contact_assoc,
        meeting_assoc,
        email_assoc,
        company_assoc,
    ) = await asyncio.gather(
        _resolve_stage(pipeline_id, deal_stage_id, hubspot, log_ctx),
        _safe_get_owner_emails(hubspot, log_ctx),
        hubspot.get_associations("deals", resolved_id, "contacts"),
        hubspot.get_associations("deals", resolved_id, "meetings"),
        hubspot.get_associations("deals", resolved_id, "emails"),
        hubspot.get_associations("deals", resolved_id, "companies"),
    )

    stage_label, is_closed = stage_result

    if is_closed:
        logger.info("tool completed (closed deal)", extra=log_ctx.finish())
        return DealAnalysisResult(
            deal_id=resolved_id,
            deal_name=deal_name,
            deal_stage=deal_stage_id,
            stage_label=stage_label,
            pipeline=pipeline_id,
            amount=props.get("amount"),
            close_date=props.get("closedate"),
            deal_url=hubspot.build_deal_url(resolved_id),
            warnings=[
                f"Deal is {stage_label} — buyer role analysis not applicable for closed deals."
            ],
        )

    stage_key = _resolve_stage_key(stage_label)
    stage_reqs = STAGE_REQUIREMENTS.get(
        stage_key or "qualification", STAGE_REQUIREMENTS["qualification"]
    )

    # Extract association IDs
    deal_contact_ids = _extract_ids(contact_assoc)
    meeting_ids = _extract_ids(meeting_assoc)
    email_ids = _extract_ids(email_assoc)
    company_id = _extract_ids(company_assoc)[:1]  # just the first

    # ── Phase 3: Parallel — batch reads + company + other deals + per-item associations ──
    coros: dict[str, any] = {}

    if deal_contact_ids:
        coros["contacts"] = hubspot.batch_read(
            "contacts", deal_contact_ids, DEAL_CONTACT_PROPERTIES
        )
    if meeting_ids:
        coros["meetings"] = hubspot.batch_read(
            "meetings", meeting_ids, MEETING_PROPERTIES
        )
        for mid in meeting_ids:
            coros[f"mtg_assoc_{mid}"] = hubspot.get_associations(
                "meetings", mid, "contacts"
            )
    if email_ids:
        recent_email_ids = email_ids[:30]
        coros["emails"] = hubspot.batch_read(
            "emails", recent_email_ids, EMAIL_PROPERTIES
        )
        for eid in recent_email_ids:
            coros[f"email_assoc_{eid}"] = hubspot.get_associations(
                "emails", eid, "contacts"
            )
    if company_id:
        coros["company"] = hubspot.get_company(company_id[0])
        coros["other_deals"] = hubspot.search_deals_by_company(company_id[0])

    keys = list(coros.keys())
    results = await asyncio.gather(*coros.values(), return_exceptions=True)
    tasks = dict(zip(keys, results))

    # ── Unpack results ──
    contact_records = _task_result(tasks, "contacts", [])
    meeting_records = _task_result(tasks, "meetings", [])
    email_records = _task_result(tasks, "emails", [])

    deal_contacts = [
        _build_contact_profile(r, internal_emails=internal_emails)
        for r in contact_records
    ]

    # Build contact map (ID → profile) for attendee resolution
    contact_map: dict[str, DealContactProfile] = {
        c.contact_id: c for c in deal_contacts
    }
    deal_contact_id_set = set(deal_contact_ids)

    # Build meeting details with attendees
    meeting_contact_ids: set[str] = set()
    meetings: list[MeetingDetail] = []
    for mr in meeting_records:
        mid = mr.get("id", "")
        m_props = mr.get("properties", {})
        attendee_ids = _extract_ids(_task_result(tasks, f"mtg_assoc_{mid}", []))
        meeting_contact_ids.update(attendee_ids)

        meetings.append(
            MeetingDetail(
                id=mid,
                title=m_props.get("hs_meeting_title"),
                start_time=m_props.get("hs_meeting_start_time"),
                outcome=m_props.get("hs_meeting_outcome"),
                attendee_ids=attendee_ids,
                body=m_props.get("hs_meeting_body") or "",
            )
        )

    # Build email details with contacts
    email_contact_ids: set[str] = set()
    emails: list[EmailDetail] = []
    recent_email_ids = email_ids[:30]
    for er in email_records:
        eid = er.get("id", "")
        e_props = er.get("properties", {})
        assoc_contact_ids = _extract_ids(_task_result(tasks, f"email_assoc_{eid}", []))
        email_contact_ids.update(assoc_contact_ids)

        to_emails: list[str] = []
        if e_props.get("hs_email_to_email"):
            to_emails = [
                addr.strip()
                for addr in e_props["hs_email_to_email"].split(";")
                if addr.strip()
            ]

        emails.append(
            EmailDetail(
                id=eid,
                subject=e_props.get("hs_email_subject"),
                timestamp=e_props.get("hs_timestamp"),
                direction=e_props.get("hs_email_direction"),
                from_email=e_props.get("hs_email_from_email"),
                to_emails=to_emails,
                associated_contact_ids=assoc_contact_ids,
                body=e_props.get("hs_email_text") or "",
            )
        )

    # Company + other deals
    company_name: str | None = None
    cid: str | None = company_id[0] if company_id else None
    other_open_deals: list[dict] = []

    if "company" in tasks:
        company_record = _task_result(tasks, "company", {})
        company_name = company_record.get("properties", {}).get("name")

        all_company_deals = _task_result(tasks, "other_deals", [])
        other_open_deals = [
            {
                "id": d.get("id"),
                "name": d.get("properties", {}).get("dealname"),
                "stage": d.get("properties", {}).get("dealstage"),
                "amount": d.get("properties", {}).get("amount"),
            }
            for d in all_company_deals
            if d.get("id") != resolved_id
        ]

    # ── Phase 4: Fetch gap contacts (in meetings/emails but not on deal) ──
    gap_candidate_ids = (meeting_contact_ids | email_contact_ids) - deal_contact_id_set
    gap_candidate_ids = {gid for gid in gap_candidate_ids if gid}

    gap_contacts: list[DealContactProfile] = []
    if gap_candidate_ids:
        try:
            gap_records = await hubspot.batch_read(
                "contacts", list(gap_candidate_ids), DEAL_CONTACT_PROPERTIES
            )
            gap_contacts = [
                _build_contact_profile(
                    r, on_deal=False, internal_emails=internal_emails
                )
                for r in gap_records
            ]
            # Add gap contacts to the map for attendee resolution
            for gc in gap_contacts:
                contact_map[gc.contact_id] = gc
        except Exception:
            logger.warning("failed to fetch gap candidate contacts")

    # ── Resolve attendees to names ──
    for meeting in meetings:
        meeting.attendees = [
            _build_attendee(aid, contact_map, deal_contact_id_set)
            for aid in meeting.attendee_ids
        ]

    for email in emails:
        email.associated_contacts = [
            _build_attendee(cid, contact_map, deal_contact_id_set)
            for cid in email.associated_contact_ids
        ]

    # ── Deal age ──
    deal_age = None
    create_date = props.get("createdate")
    if create_date:
        try:
            created = datetime.fromisoformat(create_date.replace("Z", "+00:00"))
            deal_age = (datetime.now(timezone.utc) - created).days
        except (ValueError, TypeError):
            pass

    activity_summary = {
        "total_emails": len(email_ids),
        "emails_fetched": len(emails),
        "meetings": len(meetings),
        "contacts_on_deal": len(deal_contacts),
        "gap_contacts": len(gap_contacts),
    }

    warnings: list[str] = []
    if len(deal_contacts) <= 1:
        warnings.append("SINGLE-THREADED: Only 1 contact on this deal.")
    min_contacts = stage_reqs.get("min_contacts", 1)
    if len(deal_contacts) < min_contacts:
        warnings.append(
            f"Contact count ({len(deal_contacts)}) is below stage minimum ({min_contacts})."
        )
    if not meetings and not is_closed:
        warnings.append(
            "No meetings recorded on this deal — meetings may not be associated or logged."
        )
    if not emails and not is_closed:
        warnings.append(
            "No emails recorded on this deal — emails may not be associated or logged."
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return DealAnalysisResult(
        deal_id=resolved_id,
        deal_name=deal_name,
        deal_stage=deal_stage_id,
        stage_label=stage_label,
        pipeline=pipeline_id,
        amount=props.get("amount"),
        close_date=props.get("closedate"),
        owner_id=props.get("hubspot_owner_id"),
        deal_age_days=deal_age,
        deal_url=hubspot.build_deal_url(resolved_id),
        deal_description=props.get("description"),
        company_name=company_name,
        company_id=cid,
        other_open_deals=other_open_deals,
        stage_requirements=stage_reqs,
        deal_contacts=deal_contacts,
        gap_contacts=gap_contacts,
        internal_emails=list(internal_emails),
        meetings=meetings,
        emails=emails,
        activity_summary=activity_summary,
        warnings=warnings,
    )


# ── Helpers ──────────────────────────────────────────────────────


def _extract_ids(assoc_list: list[dict]) -> list[str]:
    return [
        str(cid) for a in assoc_list if (cid := a.get("toObjectId", a.get("id", "")))
    ]


def _task_result(tasks: dict, key: str, default):
    result = tasks.get(key)
    if result is None:
        return default
    if isinstance(result, BaseException):
        return default
    if isinstance(result, asyncio.Task):
        try:
            return result.result()
        except Exception:
            return default
    return result


async def _safe_get_owner_emails(
    hubspot: HubSpotClient, log_ctx: ToolLogContext
) -> set[str]:
    try:
        emails = await hubspot.get_owner_emails()
        log_ctx.add_api_call("hubspot")
        return emails
    except Exception:
        logger.warning("failed to fetch HubSpot owners — internal filtering disabled")
        return set()


async def _resolve_deal(
    deal_id: str, hubspot: HubSpotClient, log_ctx: ToolLogContext
) -> dict:
    if deal_id.isdigit():
        try:
            deal = await hubspot.get_deal(deal_id)
            log_ctx.add_api_call("hubspot")
            return deal
        except Exception:
            log_ctx.add_api_call("hubspot")
    else:
        results = await hubspot.search_deals(deal_id)
        log_ctx.add_api_call("hubspot")
        if results:
            return results[0]

    return {
        "error": f"Deal not found: '{deal_id}'. Try searching by deal name or verify the deal ID."
    }


async def _resolve_stage(
    pipeline_id: str,
    stage_id: str,
    hubspot: HubSpotClient,
    log_ctx: ToolLogContext,
) -> tuple[str, bool]:
    try:
        pipelines = await hubspot.get_pipelines()
        log_ctx.add_api_call("hubspot")
        for pipeline in pipelines:
            if pipeline.get("id") == pipeline_id:
                for stage in pipeline.get("stages", []):
                    if stage.get("id") == stage_id:
                        label = stage.get("label", stage_id)
                        is_closed = (
                            label.lower().replace(" ", "")
                            in {
                                "closedwon",
                                "closedlost",
                            }
                            or stage.get("metadata", {}).get("isClosed") == "true"
                        )
                        return label, is_closed
    except Exception:
        logger.warning("failed to fetch pipeline definitions")

    return stage_id, False
