"""Orchestration functions for all 6 MCP tools.

Each underscore-prefixed function contains the business logic for one tool.
The MCP server module registers thin wrappers that parse input models and
forward to these functions with the appropriate client instances.
"""

from __future__ import annotations

import asyncio

from knotch_mcp.clients.apollo import ApolloClient
from knotch_mcp.clients.clay import ClayClient
from knotch_mcp.clients.hubspot import HubSpotClient
from knotch_mcp.log import ToolLogContext, get_logger
from knotch_mcp.models import (
    AddToHubSpotResult,
    ClayEnrichResult,
    ContactResult,
    EnrichContactResult,
    FindContactsResult,
    FindPhoneResult,
)

logger = get_logger("knotch_mcp.tools")


def _is_domain(company: str) -> bool:
    """Return True if *company* looks like a domain (contains a dot)."""
    return "." in company


def _extract_contact(person: dict) -> ContactResult:
    """Convert an Apollo person dict into a ContactResult."""
    org = person.get("organization") or {}
    phone_numbers = person.get("phone_numbers") or []
    phone = phone_numbers[0]["raw_number"] if phone_numbers else None
    phone_type = phone_numbers[0].get("type") if phone_numbers else None

    city = person.get("city") or ""
    state = person.get("state") or ""
    country = person.get("country") or ""
    parts = [p for p in [city, state, country] if p]
    location = ", ".join(parts) if parts else None

    gaps: list[str] = []
    if not person.get("email"):
        gaps.append("email")
    if not person.get("linkedin_url"):
        gaps.append("linkedin_url")
    if not phone:
        gaps.append("phone")

    return ContactResult(
        name=f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
        title=person.get("title"),
        company=org.get("name"),
        email=person.get("email"),
        email_status=person.get("email_status"),
        linkedin_url=person.get("linkedin_url"),
        location=location,
        apollo_id=person.get("id"),
        phone=phone,
        phone_type=phone_type,
        sources=["apollo"],
        gaps=gaps,
    )


async def _check_hubspot(
    contact: ContactResult, hubspot: HubSpotClient
) -> ContactResult:
    """Look up *contact* in HubSpot by email/LinkedIn and set hubspot_status."""
    results = []
    if contact.email:
        results = await hubspot.search_contacts_by_email(contact.email)
    if not results and contact.linkedin_url:
        results = await hubspot.search_contacts_by_linkedin(contact.linkedin_url)

    if results:
        hs_id = results[0]["id"]
        contact.hubspot_status = "found"
        contact.hubspot_contact_id = hs_id
        contact.hubspot_url = hubspot.build_contact_url(hs_id)
    else:
        contact.hubspot_status = "not_found"
        contact.suggested_actions.append("add_to_hubspot")

    if contact.gaps:
        contact.suggested_actions.append("clay_enrich for " + ", ".join(contact.gaps))

    return contact


# ── Tool 1: find_contact_by_details ──────────────────────────────────


