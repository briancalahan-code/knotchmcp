"""Team activity aggregation — one MCP call replaces ~50 HubSpot roundtrips."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from knotch_mcp.clients.hubspot import HubSpotClient
from knotch_mcp.log import ToolLogContext, get_logger
from knotch_mcp.models import OwnerActivity, TeamActivityResult

logger = get_logger("knotch_mcp.team_activity")

_TOOL_VERSION = "d530a5d"

DEFAULT_PIPELINE = "72018330"


async def _fetch_owner_activity(
    owner_id: int,
    start_ms: int,
    end_ms: int,
    pipeline_id: str,
    hubspot: HubSpotClient,
    log_ctx: ToolLogContext,
) -> tuple[OwnerActivity, set[str], set[str], dict]:
    """Fetch all activity metrics for one owner.

    Returns (activity, company_ids, contact_ids, ipm_debug) so the caller can
    union-dedup company/contact sets across owners for the team totals.
    """
    oid = str(owner_id)
    ipm_debug: dict = {}

    # HubSpot CRM Search API requires epoch ms for date properties,
    # even though ipm_held displays as YYYY-MM-DD.
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_date_ms = str(int(start_dt.timestamp() * 1000))
    end_date_ms = str(int(end_dt.timestamp() * 1000))

    # ── Phase 1: parallel searches for emails, meetings, IPM deals ──
    ipm_filters = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": oid},
        {"propertyName": "ipm_held", "operator": "GTE", "value": start_date_ms},
        {"propertyName": "ipm_held", "operator": "LTE", "value": end_date_ms},
    ]
    ipm_debug["filters"] = ipm_filters
    ipm_debug["start_date_ms"] = start_date_ms
    ipm_debug["end_date_ms"] = end_date_ms
    ipm_debug["start_date_human"] = start_dt.strftime("%Y-%m-%d")
    ipm_debug["end_date_human"] = end_dt.strftime("%Y-%m-%d")

    search_results = await asyncio.gather(
        hubspot.search_paginated(
            "emails",
            [
                {
                    "propertyName": "hubspot_owner_id",
                    "operator": "EQ",
                    "value": oid,
                },
                {
                    "propertyName": "hs_email_direction",
                    "operator": "NEQ",
                    "value": "INCOMING_EMAIL",
                },
                {
                    "propertyName": "hs_timestamp",
                    "operator": "GTE",
                    "value": str(start_ms),
                },
                {
                    "propertyName": "hs_timestamp",
                    "operator": "LTE",
                    "value": str(end_ms),
                },
            ],
        ),
        hubspot.search_paginated(
            "meetings",
            [
                {
                    "propertyName": "hubspot_owner_id",
                    "operator": "EQ",
                    "value": oid,
                },
                {
                    "propertyName": "hs_meeting_start_time",
                    "operator": "GTE",
                    "value": str(start_ms),
                },
                {
                    "propertyName": "hs_meeting_start_time",
                    "operator": "LTE",
                    "value": str(end_ms),
                },
            ],
        ),
        hubspot.search_paginated(
            "deals",
            ipm_filters,
            properties=["dealname", "ipm_held", "hubspot_owner_id"],
        ),
        return_exceptions=True,
    )

    emails: list[dict] = []
    meetings: list[dict] = []
    ipm_deals: list[dict] = []
    for i, (name, target) in enumerate(
        [("emails", emails), ("meetings", meetings), ("ipms", ipm_deals)]
    ):
        if isinstance(search_results[i], BaseException):
            logger.warning(
                "%s search failed for owner %s: %s", name, oid, search_results[i]
            )
            if name == "ipms":
                exc = search_results[i]
                ipm_debug["error"] = str(exc)
                ipm_debug["error_type"] = type(exc).__name__
                if hasattr(exc, "response"):
                    try:
                        ipm_debug["response_body"] = exc.response.text[:500]
                        ipm_debug["status_code"] = exc.response.status_code
                    except Exception:
                        ipm_debug["response_body"] = "(could not read)"
        else:
            target.extend(search_results[i])
            if name == "ipms":
                ipm_debug["result_count"] = len(search_results[i])
        log_ctx.add_api_call("hubspot")

    email_ids = [e["id"] for e in emails]
    meeting_ids = [m["id"] for m in meetings]

    # ── Phase 2: parallel batch association reads ──
    assoc_results = await asyncio.gather(
        hubspot.batch_get_associated_ids("emails", "companies", email_ids),
        hubspot.batch_get_associated_ids("meetings", "companies", meeting_ids),
        hubspot.batch_get_associated_ids("emails", "contacts", email_ids),
        hubspot.batch_get_associated_ids("meetings", "contacts", meeting_ids),
        return_exceptions=True,
    )

    company_ids: set[str] = set()
    contact_ids: set[str] = set()
    assoc_labels = (
        "email->company",
        "meeting->company",
        "email->contact",
        "meeting->contact",
    )
    for i, aresult in enumerate(assoc_results):
        if isinstance(aresult, BaseException):
            logger.warning(
                "%s assoc failed for owner %s: %s", assoc_labels[i], oid, aresult
            )
        else:
            if i < 2:
                company_ids |= aresult
            else:
                contact_ids |= aresult
            log_ctx.add_api_call("hubspot")

    return (
        OwnerActivity(
            emails=len(emails),
            meetings=len(meetings),
            ipms_held=len(ipm_deals),
            accounts_touched=len(company_ids),
            people_touched=len(contact_ids),
        ),
        company_ids,
        contact_ids,
        ipm_debug,
    )


async def _team_activity(
    start_ms: int,
    end_ms: int,
    owner_ids: list[int],
    pipeline_id: str | None,
    hubspot: HubSpotClient,
) -> TeamActivityResult:
    """Aggregate team activity across owners with server-side dedup."""
    log_ctx = ToolLogContext("team_activity")
    pipeline = pipeline_id or DEFAULT_PIPELINE

    results = await asyncio.gather(
        *[
            _fetch_owner_activity(oid, start_ms, end_ms, pipeline, hubspot, log_ctx)
            for oid in owner_ids
        ],
        return_exceptions=True,
    )

    by_owner: dict[str, OwnerActivity] = {}
    all_companies: set[str] = set()
    all_contacts: set[str] = set()
    total_emails = 0
    total_meetings = 0
    total_ipms = 0
    debug_info: dict = {"pipeline": pipeline}
    first_ipm_debug_captured = False

    for oid, result in zip(owner_ids, results):
        if isinstance(result, BaseException):
            logger.warning("owner %s failed: %s", oid, result)
            by_owner[str(oid)] = OwnerActivity()
            if not first_ipm_debug_captured:
                debug_info["first_owner_error"] = str(result)
                first_ipm_debug_captured = True
            continue
        activity, companies, contacts, ipm_debug = result
        by_owner[str(oid)] = activity
        all_companies |= companies
        all_contacts |= contacts
        total_emails += activity.emails
        total_meetings += activity.meetings
        total_ipms += activity.ipms_held
        if not first_ipm_debug_captured:
            debug_info["ipm_sample"] = ipm_debug
            first_ipm_debug_captured = True

    team = OwnerActivity(
        emails=total_emails,
        meetings=total_meetings,
        ipms_held=total_ipms,
        accounts_touched=len(all_companies),
        people_touched=len(all_contacts),
    )

    logger.info("tool completed", extra=log_ctx.finish())
    return TeamActivityResult(
        team=team,
        by_owner=by_owner,
        window={"start_ms": start_ms, "end_ms": end_ms},
        generated_at_ms=int(time.time() * 1000),
        tool_version=_TOOL_VERSION,
        ipm_debug=debug_info,
    )
