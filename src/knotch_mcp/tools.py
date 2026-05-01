"""Orchestration functions for all 7 MCP tools.

Each underscore-prefixed function contains the business logic for one tool.
The MCP server module registers thin wrappers that parse input models and
forward to these functions with the appropriate client instances.
"""

from __future__ import annotations

import asyncio

import httpx

from knotch_mcp.clients.apollo import ApolloAPIError, ApolloClient
from knotch_mcp.clients.clay import ClayClient
from knotch_mcp.clients.hubspot import HubSpotClient
from knotch_mcp.log import ToolLogContext, get_logger
from knotch_mcp.nicknames import get_name_variants
from knotch_mcp.models import (
    AddToHubSpotResult,
    AssociateResult,
    ClayEnrichResult,
    ContactResult,
    EnrichContactResult,
    FindContactsResult,
    FindPhoneResult,
    UpdateResult,
)

logger = get_logger("knotch_mcp.tools")


# ── Error helpers ──────────────────────────────────────────────────


def _friendly_hubspot_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        messages = {
            400: "Invalid request — check property names and values.",
            401: "Authentication failed — HubSpot token may be expired.",
            403: "Permission denied — HubSpot token lacks access for this object.",
            404: "Object not found in HubSpot — verify the ID is correct.",
            409: "Conflict — this record may have been modified concurrently.",
            429: "HubSpot rate limit hit — wait a moment and retry.",
        }
        return messages.get(status, f"HubSpot returned HTTP {status}.")
    if isinstance(exc, httpx.TimeoutException):
        return "HubSpot request timed out — try again."
    return f"HubSpot error: {type(exc).__name__}"


# ── Clay field extraction ────────────────────────────────────────

_CLAY_SKIP_KEYS = frozenset(
    {
        "firstname",
        "lastname",
        "companydomain",
        "requesteddata",
        "correlationid",
        "creditsused",
    }
)


def _extract_clay_fields(callback_data: dict) -> dict[str, str]:
    """Extract enrichment fields from Clay callback data.

    Clay tables use user-defined column names, so we match flexibly
    rather than requiring exact key names.
    """

    def _norm(key: str) -> str:
        return key.lower().replace(" ", "").replace("_", "").replace("-", "")

    fields: dict[str, str] = {}
    for key, val in callback_data.items():
        if not val:
            continue
        nk = _norm(key)
        if nk in _CLAY_SKIP_KEYS:
            continue
        if "emailstatus" in nk or "emailverif" in nk or "emailvalid" in nk:
            fields.setdefault("emailStatus", str(val))
        elif "phone" in nk or "mobile" in nk or "directdial" in nk:
            fields.setdefault("phone", str(val))
        elif "email" in nk:
            fields.setdefault("email", str(val))
        elif "linkedin" in nk:
            fields.setdefault("linkedinUrl", str(val))
        elif nk in ("title", "jobtitle"):
            fields.setdefault("title", str(val))
    return fields


def _is_domain(company: str) -> bool:
    """Return True if *company* looks like a domain (contains a dot)."""
    return "." in company


_FREEMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "protonmail.com",
        "live.com",
        "msn.com",
        "ymail.com",
        "me.com",
        "mail.com",
    }
)

_COMPANY_STOPWORDS = frozenset(
    {
        "the",
        "of",
        "and",
        "a",
        "an",
        "in",
        "for",
        "inc",
        "llc",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "limited",
        "group",
        "plc",
        "sa",
        "gmbh",
        "association",
        "society",
        "institute",
        "foundation",
    }
)


def _significant_words(name: str) -> set[str]:
    """Extract significant words from a company name for fuzzy matching."""
    words = {
        w.rstrip(".,;:!?").replace("®", "").replace("™", "")
        for w in name.lower().split()
    }
    return words - _COMPANY_STOPWORDS - {""}


