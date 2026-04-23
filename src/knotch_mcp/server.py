from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from knotch_mcp.clients.apollo import ApolloClient
from knotch_mcp.clients.clay import ClayClient
from knotch_mcp.clients.hubspot import HubSpotClient
from knotch_mcp.config import Settings
from knotch_mcp.log import get_logger
from knotch_mcp.rate_limit import TokenBucket
from knotch_mcp.tools import (
    _add_to_hubspot,
    _check_clay_result,
    _clay_enrich,
    _enrich_contact,
    _find_contact_by_details,
    _find_contacts_by_role,
    _find_phone,
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
)


@mcp.tool()
async def find_contact_by_details(
    first_name: str,
    last_name: str,
    company: str,
    email: str | None = None,
    linkedin_url: str | None = None,
) -> dict:
    """Find a contact by name and company. Searches Apollo for contact details
    and checks HubSpot for existing records. Returns gaps and suggested next actions."""
    result = await _find_contact_by_details(
        first_name, last_name, company, email, linkedin_url, _apollo, _hubspot
    )
    return result.model_dump()


@mcp.tool()
async def find_contacts_by_role(
    titles: list[str],
    company: str,
    seniority: str | None = None,
    limit: int = 3,
) -> dict:
    """Find contacts by job title at a company. Returns top candidates with
    HubSpot status. Seniority options: owner, founder, c_suite, partner, vp,
    head, director, manager, senior, entry, intern."""
    result = await _find_contacts_by_role(
        titles, company, seniority, limit, _apollo, _hubspot
    )
    return result.model_dump()


@mcp.tool()
async def find_phone(
    apollo_id: str | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    name: str | None = None,
) -> dict:
    """Find a contact's phone number. Provide at least one identifier:
    apollo_id, email, or linkedin_url + name. Returns cached phone data from
    Apollo. If not found, suggests clay_enrich as a follow-up."""
    result = await _find_phone(apollo_id, email, linkedin_url, name, _apollo)
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
    email: str | None = None,
    title: str | None = None,
    company: str | None = None,
    company_domain: str | None = None,
    linkedin_url: str | None = None,
    location: str | None = None,
    phone: str | None = None,
    apollo_id: str | None = None,
) -> dict:
    """Add a contact to HubSpot. Dedupes by email and LinkedIn URL first — updates
    if found, creates if new. Associates with the matching company by domain."""
    result = await _add_to_hubspot(
        first_name,
        last_name,
        email,
        title,
        company,
        company_domain,
        linkedin_url,
        location,
        phone,
        apollo_id,
        _hubspot,
    )
    return result.model_dump()


@mcp.tool()
async def clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str] | None = None,
    linkedin_url: str | None = None,
) -> dict:
    """Enrich a contact using Clay. Use this as a follow-up when Apollo didn't
    return phone, email, or other data. requested_data options: phone, email.
    Pass linkedin_url if available — Clay uses it for phone lookup. Consumes Clay credits.
    Returns a correlationId — call check_clay_result after ~90 seconds to get the data."""
    data = requested_data or ["phone", "email"]
    result = await _clay_enrich(
        first_name, last_name, company_domain, data, _clay, linkedin_url=linkedin_url
    )
    return result.model_dump()


@mcp.tool()
async def check_clay_result(correlation_id: str) -> dict:
    """Check if Clay enrichment results are ready. Call this ~90 seconds after
    clay_enrich returned a correlationId. Returns the enriched data if the Clay
    callback has arrived, or status 'pending' if still waiting."""
    result = await _check_clay_result(correlation_id, _clay)
    return result.model_dump()
