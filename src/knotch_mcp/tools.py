"""Orchestration functions for all 7 MCP tools.

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
from knotch_mcp.nicknames import get_name_variants
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
        company_domain=org.get("primary_domain"),
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
        if contact.gaps:
            contact.next_step = (
                "STOP and ask the user: 'Would you like me to run Clay "
                "enrichment to fill in the missing " + ", ".join(contact.gaps) + "?'"
            )
    else:
        contact.hubspot_status = "not_found"
        contact.suggested_actions.append("add_to_hubspot")
        contact.next_step = (
            "STOP and ask the user: 'Would you like me to add this contact "
            "to HubSpot?' Do NOT run Clay or any other tool until the user answers."
        )

    if contact.gaps:
        contact.suggested_actions.append("clay_enrich for " + ", ".join(contact.gaps))

    return contact


# ── Fallback helpers ────────────────────────────────────────────────


async def _finalize_contact(
    person: dict,
    log_ctx: ToolLogContext,
    hubspot: HubSpotClient,
    match_method: str,
) -> ContactResult:
    contact = _extract_contact(person)
    contact.match_method = match_method
    log_ctx.add_api_call("hubspot")
    contact = await _check_hubspot(contact, hubspot)
    logger.info(
        "tool completed (match_method=%s)", match_method, extra=log_ctx.finish()
    )
    return contact


async def _try_relaxed_match(
    first_name: str,
    last_name: str,
    domains: list[tuple[str, str]],
    email: str | None,
    linkedin_url: str | None,
    apollo: ApolloClient,
    log_ctx: ToolLogContext,
) -> tuple[dict | None, str]:
    if email:
        person = await apollo.people_match(email=email)
        log_ctx.add_api_call("apollo")
        if person:
            return person, "email"

    if linkedin_url:
        person = await apollo.people_match(linkedin_url=linkedin_url)
        log_ctx.add_api_call("apollo")
        if person:
            return person, "linkedin"

    name_variants = get_name_variants(first_name)
    primary_domain = domains[0][1] if domains else None
    if len(name_variants) > 1 and primary_domain:
        for variant in name_variants[1:]:
            person = await apollo.people_match(
                first_name=variant, last_name=last_name, domain=primary_domain
            )
            log_ctx.add_api_call("apollo")
            if person:
                return person, "nickname"

    for _, alt_domain in domains[1:]:
        person = await apollo.people_match(
            first_name=first_name, last_name=last_name, domain=alt_domain
        )
        log_ctx.add_api_call("apollo")
        if person:
            return person, "alternate_domain"

    return None, ""


async def _try_keyword_search(
    first_name: str,
    last_name: str,
    domain: str | None,
    company: str,
    apollo: ApolloClient,
    log_ctx: ToolLogContext,
) -> list[dict]:
    keywords = f"{first_name} {last_name}"
    people_raw, _ = await apollo.people_search(
        q_keywords=keywords, domain=domain, per_page=3
    )
    log_ctx.add_api_call("apollo")

    if not people_raw and domain:
        search_kw = f"{keywords} {company}" if not _is_domain(company) else keywords
        people_raw, _ = await apollo.people_search(q_keywords=search_kw, per_page=3)
        log_ctx.add_api_call("apollo")

    if not people_raw:
        return []

    enriched = []
    for stub in people_raw[:3]:
        person = await apollo.people_match(apollo_id=stub.get("id"))
        log_ctx.add_api_call("apollo")
        if person:
            enriched.append(person)
    return enriched


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
    """Three-tier Apollo cascade: exact → relaxed → keyword search."""
    log_ctx = ToolLogContext("find_contact_by_details")

    # ── Resolve company domain(s) ──
    if _is_domain(company):
        domains: list[tuple[str, str]] = [(company, company)]
    else:
        domains = await apollo.resolve_company_domains(company, limit=3)
        log_ctx.add_api_call("apollo")

    primary_domain = domains[0][1] if domains else None

    # ── Tier 1: Exact match ──
    person = await apollo.people_match(
        first_name=first_name,
        last_name=last_name,
        domain=primary_domain,
        organization_name=company if not _is_domain(company) else None,
        email=email,
        linkedin_url=linkedin_url,
    )
    log_ctx.add_api_call("apollo")

    if person:
        return await _finalize_contact(person, log_ctx, hubspot, "exact")

    # ── Tier 2: Relaxed match (email, linkedin, nicknames, alt domains) ──
    person, method = await _try_relaxed_match(
        first_name, last_name, domains, email, linkedin_url, apollo, log_ctx
    )
    if person:
        return await _finalize_contact(person, log_ctx, hubspot, method)

    # ── Tier 3: Keyword search ──
    candidates = await _try_keyword_search(
        first_name, last_name, primary_domain, company, apollo, log_ctx
    )
    if candidates:
        if len(candidates) == 1:
            return await _finalize_contact(
                candidates[0], log_ctx, hubspot, "keyword_search"
            )

        best = await _finalize_contact(
            candidates[0], log_ctx, hubspot, "keyword_search"
        )
        alt_summaries = []
        for c in candidates[1:]:
            org = c.get("organization") or {}
            alt_summaries.append(
                {
                    "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    "title": c.get("title"),
                    "company": org.get("name"),
                    "apollo_id": c.get("id"),
                }
            )
        best.alternate_matches = alt_summaries
        return best

    # ── No match ──
    contact = ContactResult(
        name=f"{first_name} {last_name}",
        company=company,
        sources=[],
        gaps=["no_match_after_fallback"],
        suggested_actions=["clay_enrich", "verify_spelling"],
    )
    logger.info("tool completed (no match)", extra=log_ctx.finish())
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
            if clay_status == "submitted":
                sources.append("clay (submitted — call check_clay_result later)")
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
    company_created = False
    company_name = None
    if company_domain:
        companies = await hubspot.search_companies_by_domain(company_domain)
        log_ctx.add_api_call("hubspot")
        if companies:
            company_id = companies[0]["id"]
            company_name = companies[0].get("properties", {}).get("name")
        else:
            company_props: dict[str, str] = {"domain": company_domain}
            if company:
                company_props["name"] = company
            new_company = await hubspot.create_company(company_props)
            log_ctx.add_api_call("hubspot")
            company_id = new_company["id"]
            company_name = company
            company_created = True

        await hubspot.associate_contact_to_company(hs_id, company_id)
        log_ctx.add_api_call("hubspot")
        company_associated = True

    logger.info("tool completed", extra=log_ctx.finish())
    return AddToHubSpotResult(
        hubspot_contact_id=hs_id,
        hubspot_url=hubspot.build_contact_url(hs_id),
        action=action,
        company_associated=company_associated,
        company_created=company_created,
        company_name=company_name,
    )


# ── Tool 6: clay_enrich ─────────────────────────────────────────────


CLAY_POLL_INTERVAL = 5.0
CLAY_POLL_TIMEOUT = 50.0


async def _clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str],
    clay: ClayClient,
    linkedin_url: str | None = None,
) -> ClayEnrichResult:
    """Fire Clay webhook and poll for the callback result."""
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

    status = result.get("status", "unknown")
    if status != "submitted":
        logger.info("tool completed (%s)", status, extra=log_ctx.finish())
        return ClayEnrichResult(
            enriched_fields={},
            credits_used=0,
            task_status=status,
        )

    correlation_id = result.get("correlationId", "")

    elapsed = 0.0
    while elapsed < CLAY_POLL_TIMEOUT:
        await asyncio.sleep(CLAY_POLL_INTERVAL)
        elapsed += CLAY_POLL_INTERVAL

        callback_data = clay.peek_result(correlation_id)
        if callback_data is not None:
            clay.get_result(correlation_id)
            enriched_fields: dict[str, str] = {}
            for key in ("email", "phone", "linkedinUrl", "title", "emailStatus"):
                val = callback_data.get(key)
                if val:
                    enriched_fields[key] = str(val)

            logger.info(
                "tool completed (callback received at %.0fs)",
                elapsed,
                extra=log_ctx.finish(),
            )
            return ClayEnrichResult(
                enriched_fields=enriched_fields,
                credits_used=callback_data.get("creditsUsed", 0),
                task_status="completed",
            )

    # Timeout — return correlationId for manual follow-up
    logger.info("tool completed (poll timeout)", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields={"correlationId": correlation_id},
        credits_used=0,
        task_status="timeout",
    )


# ── Tool 7: check_clay_result ──────────────────────────────────────


async def _check_clay_result(
    correlation_id: str,
    clay: ClayClient,
) -> ClayEnrichResult:
    """Retrieve stored Clay callback results by correlationId."""
    log_ctx = ToolLogContext("check_clay_result")

    result = clay.get_result(correlation_id)
    if result is None:
        logger.info("tool completed (pending)", extra=log_ctx.finish())
        return ClayEnrichResult(
            enriched_fields={},
            credits_used=0,
            task_status="pending",
        )

    enriched_fields: dict[str, str] = {}
    for key in ("email", "phone", "linkedinUrl", "title", "emailStatus"):
        val = result.get(key)
        if val:
            enriched_fields[key] = str(val)

    logger.info("tool completed", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields=enriched_fields,
        credits_used=result.get("creditsUsed", 0),
        task_status="completed",
    )