def _is_phantom(person: dict) -> bool:
    """True when Apollo returned an ID but no real data."""
    if not person:
        return True
    org = person.get("organization") or {}
    return not any(
        [
            person.get("email"),
            person.get("title"),
            person.get("linkedin_url"),
            org.get("name"),
        ]
    )


def _is_thin(person: dict) -> bool:
    """True when >=2 of (email, title, linkedin_url) are null."""
    if not person:
        return True
    nulls = sum(
        1
        for f in [person.get("email"), person.get("title"), person.get("linkedin_url")]
        if not f
    )
    return nulls >= 2


def _company_matches(
    person: dict, requested: str, domains: list[tuple[str, str]]
) -> bool:
    """Check if a person's company plausibly matches the requested company."""
    org = person.get("organization") or {}
    pname = (org.get("name") or "").lower()
    pdomain = (org.get("primary_domain") or "").lower()
    req = requested.lower()
    if req and pname and (req in pname or pname in req):
        return True
    if req and pname:
        req_words = _significant_words(req)
        p_words = _significant_words(pname)
        if req_words and p_words:
            overlap = req_words & p_words
            smaller = min(len(req_words), len(p_words))
            if len(overlap) >= 2 and len(overlap) / smaller > 0.5:
                return True
    known = {d.lower() for _, d in domains}
    if pdomain and pdomain in known:
        return True
    if _is_domain(requested) and pdomain == req:
        return True
    return False


def _alt_summary(person: dict) -> dict:
    """Build a rich alternate-match summary from a hydrated Apollo person."""
    return _extract_contact(person).model_dump(exclude_none=True)


