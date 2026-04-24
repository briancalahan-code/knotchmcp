from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from knotch_mcp.clients.apollo import ApolloClient
from knotch_mcp.clients.clay import ClayClient
from knotch_mcp.clients.hubspot import HubSpotClient
from knotch_mcp.config import Settings
from knotch_mcp.log import get_logger
from knotch_mcp.rate_limit import TokenBucket
from knotch_mcp.deal_analysis import _deal_analysis
from knotch_mcp.tools import (
    _add_to_hubspot,
    _associate_contact_to_deal,
    _check_clay_result,
    _clay_enrich,
    _enrich_contact,
    _find_contact_by_details,
    _find_contacts_by_role,
    _find_phone,
    _lookup_contact,
    _update_object,
)

logger = get_logger("knotch_mcp.server")
settings = Settings()

mcp = FastMCP("KnotchMCP", host="0.0.0.0")

_rate_limiter = TokenBucket(
    rate=settings.apollo_rate_limit / 60.0, capacity=settings.apollo_rate_limit
)
_apollo = ApolloClient(api_key=settings.apollo_api_key, rate_limiter=_rate_limiter)
_clay = ClayClient(
    webhook_url=settings.clay_webhook_url,
    webhook_token=settings.clay_webhook_token,
)
_hubspot = HubSpotClient(
    access_token=settings.hubspot_private_app_token,
    portal_id=settings.hubspot_portal_id,
    max_retries=settings.hubspot_max_retries,
    base_delay=settings.hubspot_retry_base_delay,
    timeout=settings.hubspot_timeout,
)


def _clean(val: str) -> str | None:
    if not val or val.lower() == "null":
        return None
    return val


@mcp.tool()
async def find_contact_by_details(
    first_name: str,
    last_name: str,
    company: str,
    email: str = "",
    linkedin_url: str = "",
) -> dict:
    """Find a contact by name and company. Searches Apollo using exact match,
    then falls back to nickname variants and keyword search if needed.
    Checks HubSpot for existing records.

    If match_method is not 'exact', confirm with the user that this is the
    right person. If alternate_matches is present, show those candidates too.

    WORKFLOW: After showing the results, ALWAYS ask the user:
    1. 'Is this the right person?' (if match_method is not 'exact')
    2. 'Would you like me to add this contact to HubSpot?' (if hubspot_status is not_found)
    3. 'Would you like me to run Clay enrichment for missing data?' (if there are gaps)
    Do NOT skip straight to Clay — always offer HubSpot first."""
    result = await _find_contact_by_details(
        first_name,
        last_name,
        company,
        _clean(email),
        _clean(linkedin_url),
        _apollo,
        _hubspot,
    )
    return result.model_dump()


@mcp.tool()
async def find_contacts_by_role(
    titles: list[str],
    company: str,
    seniority: str = "",
    limit: int = 3,
) -> dict:
    """Find contacts by job title at a company. Returns top candidates with
    HubSpot status. Seniority options: owner, founder, c_suite, partner, vp,
    head, director, manager, senior, entry, intern.

    WORKFLOW: After showing results, ask the user which contacts they want to
    add to HubSpot before suggesting Clay enrichment."""
    result = await _find_contacts_by_role(
        titles, company, _clean(seniority), limit, _apollo, _hubspot
    )
    return result.model_dump()


@mcp.tool()
async def find_phone(
    apollo_id: str = "",
    email: str = "",
    linkedin_url: str = "",
    name: str = "",
) -> dict:
    """Find a contact's phone number. Provide at least one identifier:
    apollo_id, email, or linkedin_url + name. Returns cached phone data from
    Apollo. If not found, suggests clay_enrich as a follow-up."""
    result = await _find_phone(
        _clean(apollo_id), _clean(email), _clean(linkedin_url), _clean(name), _apollo
    )
    return result.model_dump()


@mcp.tool()
async def lookup_contact(apollo_id: str) -> dict:
    """Look up a contact by Apollo ID. Use this when find_contact_by_details
    returned alternate_matches and the user selected one. Returns the full
    contact record with HubSpot status check.

    WORKFLOW: After showing the result, follow the same flow as
    find_contact_by_details — offer HubSpot add, then Clay enrichment."""
    result = await _lookup_contact(apollo_id, _apollo, _hubspot)
    return result.model_dump()


@mcp.tool()
async def enrich_contact(hubspot_contact_id: str) -> dict:
    """Enrich a HubSpot contact by filling empty fields (jobtitle, phone,
    linkedin, location, company) from Apollo and Clay. Writes results back
    to HubSpot automatically. Returns a diff of what was filled."""
    result = await _enrich_contact(hubspot_contact_id, _apollo, _hubspot, _clay)
    return result.model_dump()


@mcp.tool()
async def add_to_hubspot(
    first_name: str,
    last_name: str,
    email: str = "",
    title: str = "",
    company: str = "",
    company_domain: str = "",
    linkedin_url: str = "",
    location: str = "",
    phone: str = "",
    apollo_id: str = "",
) -> dict:
    """Add a contact to HubSpot. Dedupes by email and LinkedIn URL first — updates
    if found, creates if new. Creates the company in HubSpot if it doesn't exist
    yet, then associates the contact to the company. Always pass company_domain
    when available so the company association works."""
    result = await _add_to_hubspot(
        first_name,
        last_name,
        _clean(email),
        _clean(title),
        _clean(company),
        _clean(company_domain),
        _clean(linkedin_url),
        _clean(location),
        _clean(phone),
        _clean(apollo_id),
        _hubspot,
    )
    return result.model_dump()


