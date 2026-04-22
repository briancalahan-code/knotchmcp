# KnotchMCP Design Document

**Date:** 2026-04-22
**Author:** Brian Calahan + Claude
**Status:** Approved

## Summary

A remote MCP server in Python that exposes contact discovery and enrichment tools for Knotch's 10-person sales team. The server proxies Apollo, Clay, and HubSpot APIs so users never handle those credentials directly. Users connect via Claude Desktop or Cowork.

## Prior Art

- Individual Apollo and HubSpot MCP servers exist (e.g., apollo-io-mcp, HubSpot's official MCP server)
- No unified multi-platform proxy exists combining all three
- No Clay MCP server exists anywhere
- FastMCP is the dominant Python framework for MCP servers (powers ~70% of community servers)

**Disposition:** Build new — existing servers are single-source and don't cover our multi-API merge + Clay integration use case.

## Architecture

```
Claude Desktop / Cowork (10 users)
  │ Authorization: Bearer <MCP_AUTH_TOKEN>
  │ SSE transport
  ▼
Railway Container
  ├── server.py      FastMCP bootstrap + auth middleware
  ├── tools.py       6 MCP tool definitions
  ├── clients/
  │   ├── apollo.py  Apollo API client (httpx, 20s timeout, rate limited)
  │   ├── clay.py    Clay API client (httpx, 30s timeout, task polling)
  │   └── hubspot.py HubSpot API client (httpx, 10s timeout)
  ├── models.py      Pydantic input/output models
  ├── rate_limit.py  Token bucket for Apollo
  ├── config.py      Settings from environment
  └── logging.py     Structured JSON logger
```

### Design Decisions

**Apollo-Primary, Clay Opt-In:** Find tools call Apollo only. Response includes a `gaps` field listing missing data. Claude naturally prompts the user to call `clay_enrich` as a follow-up. This conserves Clay credits and keeps find tools fast (~2-3s). The `enrich_contact` tool (HubSpot write-back) is the exception — it auto-falls back to Clay since the intent is to fill all gaps.

**Rejected alternatives:**

- Auto-Clay fallback on every find (adds 5-15s latency, burns credits)
- Parallel blast of both APIs (expensive, complex merge logic)

## Tools

### 1. find_contact_by_details

**Input:** first_name, last_name, company (name or domain), email?, linkedin_url?

**Flow:**

1. If company is a name (no dot), resolve to domain via Apollo org search
2. Apollo People Match (name + company + optional email/linkedin)
3. HubSpot search by email, then by linkedin_url

**Output:** name, title, company, email, email_status, linkedin_url, location, apollo_id, sources, hubspot_status ("found"|"not_found"), hubspot_contact_id?, hubspot_url?, gaps, suggested_actions

### 2. find_contacts_by_role

**Input:** titles (string[]), company (name or domain), seniority?, limit? (default 3)

**Flow:**

1. Resolve company to domain if needed
2. Apollo People Search (titles + domain + seniority)
3. Apollo People Match for each of top N results (parallel)
4. HubSpot search for each by email + linkedin (parallel)

**Output:** candidates (same shape as tool 1), total_available

### 3. find_phone

**Input:** ONE OF: apollo_id, email, (linkedin_url + name)

**Flow:**

1. Apollo People Match with reveal_phone_number=true
2. If phone in response, return it
3. If no phone, return gap and suggest clay_enrich

**Output:** phone?, phone_type?, source, confidence?, found, suggested_action?

### 4. enrich_contact

**Input:** hubspot_contact_id

**Flow:**

1. GET HubSpot contact (jobtitle, phone, hs_linkedin_url, city, state, country, company)
2. Identify empty/stale fields
3. Apollo People Match using available identifiers
4. If Apollo misses any, auto-fallback to Clay
5. PATCH HubSpot with found data

**Output:** filled (field: new_value), already_populated, not_found, sources_used

### 5. add_to_hubspot

**Input:** contact object (output shape from tools 1 or 2)

**Flow:**

1. Search HubSpot by email
2. Search HubSpot by linkedin_url
3. If match, PATCH update instead of create
4. If no match, POST create
5. Search companies by domain, associate if found

**Output:** hubspot_contact_id, hubspot_url, action ("created"|"updated"), company_associated, company_name?

### 6. clay_enrich

**Input:** first_name, last_name, company_domain, requested_data? (["phone", "email", "work_history"])

**Flow:**

1. Clay find-and-enrich-list-of-contacts with contact + requested data points
2. Poll get-task every 2s -> 4s -> 8s, up to 45s timeout
3. Return enriched fields

**Output:** enriched_fields (field: value), source ("clay"), credits_used, task_status

## API Client Details

### Apollo (apollo.py)

- Base URL: `https://api.apollo.io/api/v1`
- Auth: `api_key` param in request body
- Endpoints:
  - People Match: `POST /people/match`
  - People Search: `POST /mixed_people/api_search`
  - Org Search: `POST /mixed_companies/search`
- Rate limit: token bucket, default 45 req/min (configurable via APOLLO_RATE_LIMIT)
- Timeout: 20s
- Phone: `reveal_phone_number=true` returns cached phone data inline (no webhook needed)

### Clay (clay.py)

- Base URL: `https://api.clay.com/v1`
- Auth: `Authorization: Bearer <CLAY_API_KEY>`
- Endpoints:
  - Find contacts: `POST /find-and-enrich-list-of-contacts`
  - Find at company: `POST /find-and-enrich-contacts-at-company`
  - Get task: `GET /tasks/{taskId}`
- Async model: all enrichment calls return a taskId, poll get-task for results
- Polling: exponential backoff 2s -> 4s -> 8s -> 8s..., 45s total timeout
- Company identifier: requires domain, not company name
- Phone enrichment: use Custom data point type

### HubSpot (hubspot.py)

- Base URL: `https://api.hubapi.com`
- Auth: `Authorization: Bearer <HUBSPOT_PRIVATE_APP_TOKEN>`
- Portal: 44523005
- LinkedIn property: `hs_linkedin_url`
- Endpoints:
  - Search contacts: `POST /crm/v3/objects/contacts/search`
  - Get contact: `GET /crm/v3/objects/contacts/{id}`
  - Create contact: `POST /crm/v3/objects/contacts`
  - Update contact: `PATCH /crm/v3/objects/contacts/{id}`
  - Search companies: `POST /crm/v3/objects/companies/search`
  - Associate: `PUT /crm/v3/objects/contacts/{id}/associations/companies/{companyId}/default`
- Timeout: 10s

## Auth & Security

Simple ASGI middleware that checks `Authorization: Bearer <token>` against `MCP_AUTH_TOKEN` env var. Not using MCP SDK's OAuth framework (overkill for shared bearer token).

Structured request logging: timestamp, tool_name, duration_ms, apis_called. Never logs PII or secrets.

v2 consideration: per-user tokens for audit trail.

## Rate Limiting

In-process token bucket for Apollo. Single Railway container, no distributed state needed.

- Default: 45 requests/minute, burst capacity 45
- Configurable via `APOLLO_RATE_LIMIT` env var
- Container restart resets to full capacity (safe — refills naturally)

## Deployment

- Platform: Railway (Docker container)
- Dockerfile: Python 3.11-slim, pip install from pyproject.toml
- Port: `$PORT` (Railway auto-sets this)
- Secrets: configured in Railway dashboard
- Modes: `python -m knotch_mcp` (stdio), `python -m knotch_mcp --http` (SSE on $PORT or 8080)

## Risks & Validated Dimensions

| Dimension                      | Status      | Note                                                         |
| ------------------------------ | ----------- | ------------------------------------------------------------ |
| Apollo search -> match pattern | PASS        | Correct approach per API surface                             |
| Clay async polling             | CONDITIONAL | 45s timeout + exponential backoff mitigates                  |
| Rate limiting                  | CONDITIONAL | In-process bucket fine for 1 container, 10 users             |
| Auth model                     | CONDITIONAL | v1 acceptable, structured logging provides audit trail       |
| SSE on Railway                 | CONDITIONAL | Add keep-alive heartbeats if MCP SDK doesn't handle natively |
| HubSpot race conditions        | PASS        | Negligible risk at 10 users                                  |
| Phone enrichment coverage      | CONDITIONAL | Cached-only from Apollo, Clay as explicit fallback           |
| Company name resolution        | CONDITIONAL | Auto-resolve via Apollo org search, accept ambiguity for UX  |

## Out of Scope (v1)

- No caching layer
- No webhook receivers
- No UI
- No bulk operations (except find_contacts_by_role returning top N)
- No per-user auth (shared bearer token)
- No distributed rate limiting (single container)

## Environment Variables

```
MCP_AUTH_TOKEN=           # Shared bearer token for client auth
APOLLO_API_KEY=           # Apollo.io API key
CLAY_API_KEY=             # Clay API key
HUBSPOT_PRIVATE_APP_TOKEN= # HubSpot private app token
HUBSPOT_PORTAL_ID=44523005 # HubSpot portal ID (for building URLs)
APOLLO_RATE_LIMIT=45      # Apollo requests per minute (optional, default 45)
PORT=8080                 # Server port (Railway auto-sets, default 8080)
```