async def _find_contact_by_details(
    first_name: str,
    last_name: str,
    company: str,
    email: str | None,
    linkedin_url: str | None,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> ContactResult:
    """Apollo match + HubSpot check for a single named contact."""
    log_ctx = ToolLogContext("find_contact_by_details")

    domain = (
        company if _is_domain(company) else await apollo.resolve_company_domain(company)
    )
    log_ctx.add_api_call("apollo")

    person = await apollo.people_match(
        first_name=first_name,
        last_name=last_name,
        domain=domain,
        organization_name=company if not _is_domain(company) else None,
        email=email,
        linkedin_url=linkedin_url,
    )
    log_ctx.add_api_call("apollo")

    if not person:
        contact = ContactResult(
            name=f"{first_name} {last_name}",
            company=company,
            sources=[],
            gaps=["apollo_returned_no_match"],
            suggested_actions=["clay_enrich"],
        )
        logger.info("tool completed", extra=log_ctx.finish())
        return contact

    contact = _extract_contact(person)
    log_ctx.add_api_call("hubspot")
    contact = await _check_hubspot(contact, hubspot)

    logger.info("tool completed", extra=log_ctx.finish())
    return contact


# ── Tool 2: find_contacts_by_role ────────────────────────────────────


async def _find_contacts_by_role(
    titles: list[str],
    company: str,
    seniority: str | None,
    limit: int,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> FindContactsResult:
    """Apollo search by title/seniority, then parallel match + HubSpot check."""
    log_ctx = ToolLogContext("find_contacts_by_role")

    domain = (
        company if _is_domain(company) else await apollo.resolve_company_domain(company)
    )
    if not _is_domain(company):
        log_ctx.add_api_call("apollo")

    seniority_list = [seniority] if seniority else None
    people_raw, total = await apollo.people_search(
        titles=titles,
        domain=domain,
        seniority=seniority_list,
        per_page=limit,
    )
    log_ctx.add_api_call("apollo")

    async def enrich_one(person_stub: dict) -> ContactResult:
        person = await apollo.people_match(
            first_name=person_stub.get("first_name"),
            last_name=person_stub.get("last_name"),
            apollo_id=person_stub.get("id"),
        )
        if not person:
            return ContactResult(
                name=f"{person_stub.get('first_name', '')} {person_stub.get('last_name', '')}".strip(),
                title=person_stub.get("title"),
                sources=["apollo"],
                gaps=["enrichment_failed"],
            )
        contact = _extract_contact(person)
        return await _check_hubspot(contact, hubspot)

    candidates = await asyncio.gather(*[enrich_one(p) for p in people_raw[:limit]])
    log_ctx.add_api_call("apollo")
    log_ctx.add_api_call("hubspot")

    logger.info("tool completed", extra=log_ctx.finish())
    return FindContactsResult(candidates=list(candidates), total_available=total)


# ── Tool 3: find_phone ───────────────────────────────────────────────


async def _find_phone(
    apollo_id: str | None,
    email: str | None,
    linkedin_url: str | None,
    name: str | None,
    apollo: ApolloClient,
) -> FindPhoneResult:
    """Apollo match with reveal_phone_number to find a direct dial."""
    log_ctx = ToolLogContext("find_phone")

    first_name = None
    last_name = None
    if name:
        parts = name.split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None

    person = await apollo.people_match(
        apollo_id=apollo_id,
        email=email,
        linkedin_url=linkedin_url,
        first_name=first_name,
        last_name=last_name,
        reveal_phone_number=True,
    )
    log_ctx.add_api_call("apollo")

    if not person:
        logger.info("tool completed", extra=log_ctx.finish())
        return FindPhoneResult(
            found=False, source="apollo", suggested_action="clay_enrich"
        )

    phone_numbers = person.get("phone_numbers") or []
    if phone_numbers:
        phone = phone_numbers[0]
        logger.info("tool completed", extra=log_ctx.finish())
        return FindPhoneResult(
            phone=phone["raw_number"],
            phone_type=phone.get("type"),
            source="apollo",
            confidence="high" if len(phone_numbers) == 1 else "medium",
            found=True,
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return FindPhoneResult(found=False, source="apollo", suggested_action="clay_enrich")


# ── Tool 4: enrich_contact ───────────────────────────────────────────


async def _enrich_contact(
    hubspot_contact_id: str,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
    clay: ClayClient,
) -> EnrichContactResult:
    """Fetch HubSpot contact, fill gaps from Apollo, fall back to Clay, push updates."""
    log_ctx = ToolLogContext("enrich_contact")

    hs_contact = await hubspot.get_contact(hubspot_contact_id)
    log_ctx.add_api_call("hubspot")
    props = hs_contact.get("properties", {})

    enrichable_fields = [
        "email",
        "jobtitle",
        "phone",
        "hs_linkedin_url",
        "city",
        "state",
        "country",
        "company",
    ]
    empty_fields = [f for f in enrichable_fields if not props.get(f)]
    populated_fields = [f for f in enrichable_fields if props.get(f)]

    if not empty_fields:
        return EnrichContactResult(already_populated=populated_fields)

    person = await apollo.people_match(
        first_name=props.get("firstname"),
        last_name=props.get("lastname"),
        email=props.get("email"),
        linkedin_url=props.get("hs_linkedin_url"),
        reveal_phone_number=True,
    )
    log_ctx.add_api_call("apollo")
    sources = ["apollo"]

    field_map = {
        "email": "email",
        "jobtitle": "title",
        "phone": lambda p: (p.get("phone_numbers") or [{}])[0].get("raw_number"),
        "hs_linkedin_url": "linkedin_url",
        "city": "city",
        "state": "state",
        "country": "country",
        "company": lambda p: (p.get("organization") or {}).get("name"),
    }

    filled: dict[str, str] = {}
    still_missing: list[str] = []

    if person:
        for field in empty_fields:
            mapping = field_map.get(field, field)
            if callable(mapping):
                val = mapping(person)
            else:
                val = person.get(mapping)
            if val:
                filled[field] = str(val)
            else:
                still_missing.append(field)
    else:
        still_missing = empty_fields

    if still_missing and clay.configured:
        first = props.get("firstname", "")
        last = props.get("lastname", "")
        domain = props.get("company", "")
        linkedin = (
            filled.get("hs_linkedin_url")
            or props.get("hs_linkedin_url")
            or (person.get("linkedin_url") if person else None)
        )

        if first and last and domain:
            clay_result = await clay.enrich_contact(
                first_name=first,
                last_name=last,
                company_domain=domain,
                requested_data=[f for f in still_missing if f in ("email", "phone")],
                linkedin_url=linkedin,
            )
            log_ctx.add_api_call("clay")

            clay_status = clay_result.get("status")
            if clay_status == "timeout":
                sources.append("clay (timeout)")
            elif clay_status == "completed":
                sources.append("clay")
                clay_contacts = clay_result.get("results", [])
                if clay_contacts:
                    c = clay_contacts[0]
                    email_status = c.get("emailStatus", "")
                    if email_status == "invalid" and "email" in still_missing:
                        still_missing.remove("email")
                    clay_field_map = {
                        "phone": "phone",
                        "hs_linkedin_url": "linkedinUrl",
                        "city": "city",
                        "state": "state",
                        "country": "country",
                    }
                    if email_status != "invalid":
                        clay_field_map["email"] = "email"
                    for field in list(still_missing):
                        clay_key = clay_field_map.get(field)
                        if clay_key and c.get(clay_key):
                            filled[field] = str(c[clay_key])
                            still_missing.remove(field)

    if filled:
        await hubspot.update_contact(hubspot_contact_id, filled)
        log_ctx.add_api_call("hubspot")

    logger.info("tool completed", extra=log_ctx.finish())
    return EnrichContactResult(
        filled=filled,
        already_populated=populated_fields,
        not_found=still_missing,
        sources_used=sources,
    )


# ── Tool 5: add_to_hubspot ──────────────────────────────────────────


async def _add_to_hubspot(
    first_name: str,
    last_name: str,
    email: str | None,
    title: str | None,
    company: str | None,
    company_domain: str | None,
    linkedin_url: str | None,
    location: str | None,
    phone: str | None,
    apollo_id: str | None,
    hubspot: HubSpotClient,
) -> AddToHubSpotResult:
    """Dedupe by email/LinkedIn, create or update, then associate company."""
    log_ctx = ToolLogContext("add_to_hubspot")

    existing = []
    if email:
        existing = await hubspot.search_contacts_by_email(email)
        log_ctx.add_api_call("hubspot")
    if not existing and linkedin_url:
        existing = await hubspot.search_contacts_by_linkedin(linkedin_url)
        log_ctx.add_api_call("hubspot")

    properties: dict[str, str] = {"firstname": first_name, "lastname": last_name}
    if email:
        properties["email"] = email
    if title:
        properties["jobtitle"] = title
    if company:
        properties["company"] = company
    if linkedin_url:
        properties["hs_linkedin_url"] = linkedin_url
    if phone:
        properties["phone"] = phone
    if location:
        properties["city"] = location

    if existing:
        hs_id = existing[0]["id"]
        await hubspot.update_contact(hs_id, properties)
        action = "updated"
    else:
        result = await hubspot.create_contact(properties)
        hs_id = result["id"]
        action = "created"
    log_ctx.add_api_call("hubspot")

    company_associated = False
    company_name = None
    if company_domain:
        companies = await hubspot.search_companies_by_domain(company_domain)
        log_ctx.add_api_call("hubspot")
        if companies:
            await hubspot.associate_contact_to_company(hs_id, companies[0]["id"])
            log_ctx.add_api_call("hubspot")
            company_associated = True
            company_name = companies[0].get("properties", {}).get("name")

    logger.info("tool completed", extra=log_ctx.finish())
    return AddToHubSpotResult(
        hubspot_contact_id=hs_id,
        hubspot_url=hubspot.build_contact_url(hs_id),
        action=action,
        company_associated=company_associated,
        company_name=company_name,
    )


# ── Tool 6: clay_enrich ─────────────────────────────────────────────


async def _clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str],
    clay: ClayClient,
    linkedin_url: str | None = None,
) -> ClayEnrichResult:
    """Enrich a contact via Clay webhook with configurable data points."""
    log_ctx = ToolLogContext("clay_enrich")

    if not clay.configured:
        logger.info("tool completed (clay not configured)", extra=log_ctx.finish())
        return ClayEnrichResult(
            enriched_fields={},
            credits_used=0,
            task_status="not_configured",
        )

    result = await clay.enrich_contact(
        first_name=first_name,
        last_name=last_name,
        company_domain=company_domain,
        requested_data=[d for d in requested_data if d in ("email", "phone")],
        linkedin_url=linkedin_url,
    )
    log_ctx.add_api_call("clay")

    enriched_fields: dict[str, str] = {}
    status = result.get("status", "unknown")

    if status == "completed":
        contacts = result.get("results", [])
        if contacts:
            c = contacts[0]
            for key in ("email", "phone", "linkedinUrl", "title", "emailStatus"):
                if c.get(key):
                    enriched_fields[key] = str(c[key])
    elif status == "timeout":
        enriched_fields["_note"] = result.get("message", "Enrichment submitted to Clay")

    logger.info("tool completed", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields=enriched_fields,
        credits_used=result.get("creditsUsed", 0),
        task_status=status,
    )