@mcp.tool()
async def clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str] | None = None,
    linkedin_url: str = "",
) -> dict:
    """Enrich a contact using Clay. Waits up to 50s for results. If the callback
    arrives in time, returns the enriched data directly. If it times out, returns
    a correlationId — use check_clay_result to retrieve the data later.
    requested_data options: phone, email. Pass linkedin_url if available."""
    data = requested_data or ["phone", "email"]
    result = await _clay_enrich(
        first_name,
        last_name,
        company_domain,
        data,
        _clay,
        linkedin_url=_clean(linkedin_url),
    )
    return result.model_dump()


@mcp.tool()
async def check_clay_result(correlation_id: str) -> dict:
    """Check if Clay enrichment results are ready. Call this after clay_enrich
    returned a correlationId with status 'timeout'. Returns the enriched data
    if the Clay callback has arrived, or status 'pending' if still waiting."""
    result = await _check_clay_result(correlation_id, _clay)
    return result.model_dump()


@mcp.tool()
async def deal_analysis(deal_id: str) -> dict:
    """Analyze a HubSpot deal's buyer roles and engagement. Accepts a deal ID
    (numeric) or deal name (text search). Returns a comprehensive analysis:

    - Contacts currently on the deal vs. who SHOULD be (from meetings/emails)
    - Buyer role recommendations for each contact (Champion, Economic Buyer, etc.)
    - Stage-specific gaps against Knotch's SPICED/buying committee framework
    - SPICED scorecard comparing what's on the deal record vs. what activity reveals
    - Recommended CRM edits grouped by category (associations, roles, other)

    Internal team members (HubSpot owners) are automatically excluded from buyer
    role recommendations — only external contacts get role assignments.

    This tool is READ-ONLY — it returns recommendations. Use
    update_contact_properties, update_deal_properties, associate_contact_to_deal
    to execute the recommended changes after user review.

    WORKFLOW: Present the full analysis. For SPICED, compare deal_record_evidence
    vs activity_evidence and highlight the gap_assessment for each element.
    For recommended changes, use edit_groups to present each category separately:
    'Would you like to add these N contacts?' then 'Would you like to set these
    M buyer roles?' — never lump all edits into one prompt."""
    result = await _deal_analysis(deal_id, _hubspot)
    return result.model_dump()


@mcp.tool()
async def update_contact_properties(contact_id: str, properties: str) -> dict:
    """Update properties on a HubSpot contact. Pass properties as a JSON string
    of key-value pairs, e.g. '{"hs_buying_role": "Champion", "jobtitle": "VP Marketing"}'.

    Common properties: hs_buying_role, jobtitle, lifecyclestage, hs_lead_status,
    phone, email, firstname, lastname, company."""
    import json

    try:
        props = json.loads(properties)
    except json.JSONDecodeError as exc:
        from knotch_mcp.models import UpdateResult

        return UpdateResult(
            object_type="contacts",
            object_id=contact_id,
            success=False,
            error=f"Invalid JSON: {exc}",
        ).model_dump()
    result = await _update_object("contacts", contact_id, props, _hubspot)
    return result.model_dump()


@mcp.tool()
async def update_deal_properties(deal_id: str, properties: str) -> dict:
    """Update properties on a HubSpot deal. Pass properties as a JSON string
    of key-value pairs, e.g. '{"dealstage": "closedwon", "amount": "50000"}'.

    Common properties: dealname, dealstage, amount, closedate, description,
    hubspot_owner_id."""
    import json

    try:
        props = json.loads(properties)
    except json.JSONDecodeError as exc:
        from knotch_mcp.models import UpdateResult

        return UpdateResult(
            object_type="deals",
            object_id=deal_id,
            success=False,
            error=f"Invalid JSON: {exc}",
        ).model_dump()
    result = await _update_object("deals", deal_id, props, _hubspot)
    return result.model_dump()


@mcp.tool()
async def update_company_properties(company_id: str, properties: str) -> dict:
    """Update properties on a HubSpot company. Pass properties as a JSON string
    of key-value pairs, e.g. '{"industry": "Technology", "numberofemployees": "500"}'.

    Common properties: name, domain, industry, numberofemployees, annualrevenue,
    description."""
    import json

    try:
        props = json.loads(properties)
    except json.JSONDecodeError as exc:
        from knotch_mcp.models import UpdateResult

        return UpdateResult(
            object_type="companies",
            object_id=company_id,
            success=False,
            error=f"Invalid JSON: {exc}",
        ).model_dump()
    result = await _update_object("companies", company_id, props, _hubspot)
    return result.model_dump()


@mcp.tool()
async def associate_contact_to_deal(contact_id: str, deal_id: str) -> dict:
    """Associate a contact to a deal in HubSpot. Use this after deal_analysis
    identifies contacts who attended meetings or appeared in emails but aren't
    on the deal yet."""
    result = await _associate_contact_to_deal(contact_id, deal_id, _hubspot)
    return result.model_dump()
