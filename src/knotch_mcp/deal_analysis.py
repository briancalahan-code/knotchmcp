"""Deal-level buyer role and engagement analysis.

Given a HubSpot deal, performs comprehensive analysis of associated contacts,
meeting attendees, email participants, and recommends buyer role assignments
and CRM edits based on Knotch's SPICED/buying committee framework.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from knotch_mcp.clients.hubspot import (
    DEAL_CONTACT_PROPERTIES,
    EMAIL_PROPERTIES,
    MEETING_PROPERTIES,
    HubSpotClient,
)
from knotch_mcp.log import ToolLogContext, get_logger
from knotch_mcp.models import (
    ContactToAdd,
    DealAnalysisResult,
    DealContactProfile,
    RecommendedEdit,
    RoleAssignment,
    SPICEDAnalysis,
    SPICEDElement,
    StageGapAnalysis,
)

logger = get_logger("knotch_mcp.deal_analysis")


# ── Knotch buying committee framework ────────────────────────────

BUYER_ROLES = [
    "Economic Buyer",
    "Champion",
    "Executive Sponsor",
    "Influencer",
    "Technical Validator",
    "End User",
    "Blocker",
]

STAGE_REQUIREMENTS: dict[str, dict] = {
    "ipm": {
        "label": "IPM Set/Held",
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
    "ipm set": "ipm",
    "ipm held": "ipm",
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


# ── Title → role mapping ─────────────────────────────────────────

_TITLE_ROLE_RULES: list[tuple[list[str], str]] = [
    (["ceo", "cfo", "coo", "cio"], "Economic Buyer"),
    (["cmo", "cto"], "Executive Sponsor"),
    (["chief"], "Economic Buyer"),
    (["vp ", "vice president"], "Economic Buyer"),
    (
        ["svp", "evp", "senior vice president", "executive vice president"],
        "Economic Buyer",
    ),
    (["legal", "counsel", "compliance"], "Blocker"),
    (["procurement", "purchasing", "sourcing"], "Blocker"),
    (["finance", "fp&a", "controller", "accounting", "treasurer"], "Economic Buyer"),
    (
        [
            "it ",
            "information technology",
            "martech",
            "analytics",
            "data engineer",
            "data science",
        ],
        "Technical Validator",
    ),
    (["engineer", "developer", "architect"], "Technical Validator"),
    (["head of"], "Influencer"),
    (["director"], "Influencer"),
    (["manager", "assistant director"], "Champion"),
    (["specialist", "analyst", "coordinator", "associate"], "End User"),
    (["ai ", "innovation", "strategy"], "Influencer"),
    (["marketing"], "Influencer"),
    (["sales"], "Champion"),
    (["content", "editorial", "brand"], "End User"),
]


_WORD_BOUNDARY_PATTERNS = {"ceo", "cfo", "cmo", "cto", "coo", "cio", "svp", "evp"}

_HIGH_CONFIDENCE = {
    "chief",
    "ceo",
    "cfo",
    "cmo",
    "cto",
    "coo",
    "cio",
    "vp ",
    "vice president",
}


def _pattern_matches(pattern: str, text: str) -> bool:
    if pattern in _WORD_BOUNDARY_PATTERNS:
        return bool(re.search(rf"\b{re.escape(pattern)}\b", text))
    return pattern in text


# ── Persona / seniority → role mapping ─────────────────────────

_PERSONA_ROLE_RULES: list[tuple[list[str], str]] = [
    (["executive", "c-suite"], "Economic Buyer"),
    (["finance", "accounting", "controller"], "Economic Buyer"),
    (["legal", "compliance", "counsel"], "Blocker"),
    (["procurement", "purchasing", "sourcing"], "Blocker"),
    (
        [
            "information technology",
            "technical",
            "engineering",
            "developer",
            "data science",
        ],
        "Technical Validator",
    ),
    (["marketing", "demand gen"], "Influencer"),
    (["sales", "business development", "revenue"], "Champion"),
    (["operations", "strategy", "consulting"], "Influencer"),
    (["content", "editorial", "brand", "creative"], "End User"),
]

_PERSONA_EXACT_MAP: dict[str, str] = {
    "it": "Technical Validator",
}

_SENIORITY_EXECUTIVE_KEYS = ("c-suite", "c_suite", "executive", "c suite")

_SENIORITY_ROLE_MAP: list[tuple[list[str], str]] = [
    (["director"], "Influencer"),
    (["manager"], "Champion"),
    (["senior"], "Influencer"),
    (["individual contributor", "ic"], "End User"),
    (["entry", "intern"], "End User"),
]


def _infer_role_from_title(title: str | None) -> tuple[str | None, str]:
    if not title:
        return None, "LOW"
    t = title.lower()
    for patterns, role in _TITLE_ROLE_RULES:
        for p in patterns:
            if _pattern_matches(p, t):
                confidence = (
                    "HIGH"
                    if any(_pattern_matches(x, t) for x in _HIGH_CONFIDENCE)
                    else "MEDIUM"
                )
                return role, confidence
    return None, "LOW"


def _infer_role(
    title: str | None = None,
    persona: str | None = None,
    seniority: str | None = None,
) -> tuple[str | None, str]:
    """Layered role inference: seniority C-suite → persona → seniority → title."""
    if seniority:
        s = seniority.lower().strip()
        if any(k in s for k in _SENIORITY_EXECUTIVE_KEYS):
            return "Economic Buyer", "HIGH"
        if "vp" in s or "vice president" in s:
            return "Economic Buyer", "HIGH"

    if persona:
        p = persona.lower().strip()
        if p in _PERSONA_EXACT_MAP:
            return _PERSONA_EXACT_MAP[p], "HIGH"
        for keywords, role in _PERSONA_ROLE_RULES:
            if any(k in p for k in keywords):
                return role, "HIGH"

    if seniority:
        s = seniority.lower().strip()
        for keywords, role in _SENIORITY_ROLE_MAP:
            if any(k in s for k in keywords):
                return role, "HIGH"

    return _infer_role_from_title(title)


# ── Signal parsing ───────────────────────────────────────────────

_SIGNAL_PATTERNS: list[tuple[str, str, str]] = [
    (r"(?:key )?decision[- ]?maker", "Economic Buyer", "decision-maker reference"),
    (r"\bbudget\b|\bpricing\b|\bcost\b", "Economic Buyer", "budget/pricing discussion"),
    (
        r"\btechnical\b|\bintegration\b|\bapi\b|\bimplementation\b",
        "Technical Validator",
        "technical discussion",
    ),
    (
        r"\bblocker\b|\bconcern\b|\brisk\b|\bobjection\b",
        "Blocker",
        "risk/objection signal",
    ),
    (
        r"\bchampion\b|\badvocate\b|\bsponsor\b",
        "Champion",
        "champion/advocate reference",
    ),
    (
        r"\bex-|\bformer\b|\bleft\b|\bno longer\b|\bdeparted\b",
        "PERSONNEL_CHANGE",
        "personnel change signal",
    ),
    (
        r"\bnew hire\b|\bjust started\b|\brecently joined\b",
        "PERSONNEL_CHANGE",
        "new hire signal",
    ),
    (
        r"\breally hoping\b|\bexcited about\b|\blooking forward\b|\benthusiastic\b",
        "Champion",
        "enthusiasm signal",
    ),
]


def _parse_signals(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    signals = []
    for pattern, role, description in _SIGNAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            signals.append((role, description))
    return signals


def _extract_mentioned_names(text: str) -> list[str]:
    if not text:
        return []
    name_pattern = re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", text)
    return list(set(name_pattern))


# ── SPICED analysis ────────────────────────────────────────────

_SPICED_LABELS: dict[str, str] = {
    "S": "Situation",
    "P": "Pain",
    "I": "Impact",
    "C": "Critical Event",
    "E": "Decision Criteria",
    "D": "Decision Process",
}

_SPICED_SIGNALS: dict[str, list[tuple[str, str]]] = {
    "S": [
        (r"\bcurrently\b|\btoday\b|\bright now\b", "current state discussed"),
        (
            r"\busing\b|\bworkflow\b|\bprocess\b|\bplatform\b|\btool\b",
            "tools/process mentioned",
        ),
        (r"\bteam\b|\borg\b|\bstructure\b", "team/org context"),
    ],
    "P": [
        (
            r"\bpain\b|\bchallenge\b|\bproblem\b|\bstruggle\b|\bfrustrat",
            "pain point identified",
        ),
        (
            r"\bdifficult\b|\bhard to\b|\btime.?consuming\b|\bmanual\b",
            "friction identified",
        ),
        (r"\bcan'?t\b|\bunable\b|\bno way to\b|\black", "capability gap"),
        (r"\binefficien|\bbroken\b|\bnot working\b", "broken process"),
    ],
    "I": [
        (r"\brevenue\b|\bgrowth\b|\broi\b", "revenue impact"),
        (r"\bretention\b|\bchurn\b|\brenewal rate\b", "retention impact"),
        (r"\bcost\b|\bsaving\b|\befficienc|\bproductiv", "efficiency impact"),
        (r"\brisk\b|\bcompliance\b|\bliability\b", "risk impact"),
        (r"\$[\d,]+|\bmillion\b|\b\d+[kmb]\b", "quantified impact"),
    ],
    "C": [
        (r"\bdeadline\b|\btimeline\b|\burgent\b", "timeline pressure"),
        (r"\bq[1-4]\b|\bquarter\b|\bfiscal\b|\byear.?end\b", "fiscal deadline"),
        (r"\brenewal\b|\bcontract\b|\bexpir", "contract event"),
        (r"\blaunch\b|\brollout\b|\bgo.?live\b", "launch event"),
        (r"\bboard\b|\bexec\w*\s+review\b", "executive review"),
    ],
    "E": [
        (r"\bevaluat|\bcriteri|\brequirement\b", "evaluation criteria discussed"),
        (r"\brfp\b|\bpoc\b|\bpilot\b|\btrial\b", "formal evaluation"),
        (r"\bcompet|\balternative\b|\bcompar", "competitive evaluation"),
    ],
    "D": [
        (r"\bapprov|\bsign.?off\b", "approval process"),
        (r"\bcommittee\b|\breview board\b", "review committee"),
        (r"\bstakeholder\b|\bconsensus\b|\balign", "stakeholder alignment"),
        (
            r"\bprocurement\b|\blegal review\b|\bsecurity review\b",
            "formal review process",
        ),
    ],
}

_SPICED_RECS: dict[str, list[str]] = {
    "S": [
        "Ask about current tools, processes, and team structure",
        "Research the company's recent initiatives and tech stack",
    ],
    "P": [
        "Dig into specific pain points — what's not working today?",
        "Quantify the pain: how much time/money is wasted?",
    ],
    "I": [
        "Build a business case — tie pain to revenue, retention, or efficiency",
        "Ask: 'What would solving this be worth to the organization?'",
    ],
    "C": [
        "Identify the forcing function — deadline, renewal, board review, or launch",
        "Ask: 'What happens if this doesn't get solved this quarter?'",
    ],
    "E": [
        "Document evaluation criteria and competitive alternatives",
        "Ask: 'What criteria will drive your final decision?'",
    ],
    "D": [
        "Map all approvers and blockers in the decision process",
        "Ask: 'Walk me through how decisions like this get made here'",
    ],
}


def _analyze_spiced(
    deal_props: dict,
    company_name: str | None,
    meeting_texts: list[str],
    email_texts: list[str],
    role_assignments: list[RoleAssignment],
    deal_age_days: int | None,
    other_open_deals: list[dict],
) -> SPICEDAnalysis:
    all_text = " ".join(meeting_texts + email_texts)
    filled_roles = {ra.recommended_role for ra in role_assignments}

    elements: list[SPICEDElement] = []

    for key in ("S", "P", "I", "C", "E", "D"):
        evidence: list[str] = []

        for pattern, desc in _SPICED_SIGNALS.get(key, []):
            if re.search(pattern, all_text, re.IGNORECASE):
                evidence.append(desc)

        if key == "S":
            if company_name:
                evidence.append(f"Company identified: {company_name}")
            if deal_props.get("description"):
                evidence.append("Deal description populated")
            if other_open_deals:
                evidence.append(
                    f"{len(other_open_deals)} other open deal(s) — broader relationship"
                )

        elif key == "I":
            amount = deal_props.get("amount")
            if amount and amount != "0":
                evidence.append(f"Deal amount: ${amount}")

        elif key == "C":
            close_date = deal_props.get("closedate")
            if close_date:
                try:
                    cd = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
                    days_until = (cd - datetime.now(timezone.utc)).days
                    if 0 < days_until <= 90:
                        evidence.append(f"Close date in {days_until} days")
                    elif days_until <= 0:
                        evidence.append("Close date has passed — deal may be stale")
                except (ValueError, TypeError):
                    pass
            if deal_age_days and deal_age_days > 180 and not close_date:
                evidence.append(f"Deal is {deal_age_days} days old with no close date")

        elif key == "E":
            if "Technical Validator" in filled_roles:
                evidence.append(
                    "Technical Validator identified — evaluation likely active"
                )

        elif key == "D":
            if "Economic Buyer" in filled_roles:
                evidence.append("Economic Buyer identified")
            if "Blocker" in filled_roles:
                evidence.append("Blocker identified — decision process mapped")
            role_count = len(filled_roles - {"PERSONNEL_CHANGE"})
            if role_count >= 3:
                evidence.append(f"{role_count} buyer roles identified — multi-threaded")

        if len(evidence) >= 2:
            status = "strong"
        elif len(evidence) == 1:
            status = "partial"
        else:
            status = "missing"

        recs: list[str] = []
        if status in ("missing", "partial"):
            recs = _SPICED_RECS.get(key, [])

        elements.append(
            SPICEDElement(
                element=key,
                label=_SPICED_LABELS[key],
                status=status,
                evidence=evidence,
                recommendations=recs,
            )
        )

    strong = sum(1 for e in elements if e.status == "strong")
    partial = sum(1 for e in elements if e.status == "partial")
    missing = sum(1 for e in elements if e.status == "missing")

    if strong >= 4:
        overall = "strong"
    elif (strong + partial) >= 4:
        overall = "partial"
    else:
        overall = "weak"

    missing_labels = [e.label for e in elements if e.status == "missing"]
    partial_labels = [e.label for e in elements if e.status == "partial"]

    summary_parts = [f"{strong}/6 strong, {partial}/6 partial, {missing}/6 missing"]
    if missing_labels:
        summary_parts.append(f"Gaps: {', '.join(missing_labels)}")
    if partial_labels:
        summary_parts.append(f"Needs work: {', '.join(partial_labels)}")

    top_recs: list[str] = []
    for e in elements:
        if e.status == "missing" and e.recommendations:
            top_recs.append(f"[{e.label}] {e.recommendations[0]}")

    return SPICEDAnalysis(
        elements=elements,
        overall_score=overall,
        strong_count=strong,
        partial_count=partial,
        missing_count=missing,
        summary=" | ".join(summary_parts),
        recommendations=top_recs,
    )


# ── Contact profile builder ─────────────────────────────────────


def _build_contact_profile(record: dict, on_deal: bool = True) -> DealContactProfile:
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
    )


# ── Main analysis orchestration ──────────────────────────────────


async def _deal_analysis(
    deal_id: str,
    hubspot: HubSpotClient,
) -> DealAnalysisResult:
    log_ctx = ToolLogContext("deal_analysis")

    # ── Step 1: Resolve the deal ──
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

    # ── Step 2: Resolve pipeline stage context ──
    stage_label, is_closed = await _resolve_stage(
        pipeline_id, deal_stage_id, hubspot, log_ctx
    )

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

    # ── Step 3: Fetch associated contacts ──
    contact_assoc = await hubspot.get_associations("deals", resolved_id, "contacts")
    log_ctx.add_api_call("hubspot")
    deal_contact_ids = [a.get("toObjectId", a.get("id", "")) for a in contact_assoc]
    deal_contact_ids = [str(cid) for cid in deal_contact_ids if cid]

    deal_contacts: list[DealContactProfile] = []
    if deal_contact_ids:
        contact_records = await hubspot.batch_read(
            "contacts", deal_contact_ids, DEAL_CONTACT_PROPERTIES
        )
        log_ctx.add_api_call("hubspot")
        deal_contacts = [_build_contact_profile(r) for r in contact_records]

    # ── Step 4: Fetch meeting associations and attendees ──
    meeting_assoc = await hubspot.get_associations("deals", resolved_id, "meetings")
    log_ctx.add_api_call("hubspot")
    meeting_ids = [str(a.get("toObjectId", a.get("id", ""))) for a in meeting_assoc]
    meeting_ids = [mid for mid in meeting_ids if mid]

    meetings_data: list[dict] = []
    meeting_contact_ids: set[str] = set()
    meeting_signals: list[tuple[str, str]] = []
    meeting_texts: list[str] = []

    if meeting_ids:
        meeting_records = await hubspot.batch_read(
            "meetings", meeting_ids, MEETING_PROPERTIES
        )
        log_ctx.add_api_call("hubspot")

        meeting_assoc_tasks = [
            hubspot.get_associations("meetings", mid, "contacts") for mid in meeting_ids
        ]
        meeting_contact_results = await asyncio.gather(
            *meeting_assoc_tasks, return_exceptions=True
        )
        log_ctx.add_api_call("hubspot")

        for i, mr in enumerate(meeting_records):
            m_props = mr.get("properties", {})
            m_contacts_raw = (
                meeting_contact_results[i]
                if i < len(meeting_contact_results)
                and not isinstance(meeting_contact_results[i], Exception)
                else []
            )
            m_contact_ids = [
                str(a.get("toObjectId", a.get("id", ""))) for a in m_contacts_raw
            ]
            m_contact_ids = [c for c in m_contact_ids if c]
            meeting_contact_ids.update(m_contact_ids)

            body = m_props.get("hs_meeting_body") or ""
            meeting_texts.append(body)
            signals = _parse_signals(body)
            meeting_signals.extend(signals)

            meetings_data.append(
                {
                    "id": mr.get("id"),
                    "title": m_props.get("hs_meeting_title"),
                    "start_time": m_props.get("hs_meeting_start_time"),
                    "outcome": m_props.get("hs_meeting_outcome"),
                    "contact_ids": m_contact_ids,
                    "signals": signals,
                }
            )

    # ── Step 5: Fetch email associations and threading ──
    email_assoc = await hubspot.get_associations("deals", resolved_id, "emails")
    log_ctx.add_api_call("hubspot")
    email_ids = [str(a.get("toObjectId", a.get("id", ""))) for a in email_assoc]
    email_ids = [eid for eid in email_ids if eid]

    email_contact_ids: set[str] = set()
    email_signals: list[tuple[str, str]] = []
    email_texts: list[str] = []
    email_from_addresses: set[str] = set()
    email_to_addresses: set[str] = set()
    email_count = len(email_ids)

    if email_ids:
        recent_email_ids = email_ids[:20]
        email_records = await hubspot.batch_read(
            "emails", recent_email_ids, EMAIL_PROPERTIES
        )
        log_ctx.add_api_call("hubspot")

        email_assoc_tasks = [
            hubspot.get_associations("emails", eid, "contacts")
            for eid in recent_email_ids
        ]
        email_contact_results = await asyncio.gather(
            *email_assoc_tasks, return_exceptions=True
        )
        log_ctx.add_api_call("hubspot")

        for i, er in enumerate(email_records):
            e_props = er.get("properties", {})
            e_contacts_raw = (
                email_contact_results[i]
                if i < len(email_contact_results)
                and not isinstance(email_contact_results[i], Exception)
                else []
            )
            e_contact_ids = [
                str(a.get("toObjectId", a.get("id", ""))) for a in e_contacts_raw
            ]
            e_contact_ids = [c for c in e_contact_ids if c]
            email_contact_ids.update(e_contact_ids)

            if e_props.get("hs_email_from_email"):
                email_from_addresses.add(e_props["hs_email_from_email"].lower())
            if e_props.get("hs_email_to_email"):
                for addr in e_props["hs_email_to_email"].split(";"):
                    email_to_addresses.add(addr.strip().lower())

            body = e_props.get("hs_email_text") or ""
            email_texts.append(body)
            signals = _parse_signals(body)
            email_signals.extend(signals)

    # ── Identify gap candidates (in meetings/emails but not on deal) ──
    deal_contact_id_set = set(deal_contact_ids)
    gap_candidate_ids = (meeting_contact_ids | email_contact_ids) - deal_contact_id_set
    gap_candidate_ids = {gid for gid in gap_candidate_ids if gid}

    gap_contacts: list[DealContactProfile] = []
    if gap_candidate_ids:
        try:
            gap_records = await hubspot.batch_read(
                "contacts", list(gap_candidate_ids), DEAL_CONTACT_PROPERTIES
            )
            log_ctx.add_api_call("hubspot")
            gap_contacts = [
                _build_contact_profile(r, on_deal=False) for r in gap_records
            ]
        except Exception:
            logger.warning("failed to fetch gap candidate contacts")

    # ── Step 6: Fetch company record ──
    company_assoc = await hubspot.get_associations("deals", resolved_id, "companies")
    log_ctx.add_api_call("hubspot")

    company_name: str | None = None
    company_id: str | None = None
    other_open_deals: list[dict] = []

    if company_assoc:
        company_id = str(
            company_assoc[0].get("toObjectId", company_assoc[0].get("id", ""))
        )
        if company_id:
            try:
                company_record = await hubspot.get_company(company_id)
                log_ctx.add_api_call("hubspot")
                company_name = company_record.get("properties", {}).get("name")

                other_deals = await hubspot.search_deals_by_company(company_id)
                log_ctx.add_api_call("hubspot")
                other_open_deals = [
                    {
                        "id": d.get("id"),
                        "name": d.get("properties", {}).get("dealname"),
                        "stage": d.get("properties", {}).get("dealstage"),
                        "amount": d.get("properties", {}).get("amount"),
                    }
                    for d in other_deals
                    if d.get("id") != resolved_id
                ]
            except Exception:
                logger.warning("failed to fetch company record for %s", company_id)

    # ── Step 7: Analyze and score ──
    all_signals = meeting_signals + email_signals
    signal_map: dict[str, list[str]] = {}
    for role, desc in all_signals:
        signal_map.setdefault(role, []).append(desc)

    all_contacts = deal_contacts + gap_contacts
    role_assignments: list[RoleAssignment] = []
    contacts_to_add: list[ContactToAdd] = []
    recommended_edits: list[RecommendedEdit] = []
    open_questions: list[str] = []

    # Build meeting attendance map for evidence
    contact_meeting_count: dict[str, int] = {}
    for m in meetings_data:
        for cid in m.get("contact_ids", []):
            contact_meeting_count[cid] = contact_meeting_count.get(cid, 0) + 1

    # Build email participation map
    contact_email_map: dict[str, bool] = {}
    for cid in email_contact_ids:
        contact_email_map[cid] = True

    for contact in all_contacts:
        inferred_role, confidence = _infer_role(
            contact.title, contact.persona, contact.seniority
        )
        evidence: list[str] = []

        if contact.persona:
            evidence.append(f"Persona: {contact.persona}")
        if contact.seniority:
            evidence.append(f"Seniority: {contact.seniority}")
        if contact.title:
            evidence.append(f"Title: {contact.title}")

        mtg_count = contact_meeting_count.get(contact.contact_id, 0)
        if mtg_count > 0:
            evidence.append(f"Attended {mtg_count} meeting(s)")
            if confidence == "LOW":
                confidence = "MEDIUM"

        if contact_email_map.get(contact.contact_id):
            evidence.append("Active in email threads")

        if contact.engagement_level == "high":
            evidence.append(f"High engagement ({contact.notes_count} notes)")
        elif contact.engagement_level == "none" and contact.on_deal:
            evidence.append("Zero engagement — may be stale")

        # Check personnel change signals
        personnel_signals = signal_map.get("PERSONNEL_CHANGE", [])
        if personnel_signals and contact.engagement_level == "none":
            open_questions.append(
                f"Is {contact.name} still at the company? Zero engagement + personnel change signals detected."
            )

        recommended_role = inferred_role or contact.current_buying_role
        if not recommended_role:
            if mtg_count >= 3:
                recommended_role = "Champion"
                evidence.append(
                    "Frequent meeting attendance suggests champion behavior"
                )
            elif contact.engagement_level == "high":
                recommended_role = "Influencer"
                evidence.append("High engagement suggests influencer role")

        if recommended_role:
            role_assignments.append(
                RoleAssignment(
                    contact_id=contact.contact_id,
                    name=contact.name,
                    title=contact.title,
                    current_role=contact.current_buying_role,
                    recommended_role=recommended_role,
                    confidence=confidence,
                    evidence=evidence,
                    on_deal=contact.on_deal,
                )
            )

        if not contact.on_deal:
            priority = _calculate_priority(
                contact, recommended_role, stage_reqs, mtg_count
            )
            contacts_to_add.append(
                ContactToAdd(
                    contact_id=contact.contact_id,
                    name=contact.name,
                    title=contact.title,
                    email=contact.email,
                    recommended_role=recommended_role,
                    evidence=evidence,
                    priority=priority,
                )
            )
            recommended_edits.append(
                RecommendedEdit(
                    edit_type="associate_contact",
                    target_id=contact.contact_id,
                    target_name=contact.name,
                    field="deal_association",
                    new_value=resolved_id,
                    reason=f"Appeared in {mtg_count} meeting(s)"
                    + (
                        " and email threads"
                        if contact_email_map.get(contact.contact_id)
                        else ""
                    ),
                )
            )

        if (
            recommended_role
            and recommended_role != contact.current_buying_role
            and recommended_role != "PERSONNEL_CHANGE"
        ):
            recommended_edits.append(
                RecommendedEdit(
                    edit_type="set_buyer_role",
                    target_id=contact.contact_id,
                    target_name=contact.name,
                    field="hs_buying_role",
                    current_value=contact.current_buying_role,
                    new_value=recommended_role,
                    reason="; ".join(evidence[:2])
                    if evidence
                    else "Title-based inference",
                )
            )

    # ── Step 7b: Stage gap analysis ──
    filled_roles = list({ra.recommended_role for ra in role_assignments if ra.on_deal})
    required = stage_reqs.get("required_roles", [])
    missing = [r for r in required if r not in filled_roles]
    recommended_extra = [
        r for r in stage_reqs.get("recommended_roles", []) if r not in filled_roles
    ]

    cold = [c.name for c in deal_contacts if c.engagement_level == "none"]

    total_on_deal = len(deal_contacts)
    min_contacts = stage_reqs.get("min_contacts", 1)

    stage_gap = StageGapAnalysis(
        current_stage=stage_key or "unknown",
        stage_label=stage_reqs.get("label", stage_label),
        required_roles=required,
        filled_roles=filled_roles,
        missing_roles=missing + recommended_extra,
        contact_count=total_on_deal,
        minimum_contacts=min_contacts,
        cold_contacts=cold,
        single_threaded=total_on_deal <= 1,
        multithreading_score=(
            "critical"
            if total_on_deal < min_contacts
            else "adequate"
            if total_on_deal >= min_contacts
            else "weak"
        ),
    )

    if missing:
        for role in missing:
            open_questions.append(
                f"Missing required role: {role}. Who at {company_name or 'this company'} fills this role?"
            )

    if cold:
        for name in cold:
            open_questions.append(
                f"{name} has zero engagement. Still relevant to this deal?"
            )

    # ── Step 8: Calculate deal age and activity summary ──
    deal_age = None
    create_date = props.get("createdate")
    if create_date:
        try:
            created = datetime.fromisoformat(create_date.replace("Z", "+00:00"))
            deal_age = (datetime.now(timezone.utc) - created).days
        except (ValueError, TypeError):
            pass

    activity_summary = {
        "emails": email_count,
        "meetings": len(meetings_data),
        "contacts_on_deal": total_on_deal,
        "contacts_in_meetings_not_on_deal": len(
            meeting_contact_ids - deal_contact_id_set
        ),
        "contacts_in_emails_not_on_deal": len(email_contact_ids - deal_contact_id_set),
    }

    # ── Step 9: SPICED analysis ──
    spiced = _analyze_spiced(
        deal_props=props,
        company_name=company_name,
        meeting_texts=meeting_texts,
        email_texts=email_texts,
        role_assignments=role_assignments,
        deal_age_days=deal_age,
        other_open_deals=other_open_deals,
    )

    # Sort contacts_to_add by priority
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    contacts_to_add.sort(key=lambda c: priority_order.get(c.priority, 4))

    warnings: list[str] = []
    if stage_gap.single_threaded:
        warnings.append(
            "SINGLE-THREADED: Only 1 contact on this deal. High risk of deal loss."
        )
    if stage_gap.multithreading_score == "critical":
        warnings.append(
            f"Contact count ({total_on_deal}) is below stage minimum ({min_contacts})."
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
        activity_summary=activity_summary,
        contact_count=total_on_deal,
        stage_minimum=min_contacts,
        deal_contacts=deal_contacts,
        contacts_to_add=contacts_to_add,
        role_assignments=role_assignments,
        stage_gap_analysis=stage_gap,
        spiced_analysis=spiced,
        recommended_edits=recommended_edits,
        open_questions=open_questions,
        warnings=warnings,
        company_name=company_name,
        company_id=company_id,
        other_open_deals=other_open_deals,
    )


# ── Helpers ──────────────────────────────────────────────────────


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


def _calculate_priority(
    contact: DealContactProfile,
    recommended_role: str | None,
    stage_reqs: dict,
    meeting_count: int,
) -> str:
    required_roles = stage_reqs.get("required_roles", [])

    if recommended_role in required_roles:
        return "CRITICAL"

    if meeting_count >= 3:
        return "HIGH"

    title = (contact.title or "").lower()
    if any(
        x in title
        for x in ["vp", "vice president", "chief", "ceo", "cfo", "cmo", "cto"]
    ):
        return "HIGH"

    if meeting_count >= 1:
        return "MEDIUM"

    return "LOW"
