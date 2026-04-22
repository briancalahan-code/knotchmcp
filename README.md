# KnotchMCP

A remote MCP server that exposes contact discovery and enrichment tools for Knotch's sales team. It proxies Apollo, Clay, and HubSpot APIs so users never handle those credentials directly — just connect via Claude Desktop or Cowork.

## Prerequisites

- Python 3.11+
- API keys for Apollo, Clay, and HubSpot
- Docker (for deployment)

## Local Development

```bash
git clone <repo-url> && cd KnotchMCP
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your actual API keys
```

Run tests:

```bash
python -m pytest tests/ -v
```

## Tools

### find_contact_by_details

Find a specific contact by name and company. Searches Apollo for contact details, then checks HubSpot for existing records. Returns the contact with gaps (missing fields) and suggested next actions.

**Input:** `first_name`, `last_name`, `company` (name or domain), optional `email`, `linkedin_url`
**Output:** Contact with `hubspot_status`, `gaps`, `suggested_actions`

### find_contacts_by_role

Find contacts by job title at a company. Returns top candidates ranked by relevance, each checked against HubSpot.

**Input:** `titles` (list), `company`, optional `seniority` (e.g. "vp", "director"), `limit` (default 3)
**Output:** List of candidates with `total_available` count

### find_phone

Find a contact's phone number via Apollo. Provide at least one identifier.

**Input:** One of `apollo_id`, `email`, or `linkedin_url` + `name`
**Output:** `phone`, `phone_type`, `found`, `suggested_action` (if not found, suggests `clay_enrich`)

### enrich_contact

Fill empty fields on an existing HubSpot contact using Apollo and Clay. Automatically writes enriched data back to HubSpot.

**Input:** `hubspot_contact_id`
**Output:** `filled` (field → value map), `already_populated`, `not_found`, `sources_used`

### add_to_hubspot

Add a contact to HubSpot with deduplication. Searches by email and LinkedIn URL first — updates if found, creates if new. Associates with the matching company by domain.

**Input:** `first_name`, `last_name`, optional `email`, `title`, `company`, `company_domain`, `linkedin_url`, `location`, `phone`, `apollo_id`
**Output:** `hubspot_contact_id`, `hubspot_url`, `action` ("created" or "updated"), `company_associated`

### clay_enrich

Enrich a contact using Clay as a follow-up when Apollo didn't return phone, email, or other data. Consumes Clay credits.

**Input:** `first_name`, `last_name`, `company_domain`, optional `requested_data` (default: `["phone", "email"]`)
**Output:** `enriched_fields`, `credits_used`, `task_status`

## Running the Server

**stdio mode** (for local Claude Desktop):

```bash
python -m knotch_mcp
```

**SSE mode** (for remote access):

```bash
python -m knotch_mcp --http
```

The server listens on port 8080 by default (configurable via `PORT` env var).

## Deploying to Railway

1. Push this repo to GitHub
2. Create a new project on [Railway](https://railway.app)
3. Connect your GitHub repo
4. Add environment variables in the Railway dashboard (see below)
5. Deploy — Railway will use the included `Dockerfile` and `railway.toml`

## Client Configuration

### Claude Desktop / Cowork

Add to your MCP settings:

```json
{
  "mcpServers": {
    "knotch-contacts": {
      "url": "https://your-app.railway.app/sse",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_AUTH_TOKEN"
      }
    }
  }
}
```

### Local stdio mode (Claude Desktop)

```json
{
  "mcpServers": {
    "knotch-contacts": {
      "command": "python",
      "args": ["-m", "knotch_mcp"],
      "cwd": "/path/to/KnotchMCP",
      "env": {
        "APOLLO_API_KEY": "your-key",
        "CLAY_API_KEY": "your-key",
        "HUBSPOT_PRIVATE_APP_TOKEN": "your-token"
      }
    }
  }
}
```

## Environment Variables

| Variable                    | Required | Default    | Description                           |
| --------------------------- | -------- | ---------- | ------------------------------------- |
| `MCP_AUTH_TOKEN`            | Yes      | —          | Shared bearer token for client auth   |
| `APOLLO_API_KEY`            | Yes      | —          | Apollo.io API key                     |
| `CLAY_API_KEY`              | Yes      | —          | Clay API key                          |
| `HUBSPOT_PRIVATE_APP_TOKEN` | Yes      | —          | HubSpot private app token             |
| `HUBSPOT_PORTAL_ID`         | No       | `44523005` | HubSpot portal ID (for building URLs) |
| `APOLLO_RATE_LIMIT`         | No       | `45`       | Apollo requests per minute            |
| `PORT`                      | No       | `8080`     | Server port (Railway auto-sets)       |