def _extract_contact(person: dict) -> ContactResult:
    """Convert an Apollo person dict into a ContactResult."""
    org = person.get("organization") or {}
    phone_numbers = person.get("phone_numbers") or []
    phone = phone_numbers[0].get("raw_number") if phone_numbers else None
    phone_type = phone_numbers[0].get("type") if phone_numbers else None

    city = person.get("city") or ""
    state = person.get("state") or ""
    country = person.get("country") or ""
    parts = [p for p in [city, state, country] if p]
    location = ", ".join(parts) if parts else None

    company = org.get("name")
    company_domain = org.get("primary_domain")

    if person.get("email") and "@" in person.get("email", ""):
        email_domain = person["email"].split("@")[1].lower()
        if (
            email_domain not in _FREEMAIL_DOMAINS
            and company_domain
            and email_domain != company_domain.lower()
        ):
            company_domain = email_domain

    if not company and person.get("email") and "@" in person.get("email", ""):
        email_domain = person["email"].split("@")[1].lower()
        if email_domain not in _FREEMAIL_DOMAINS:
            if not company_domain:
                company_domain = email_domain
            company = email_domain.split(".")[0].capitalize()

    gaps: list[str] = []
    if not person.get("email"):
        gaps.append("email")
    if not person.get("linkedin_url"):
        gaps.append("linkedin_url")
    if not phone:
        gaps.append("phone")

    return ContactResult(
        name=f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip(),
        title=person.get("title"),
        company=company,
        email=person.get("email"),
        email_status=person.get("email_status"),
        linkedin_url=person.get("linkedin_url"),
        location=location,
        apollo_id=person.get("id"),
        phone=phone,
        phone_type=phone_type,
        company_domain=company_domain,
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

    has_issues = contact.confidence == "low" or any(
        kw in w for w in contact.warnings for kw in ("mismatch", "phantom", "thin")
    )

    if results:
        hs_id = results[0]["id"]
        contact.hubspot_status = "found"
        contact.hubspot_contact_id = hs_id
        contact.hubspot_url = hubspot.build_contact_url(hs_id)
        if contact.gaps and not has_issues:
            contact.next_step = (
                "STOP and ask: 'Run Clay enrichment for missing "
                + ", ".join(contact.gaps)
                + "?'"
            )
        elif has_issues:
            contact.next_step = (
                "STOP: This result has low confidence ("
                + "; ".join(contact.warnings)
                + "). Ask the user to verify before proceeding."
            )
    else:
        contact.hubspot_status = "not_found"
        contact.suggested_actions.append("add_to_hubspot")
        if not has_issues:
            contact.next_step = (
                "STOP and ask: 'Would you like me to add this contact "
                "to HubSpot?' Do NOT run Clay first."
            )
        else:
            contact.next_step = (
                "STOP: Low confidence result ("
                + "; ".join(contact.warnings)
                + "). Ask user to verify this is the right person before adding to HubSpot."
            )

    if contact.gaps:
        contact.suggested_actions.append("clay_enrich for " + ", ".join(contact.gaps))

    return contact


# ── Fallback helpers ────────────────────────────────────────────────


_CONFIDENCE_BY_METHOD = {
    "exact": "high",
    "email": "high",
    "linkedin": "high",
    "lookup": "high",
    "nickname": "medium",
    "alternate_domain": "medium",
    "keyword_search": "medium",
}


async def _finalize_contact(
    person: dict,
    log_ctx: ToolLogContext,
    hubspot: HubSpotClient,
    match_method: str,
    confidence: str | None = None,
    warnings: list[str] | None = None,
) -> ContactResult:
    contact = _extract_contact(person)
    contact.match_method = match_method
    contact.confidence = confidence or _CONFIDENCE_BY_METHOD.get(match_method, "low")
    if warnings:
        contact.warnings.extend(warnings)
    log_ctx.add_api_call("hubspot")
    contact = await _check_hubspot(contact, hubspot)
    logger.info(
        "tool completed (match_method=%s)", match_method, extra=log_ctx.finish()
    )
    return contact


def _qa_result(
    contact: ContactResult,
    searched_company: str | None,
    had_email: bool,
    had_linkedin: bool,
) -> ContactResult:
    """QA a contact result: validate data, replace generic suggestions with
    specific enrichment recommendations, and add disambiguation guidance."""

    contact.suggested_actions = [
        a for a in contact.suggested_actions if not a.startswith("clay_enrich for ")
    ]

    if contact.email and "@" in contact.email:
        email_domain = contact.email.split("@")[1].lower()
        if email_domain in _FREEMAIL_DOMAINS:
            contact.warnings.append(
                f"personal_email: using {email_domain}, no corporate email on file"
            )

    if contact.email_status and contact.email_status not in ("verified", "valid"):
        contact.warnings.append(
            f"email_risky: status '{contact.email_status}' — may bounce"
        )

    if not contact.email:
        if contact.company_domain:
            contact.suggested_actions.append(
                f"clay_enrich for email (domain: {contact.company_domain})"
            )
        else:
            contact.suggested_actions.append(
                "need company_domain to run clay_enrich for email"
            )

    if not contact.phone:
        if contact.apollo_id:
            contact.suggested_actions.append(
                f"find_phone (apollo_id: {contact.apollo_id})"
            )
        if contact.company_domain:
            contact.suggested_actions.append(
                f"clay_enrich for phone (domain: {contact.company_domain})"
            )

    if contact.confidence in ("low", "medium"):
        if not had_linkedin and not contact.linkedin_url:
            contact.suggested_actions.append("provide LinkedIn URL to confirm identity")
        if not had_email and not contact.email:
            contact.suggested_actions.append("provide email for exact match")
        if not searched_company:
            contact.suggested_actions.append("provide company name for better results")

    if any("company_changed" in w for w in contact.warnings):
        moved_warning = next(w for w in contact.warnings if "company_changed" in w)
        if contact.hubspot_status == "not_found":
            contact.next_step = (
                f"STOP: {moved_warning}. "
                "Confirm with the user this is the right person, then offer "
                "to add to HubSpot."
            )
        else:
            contact.next_step = (
                f"STOP: {moved_warning}. "
                "Confirm with the user — check if their HubSpot record needs "
                "a company update."
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
        people_raw, _ = await apollo.people_search(q_keywords=keywords, per_page=5)
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
    company: str | None,
    email: str | None,
    linkedin_url: str | None,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> ContactResult:
    """Three-tier Apollo cascade: exact → relaxed → keyword search.
    Company is optional — without it, anchors on email/linkedin or
    falls back to name-only keyword search."""
    log_ctx = ToolLogContext("find_contact_by_details")
    had_email = bool(email)
    had_linkedin = bool(linkedin_url)

    def qa(contact: ContactResult) -> ContactResult:
        return _qa_result(contact, company, had_email, had_linkedin)

    try:
        return await _find_contact_by_details_inner(
            first_name,
            last_name,
            company,
            email,
            linkedin_url,
            apollo,
            hubspot,
            log_ctx,
            qa,
        )
    except ApolloAPIError as exc:
        logger.warning("Apollo error in find_contact_by_details: %s", exc)
        return ContactResult(
            name=f"{first_name} {last_name}",
            company=company or None,
            sources=[],
            gaps=["api_error"],
            confidence="low",
            warnings=[f"Apollo API error: {exc.message}"],
            next_step="Apollo is unavailable. Try again in a moment, or provide an email/LinkedIn URL for a direct HubSpot lookup.",
        )


async def _find_contact_by_details_inner(
    first_name: str,
    last_name: str,
    company: str | None,
    email: str | None,
    linkedin_url: str | None,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
    log_ctx: ToolLogContext,
    qa,
) -> ContactResult:
    had_email = bool(email)
    had_linkedin = bool(linkedin_url)

    # ── Resolve company domain(s) ──
    if not company:
        domains: list[tuple[str, str]] = []
    elif _is_domain(company):
        domains = [(company, company)]
    else:
        domains = await apollo.resolve_company_domains(company, limit=3)
        log_ctx.add_api_call("apollo")

    primary_domain = domains[0][1] if domains else None
    thin_fallback: dict | None = None
    wrong_company_stash: dict | None = None

    # ── Tier 1: Exact match ──
    person = await apollo.people_match(
        first_name=first_name,
        last_name=last_name,
        domain=primary_domain,
        organization_name=company if company and not _is_domain(company) else None,
        email=email,
        linkedin_url=linkedin_url,
    )
    log_ctx.add_api_call("apollo")

    if person and not _is_phantom(person):
        if company and not _company_matches(person, company, domains):
            current_org = (person.get("organization") or {}).get("name", "unknown")
            if not _is_thin(person):
                return qa(
                    await _finalize_contact(
                        person,
                        log_ctx,
                        hubspot,
                        "exact",
                        confidence="medium",
                        warnings=[
                            f"company_changed: now at {current_org}, searched {company}"
                        ],
                    )
                )
            logger.info("exact match company mismatch (thin), continuing cascade")
            wrong_company_stash = person
            person = None
        elif _is_thin(person):
            logger.info("exact match is thin, continuing cascade")
            thin_fallback = person
            person = None
        else:
            return qa(await _finalize_contact(person, log_ctx, hubspot, "exact"))
    elif person:
        logger.info("phantom record discarded: id=%s", person.get("id"))
        person = None

    # ── Tier 2: Relaxed match (email, linkedin, nicknames, alt domains) ──
    person, method = await _try_relaxed_match(
        first_name, last_name, domains, email, linkedin_url, apollo, log_ctx
    )
    if person and not _is_phantom(person):
        if _is_thin(person) and not thin_fallback:
            thin_fallback = person
            person = None
        elif not _is_thin(person):
            return qa(await _finalize_contact(person, log_ctx, hubspot, method))
        else:
            person = None

    # ── Tier 3: Keyword search ──
    candidates = await _try_keyword_search(
        first_name, last_name, primary_domain, company or "", apollo, log_ctx
    )
    candidates = [c for c in candidates if not _is_phantom(c)]
    if company:
        matching = [c for c in candidates if _company_matches(c, company, domains)]
        non_matching = [
            c for c in candidates if not _company_matches(c, company, domains)
        ]
        candidates = matching if matching else candidates
    else:
        non_matching = []

    if candidates:
        best = await _finalize_contact(
            candidates[0], log_ctx, hubspot, "keyword_search"
        )
        alt_summaries = [_alt_summary(c) for c in candidates[1:]]
        alt_summaries.extend(_alt_summary(c) for c in non_matching)
        if wrong_company_stash:
            alt_summaries.append(_alt_summary(wrong_company_stash))
        if alt_summaries:
            best.alternate_matches = alt_summaries
            n = len(alt_summaries)
            best.next_step = (
                f"STOP and present this result AND the {n} alternate match"
                f"{'es' if n > 1 else ''} to the user. Ask: 'Is this the right "
                "person, or would you like me to look up one of the alternates?' "
                "If they pick an alternate, use lookup_contact with the apollo_id."
            )
        return qa(best)

    # ── Thin fallback ──
    if thin_fallback:
        contact = await _finalize_contact(
            thin_fallback,
            log_ctx,
            hubspot,
            "exact",
            confidence="low",
            warnings=["thin_record: limited data available"],
        )
        if wrong_company_stash:
            contact.alternate_matches = [_alt_summary(wrong_company_stash)]
            contact.next_step = (
                "STOP and present this result AND the 1 alternate match "
                "to the user. Ask: 'Is this the right person, or would you "
                "like me to look up the alternate?' "
                "If they pick the alternate, use lookup_contact with the apollo_id."
            )
        return qa(contact)

    # ── No match ──
    no_match_actions: list[str] = ["verify_spelling"]
    no_match_tips: list[str] = []
    if not had_email:
        no_match_tips.append("email")
    if not had_linkedin:
        no_match_tips.append("LinkedIn URL")
    if not company:
        no_match_tips.append("company name")
    if no_match_tips:
        no_match_actions.append(
            "provide " + " or ".join(no_match_tips) + " for better matching"
        )
    no_match_actions.append("clay_enrich as last resort")

    contact = ContactResult(
        name=f"{first_name} {last_name}",
        company=company or None,
        sources=[],
        gaps=["no_match"],
        suggested_actions=no_match_actions,
        confidence="low",
        next_step=(
            "STOP and tell the user: 'No match found.' Then suggest: "
            + "; ".join(no_match_actions)
        ),
    )
    if wrong_company_stash:
        contact.alternate_matches = [_alt_summary(wrong_company_stash)]
        contact.next_step = (
            "STOP: No exact match, but found 1 possible match at a different "
            "company. Present the alternate to the user. Ask: 'Is this the "
            "person you meant?' If yes, use lookup_contact with the apollo_id."
        )
    logger.info("tool completed (no match)", extra=log_ctx.finish())
    return qa(contact)


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

    domain: str | None = None
    if _is_domain(company):
        domain = company
    else:
        try:
            domain = await apollo.resolve_company_domain(company)
        except ApolloAPIError as exc:
            logger.warning("Apollo error resolving domain for %s: %s", company, exc)
            return FindContactsResult(
                candidates=[],
                total_available=0,
                warnings=[f"Apollo API error: {exc.message}"],
                next_step="Apollo is unavailable. Try again, or provide the company domain directly (e.g. 'stripe.com').",
            )
        log_ctx.add_api_call("apollo")
        if not domain:
            logger.warning("could not resolve domain for %s", company)

    domain_warning: str | None = None
    if not domain and not _is_domain(company):
        domain_warning = f"Could not resolve domain for '{company}'. Results may include contacts from other companies with similar names."

    seniority_list = [seniority] if seniority else None
    try:
        people_raw, total = await apollo.people_search(
            titles=titles,
            domain=domain,
            seniority=seniority_list,
            per_page=limit,
        )
    except ApolloAPIError as exc:
        logger.warning("Apollo search error: %s", exc)
        return FindContactsResult(
            candidates=[],
            total_available=0,
            warnings=[f"Apollo API error: {exc.message}"],
            next_step="Apollo is unavailable. Try again in a moment.",
        )
    log_ctx.add_api_call("apollo")

    if total == 0:
        logger.info("tool completed (no results)", extra=log_ctx.finish())
        suggestions = [
            "Try broader titles (e.g. 'Marketing' instead of 'Content Marketing Manager')."
        ]
        if seniority:
            suggestions.append("Remove the seniority filter to widen the search.")
        if not _is_domain(company):
            suggestions.append(
                f"Provide the exact domain (e.g. 'company.com') instead of '{company}'."
            )
        return FindContactsResult(
            candidates=[],
            total_available=0,
            warnings=[domain_warning] if domain_warning else [],
            next_step="No results found. " + " ".join(suggestions),
        )

    async def enrich_one(person_stub: dict) -> ContactResult:
        person = await apollo.people_match(
            first_name=person_stub.get("first_name"),
            last_name=person_stub.get("last_name"),
            apollo_id=person_stub.get("id"),
        )
        if not person:
            return ContactResult(
                name=f"{person_stub.get('first_name') or ''} {person_stub.get('last_name') or ''}".strip(),
                title=person_stub.get("title"),
                sources=["apollo"],
                gaps=["enrichment_failed"],
            )
        contact = _extract_contact(person)
        try:
            contact = await _check_hubspot(contact, hubspot)
        except Exception as exc:
            logger.warning("HubSpot check failed for %s: %s", contact.name, exc)
            contact.hubspot_status = "error"
            contact.warnings.append(f"HubSpot lookup failed: {type(exc).__name__}")
        return contact

    candidates = list(
        await asyncio.gather(*[enrich_one(p) for p in people_raw[:limit]])
    )
    log_ctx.add_api_call("apollo")
    log_ctx.add_api_call("hubspot")

    if domain:
        filtered = [
            c
            for c in candidates
            if c.company_domain and c.company_domain.lower() == domain.lower()
        ]
        if not filtered:
            filtered = [
                c
                for c in candidates
                if c.company and company.lower() in (c.company or "").lower()
            ]
        if filtered:
            candidates = filtered

    logger.info("tool completed", extra=log_ctx.finish())
    return FindContactsResult(
        candidates=candidates,
        total_available=total,
        warnings=[domain_warning] if domain_warning else [],
    )


# ── Tool 3: find_phone ───────────────────────────────────────────────


async def _find_phone(
    apollo_id: str | None,
    email: str | None,
    linkedin_url: str | None,
    name: str | None,
    apollo: ApolloClient,
) -> FindPhoneResult:
    """Apollo match with reveal_phone_number to find a direct dial."""
    if not any([apollo_id, email, linkedin_url, name]):
        return FindPhoneResult(
            found=False,
            source="apollo",
            suggested_action="Provide at least one identifier: apollo_id, email, linkedin_url, or name.",
        )

    log_ctx = ToolLogContext("find_phone")

    first_name = None
    last_name = None
    if name:
        parts = name.split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None

    try:
        person = await apollo.people_match(
            apollo_id=apollo_id,
            email=email,
            linkedin_url=linkedin_url,
            first_name=first_name,
            last_name=last_name,
            reveal_phone_number=True,
        )
    except ApolloAPIError as exc:
        logger.warning("Apollo error in find_phone: %s", exc)
        return FindPhoneResult(
            found=False,
            source="apollo",
            suggested_action=f"Apollo error: {exc.message}. Try clay_enrich instead.",
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
            phone=phone.get("raw_number"),
            phone_type=phone.get("type"),
            source="apollo",
            confidence="high" if len(phone_numbers) == 1 else "medium",
            found=True,
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return FindPhoneResult(found=False, source="apollo", suggested_action="clay_enrich")


# ── Tool 3b: lookup_contact ─────────────────────────────────────────


async def _lookup_contact(
    apollo_id: str,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> ContactResult:
    """Look up a single contact by Apollo ID, returning full data + HubSpot check."""
    log_ctx = ToolLogContext("lookup_contact")
    try:
        person = await apollo.people_match(apollo_id=apollo_id)
    except ApolloAPIError as exc:
        logger.warning("Apollo error in lookup_contact: %s", exc)
        return ContactResult(
            name="",
            sources=[],
            gaps=["api_error"],
            confidence="low",
            warnings=[f"Apollo API error: {exc.message}"],
            next_step="Apollo is unavailable. Try again in a moment.",
        )
    log_ctx.add_api_call("apollo")

    if not person or _is_phantom(person):
        logger.info("tool completed (not found)", extra=log_ctx.finish())
        return ContactResult(
            name="",
            sources=[],
            gaps=["lookup_failed"],
            confidence="low",
            next_step="Apollo ID not found. Try find_contact_by_details instead.",
        )

    return await _finalize_contact(
        person, log_ctx, hubspot, "lookup", confidence="high"
    )


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

    try:
        person = await apollo.people_match(
            first_name=props.get("firstname"),
            last_name=props.get("lastname"),
            email=props.get("email"),
            linkedin_url=props.get("hs_linkedin_url"),
            reveal_phone_number=True,
        )
    except ApolloAPIError as exc:
        logger.warning("Apollo error in enrich_contact, continuing with Clay: %s", exc)
        person = None
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
        domain = None
        contact_email = filled.get("email") or props.get("email") or ""
        if "@" in contact_email:
            email_domain = contact_email.split("@")[1].lower()
            if email_domain not in _FREEMAIL_DOMAINS:
                domain = email_domain
        if not domain and person:
            domain = (person.get("organization") or {}).get("primary_domain")
        if not domain:
            company_val = props.get("company", "")
            if "." in company_val:
                domain = company_val
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

    try:
        if existing:
            hs_id = existing[0].get("id")
            if not hs_id:
                return AddToHubSpotResult(
                    error="HubSpot returned a contact without an ID."
                )
            await hubspot.update_contact(hs_id, properties)
            action = "updated"
        else:
            result = await hubspot.create_contact(properties)
            hs_id = result.get("id")
            if not hs_id:
                return AddToHubSpotResult(
                    error="HubSpot created a contact but returned no ID."
                )
            action = "created"
    except Exception as exc:
        logger.warning("add_to_hubspot create/update failed: %s", exc)
        return AddToHubSpotResult(error=_friendly_hubspot_error(exc))
    log_ctx.add_api_call("hubspot")

    company_associated = False
    company_created = False
    company_name = None
    if company_domain:
        try:
            companies = await hubspot.search_companies_by_domain(company_domain)
            log_ctx.add_api_call("hubspot")
            if companies:
                company_id = companies[0].get("id")
                company_name = companies[0].get("properties", {}).get("name")
            else:
                company_props: dict[str, str] = {"domain": company_domain}
                if company:
                    company_props["name"] = company
                new_company = await hubspot.create_company(company_props)
                log_ctx.add_api_call("hubspot")
                company_id = new_company.get("id")
                company_name = company
                company_created = True

            if company_id:
                await hubspot.associate_contact_to_company(hs_id, company_id)
                log_ctx.add_api_call("hubspot")
                company_associated = True
        except Exception as exc:
            logger.warning("company association failed: %s", exc)

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
            logger.info(
                "clay callback payload keys: %s",
                list(callback_data.keys()),
            )
            enriched_fields = _extract_clay_fields(callback_data)

            credits = callback_data.get("creditsUsed", 0)
            warnings: list[str] = []
            if credits > 0 and not enriched_fields:
                warnings.append(
                    f"Clay used {credits} credit(s) but returned no data for this person."
                )

            logger.info(
                "tool completed (callback received at %.0fs)",
                elapsed,
                extra=log_ctx.finish(),
            )
            return ClayEnrichResult(
                enriched_fields=enriched_fields,
                credits_used=credits,
                task_status="completed",
                warnings=warnings,
                next_step=(
                    "STOP and ask: 'Add to HubSpot?'"
                    if enriched_fields
                    else "Clay returned no data. Ask user if they want to try different search terms."
                ),
            )

    # Timeout — return correlationId for manual follow-up
    logger.info("tool completed (poll timeout)", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields={"correlationId": correlation_id},
        credits_used=0,
        task_status="timeout",
        next_step=(
            "STOP and ask the user: 'Would you like me to add this "
            "contact to HubSpot?' Do NOT proceed without the user's answer."
        ),
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

    logger.info("clay callback payload keys: %s", list(result.keys()))
    enriched_fields = _extract_clay_fields(result)

    credits = result.get("creditsUsed", 0)
    warnings: list[str] = []
    if credits > 0 and not enriched_fields:
        warnings.append(
            f"Clay used {credits} credit(s) but returned no data for this person."
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields=enriched_fields,
        credits_used=credits,
        task_status="completed",
        warnings=warnings,
        next_step=(
            "STOP and ask: 'Add to HubSpot?'"
            if enriched_fields
            else "Clay returned no data. Ask user if they want to try different search terms."
        ),
    )


# ── Write tools ──────────────────────────────────────────────────

_WRITABLE_TYPES = {"contacts", "deals", "companies"}


async def _update_object(
    object_type: str,
    object_id: str,
    properties: dict,
    hubspot: HubSpotClient,
) -> UpdateResult:
    """Update properties on a contact, deal, or company."""
    log_ctx = ToolLogContext(f"update_{object_type}")

    if object_type not in _WRITABLE_TYPES:
        logger.info("tool completed (rejected)", extra=log_ctx.finish())
        return UpdateResult(
            object_type=object_type,
            object_id=object_id,
            success=False,
            error=f"Write access limited to {_WRITABLE_TYPES}. Got: {object_type}",
        )

    try:
        if object_type == "contacts":
            await hubspot.update_contact(object_id, properties)
        elif object_type == "deals":
            await hubspot.update_deal(object_id, properties)
        elif object_type == "companies":
            await hubspot.update_company(object_id, properties)
        log_ctx.add_api_call("hubspot")
    except Exception as exc:
        logger.warning("update failed: %s", exc, extra=log_ctx.finish())
        return UpdateResult(
            object_type=object_type,
            object_id=object_id,
            success=False,
            error=str(exc),
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return UpdateResult(
        object_type=object_type,
        object_id=object_id,
        updated_properties=list(properties.keys()),
    )


async def _associate_contact_to_deal(
    contact_id: str,
    deal_id: str,
    hubspot: HubSpotClient,
) -> AssociateResult:
    """Associate a contact to a deal."""
    log_ctx = ToolLogContext("associate_contact_to_deal")

    try:
        await hubspot.associate_objects("contacts", contact_id, "deals", deal_id)
        log_ctx.add_api_call("hubspot")
    except Exception as exc:
        logger.warning("association failed: %s", exc, extra=log_ctx.finish())
        return AssociateResult(
            from_type="contacts",
            from_id=contact_id,
            to_type="deals",
            to_id=deal_id,
            success=False,
            error=str(exc),
        )

    logger.info("tool completed", extra=log_ctx.finish())
    return AssociateResult(
        from_type="contacts",
        from_id=contact_id,
        to_type="deals",
        to_id=deal_id,
    )
