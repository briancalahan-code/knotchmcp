# KnotchMCP Implementation Plan

> **For Claude:** REQUIRED: Use using-git-worktrees for isolation, then parallel subagent execution (see Execution Model below).

**Goal:** Build a remote MCP server that exposes contact discovery and enrichment tools proxying Apollo, Clay, and HubSpot APIs for Knotch's sales team.

**Architecture:** FastMCP server with SSE transport, deployed on Railway via Docker. Thin async httpx clients for each API, Pydantic models for all I/O, in-process token bucket for Apollo rate limiting, bearer token auth middleware. Apollo-primary with Clay as an explicit opt-in follow-up tool.

**Tech Stack:** Python 3.11+, mcp SDK (FastMCP), httpx, Pydantic 2, pydantic-settings, respx (test mocking), pytest-asyncio

**Test command:** `cd /Users/briancalahan/KnotchMCP && python -m pytest tests/ -v`
**Pre-execution baseline:** 0 tests (greenfield)
**Execution mode:** parallel (per wave table)
**MCP servers:** none

**Design doc:** `docs/plans/2026-04-22-knotch-mcp-design.md`

---

## Discoveries

_Updated by orchestrator at session end. Captures what agents found that the plan didn't anticipate._

---

## Review Notes (APPROVE-WITH-NOTES, 2026-04-22)

**Fixed:**

1. `pyproject.toml` build-backend corrected from invalid `setuptools.backends._legacy:_Backend` to `setuptools.build_meta`
2. `conftest.py` env var setup moved before test execution (Step 4b) instead of after-the-fact note
3. `railway.toml` healthcheck path changed from `/` to `/sse` (FastMCP exposes `/sse`, no root route)

**Noted (address during execution):**

- Auth: `_check_auth` is a utility function, not wired as ASGI middleware. For v1 this is acceptable if FastMCP's SSE transport supports auth hooks — verify during Task 8 and wire into the transport layer if possible. If not, document that auth enforcement requires a reverse proxy.
- SSE keep-alive: Design doc flags this as a conditional risk. Verify post-deploy that Railway doesn't drop idle SSE connections. FastMCP may send heartbeats natively — check during Task 8.

---

## Success Criteria

- [ ] All 6 MCP tools return correct response shapes with mocked API backends
- [ ] Auth middleware rejects requests without valid bearer token
- [ ] Server starts in both stdio and SSE modes without error
- [ ] Apollo rate limiter enforces configured requests/minute
- [ ] Clay polling handles timeout and exponential backoff correctly
- [ ] `docker build` succeeds and container starts
- [ ] Test suite passes with all happy paths covered (1 test minimum per tool + per client method)
- [ ] README contains local dev setup, credential config, tool docs, and Claude Desktop config snippet

---

## Execution Model

**Dependency Graph:**

```
Task 1 (scaffold) ──────────────────────────────────────┐
  ├── Task 2 (models)                                    │
  └── Task 3 (rate limiter + logger)                     │
        ├── Task 4 (Apollo client, needs models+ratelimit)│
        ├── Task 5 (Clay client, needs models)           │
        └── Task 6 (HubSpot client, needs models)        │
              └── Task 7 (tools, needs all clients)      │
                    └── Task 8 (server + auth)            │
                          └── Task 9 (Docker + README) ──┘
```

**Parallel Waves:**

| Wave   | Tasks          | Dependencies                 |
| ------ | -------------- | ---------------------------- |
| Wave 1 | Task 1         | None                         |
| Wave 2 | Task 2, Task 3 | Wave 1 complete              |
| Wave 3 | Task 4, 5, 6   | Wave 2 complete              |
| Wave 4 | Task 7         | Wave 3 complete              |
| Wave 5 | Task 8, then 9 | Wave 4 complete (sequential) |

**Test Gate (between waves):**

1. Run `python -m pytest tests/ -v`
2. Compare against baseline (starts at 0, grows each wave)
3. **Pass:** proceed to next wave
4. **Fail:** fix before proceeding

---

## Task 1: Project Scaffold + Config

**Parallelizable with:** None (Wave 1 — must come first)

**Files:**

- Create: `pyproject.toml`
- Create: `src/knotch_mcp/__init__.py`
- Create: `src/knotch_mcp/__main__.py`
- Create: `src/knotch_mcp/config.py`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "knotch-mcp"
version = "0.1.0"
description = "MCP server for contact discovery and enrichment via Apollo, Clay, and HubSpot"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.6.0",
    "httpx>=0.27",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "python-dotenv>=1.0",
    "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "pytest-cov>=5.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Create package files**

`src/knotch_mcp/__init__.py`:

```python
"""KnotchMCP — Contact discovery and enrichment MCP server."""
```

`src/knotch_mcp/__main__.py`:

```python
import sys
from knotch_mcp.server import mcp, settings


def main():
    transport = "sse" if "--http" in sys.argv else "stdio"
    if transport == "sse":
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = settings.port
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
```

**Step 3: Create config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mcp_auth_token: str = ""
    apollo_api_key: str = ""
    clay_api_key: str = ""
    hubspot_private_app_token: str = ""
    hubspot_portal_id: str = "44523005"
    apollo_rate_limit: int = 45
    port: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

**Step 4: Create .env.example**

```
MCP_AUTH_TOKEN=your-shared-bearer-token
APOLLO_API_KEY=your-apollo-api-key
CLAY_API_KEY=your-clay-api-key
HUBSPOT_PRIVATE_APP_TOKEN=your-hubspot-private-app-token
HUBSPOT_PORTAL_ID=44523005
APOLLO_RATE_LIMIT=45
PORT=8080
```

**Step 5: Create .gitignore**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.env
.venv/
venv/
.pytest_cache/
.coverage
htmlcov/
*.egg
.eggs/
```

**Step 6: Create test infrastructure**

`tests/__init__.py`: empty file

`tests/conftest.py`:

```python
import pytest
from knotch_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(
        mcp_auth_token="test-token",
        apollo_api_key="test-apollo-key",
        clay_api_key="test-clay-key",
        hubspot_private_app_token="test-hubspot-token",
        hubspot_portal_id="12345",
        apollo_rate_limit=45,
        port=8080,
    )
```

**Step 7: Install and verify**

```bash
cd /Users/briancalahan/KnotchMCP
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "from knotch_mcp.config import Settings; print('OK')"
```

**Step 8: Commit**

```bash
git add pyproject.toml src/ tests/ .env.example .gitignore
git commit -m "feat: project scaffold with config and test infrastructure"
```

---

## Task 2: Pydantic Models

**Parallelizable with:** Task 3

**Files:**

- Create: `src/knotch_mcp/models.py`
- Create: `tests/test_models.py`

**Step 1: Write failing tests**

`tests/test_models.py`:

```python
from knotch_mcp.models import (
    ContactResult,
    FindContactInput,
    FindContactsByRoleInput,
    FindPhoneInput,
    EnrichContactInput,
    AddToHubSpotInput,
    ClayEnrichInput,
    FindContactsResult,
    FindPhoneResult,
    EnrichContactResult,
    AddToHubSpotResult,
    ClayEnrichResult,
)


def test_contact_result_defaults():
    c = ContactResult(name="Jane Smith")
    assert c.name == "Jane Smith"
    assert c.hubspot_status == "not_checked"
    assert c.gaps == []
    assert c.suggested_actions == []
    assert c.sources == []


def test_contact_result_with_gaps():
    c = ContactResult(
        name="Jane Smith",
        title="VP Engineering",
        company="Stripe",
        email="jane@stripe.com",
        email_status="verified",
        sources=["apollo"],
        hubspot_status="not_found",
        gaps=["phone", "linkedin_url"],
        suggested_actions=["clay_enrich for phone", "add_to_hubspot"],
    )
    assert c.hubspot_status == "not_found"
    assert "phone" in c.gaps
    assert len(c.suggested_actions) == 2


def test_find_contact_input_required_fields():
    inp = FindContactInput(
        first_name="Jane", last_name="Smith", company="stripe.com"
    )
    assert inp.first_name == "Jane"
    assert inp.email is None
    assert inp.linkedin_url is None


def test_find_contacts_by_role_defaults():
    inp = FindContactsByRoleInput(
        titles=["VP Engineering", "CTO"], company="Stripe"
    )
    assert inp.limit == 3
    assert inp.seniority is None


def test_find_phone_input_accepts_any_identifier():
    by_email = FindPhoneInput(email="jane@stripe.com")
    assert by_email.apollo_id is None

    by_id = FindPhoneInput(apollo_id="abc123")
    assert by_id.email is None


def test_add_to_hubspot_input():
    inp = AddToHubSpotInput(
        first_name="Jane",
        last_name="Smith",
        email="jane@stripe.com",
        company_domain="stripe.com",
    )
    assert inp.title is None
    assert inp.phone is None


def test_clay_enrich_input_defaults():
    inp = ClayEnrichInput(
        first_name="Jane", last_name="Smith", company_domain="stripe.com"
    )
    assert inp.requested_data == ["phone", "email"]


def test_find_contacts_result():
    r = FindContactsResult(
        candidates=[ContactResult(name="Jane Smith")],
        total_available=42,
    )
    assert len(r.candidates) == 1
    assert r.total_available == 42


def test_find_phone_result_found():
    r = FindPhoneResult(
        phone="+14155551234",
        phone_type="mobile",
        source="apollo",
        confidence="high",
        found=True,
    )
    assert r.found is True
    assert r.suggested_action is None


def test_find_phone_result_not_found():
    r = FindPhoneResult(found=False, source="apollo", suggested_action="clay_enrich")
    assert r.phone is None


def test_enrich_contact_result():
    r = EnrichContactResult(
        filled={"jobtitle": "VP Engineering", "phone": "+14155551234"},
        already_populated=["email", "hs_linkedin_url"],
        not_found=["city"],
        sources_used=["apollo"],
    )
    assert len(r.filled) == 2
    assert "email" in r.already_populated


def test_add_to_hubspot_result():
    r = AddToHubSpotResult(
        hubspot_contact_id="123",
        hubspot_url="https://app.hubspot.com/contacts/12345/contact/123",
        action="created",
        company_associated=True,
        company_name="Stripe",
    )
    assert r.action == "created"


def test_clay_enrich_result():
    r = ClayEnrichResult(
        enriched_fields={"phone": "+14155551234"},
        source="clay",
        credits_used=2,
        task_status="completed",
    )
    assert r.task_status == "completed"
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'knotch_mcp.models'`

**Step 3: Implement models.py**

`src/knotch_mcp/models.py`:

```python
from __future__ import annotations

from pydantic import BaseModel


class ContactResult(BaseModel):
    name: str
    title: str | None = None
    company: str | None = None
    email: str | None = None
    email_status: str | None = None
    linkedin_url: str | None = None
    location: str | None = None
    apollo_id: str | None = None
    phone: str | None = None
    phone_type: str | None = None
    sources: list[str] = []
    hubspot_status: str = "not_checked"
    hubspot_contact_id: str | None = None
    hubspot_url: str | None = None
    gaps: list[str] = []
    suggested_actions: list[str] = []


class FindContactInput(BaseModel):
    first_name: str
    last_name: str
    company: str
    email: str | None = None
    linkedin_url: str | None = None


class FindContactsByRoleInput(BaseModel):
    titles: list[str]
    company: str
    seniority: str | None = None
    limit: int = 3


class FindPhoneInput(BaseModel):
    apollo_id: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    name: str | None = None


class EnrichContactInput(BaseModel):
    hubspot_contact_id: str


class AddToHubSpotInput(BaseModel):
    first_name: str
    last_name: str
    email: str | None = None
    title: str | None = None
    company: str | None = None
    company_domain: str | None = None
    linkedin_url: str | None = None
    location: str | None = None
    phone: str | None = None
    apollo_id: str | None = None


class ClayEnrichInput(BaseModel):
    first_name: str
    last_name: str
    company_domain: str
    requested_data: list[str] = ["phone", "email"]


class FindContactsResult(BaseModel):
    candidates: list[ContactResult]
    total_available: int


class FindPhoneResult(BaseModel):
    phone: str | None = None
    phone_type: str | None = None
    source: str = "apollo"
    confidence: str | None = None
    found: bool = False
    suggested_action: str | None = None


class EnrichContactResult(BaseModel):
    filled: dict[str, str] = {}
    already_populated: list[str] = []
    not_found: list[str] = []
    sources_used: list[str] = []


class AddToHubSpotResult(BaseModel):
    hubspot_contact_id: str
    hubspot_url: str
    action: str
    company_associated: bool = False
    company_name: str | None = None


class ClayEnrichResult(BaseModel):
    enriched_fields: dict[str, str] = {}
    source: str = "clay"
    credits_used: int = 0
    task_status: str = "completed"
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_models.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/knotch_mcp/models.py tests/test_models.py
git commit -m "feat: add Pydantic models for all tool inputs and outputs"
```

---

## Task 3: Rate Limiter + Structured Logger

**Parallelizable with:** Task 2

**Files:**

- Create: `src/knotch_mcp/rate_limit.py`
- Create: `src/knotch_mcp/log.py`
- Create: `tests/test_rate_limit.py`
- Create: `tests/test_log.py`

**Step 1: Write failing tests for rate limiter**

`tests/test_rate_limit.py`:

```python
import asyncio
import time

import pytest

from knotch_mcp.rate_limit import TokenBucket


@pytest.fixture
def bucket():
    return TokenBucket(rate=10.0, capacity=10)


@pytest.mark.asyncio
async def test_acquire_within_capacity(bucket):
    for _ in range(10):
        await bucket.acquire()


@pytest.mark.asyncio
async def test_acquire_blocks_when_empty():
    bucket = TokenBucket(rate=100.0, capacity=1)
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.005


@pytest.mark.asyncio
async def test_bucket_refills_over_time():
    bucket = TokenBucket(rate=1000.0, capacity=2)
    await bucket.acquire()
    await bucket.acquire()
    await asyncio.sleep(0.01)
    await bucket.acquire()
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_rate_limit.py -v
```

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement rate_limit.py**

`src/knotch_mcp/rate_limit.py`:

```python
import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_time = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait_time)
```

**Step 4: Run rate limiter tests, verify they pass**

```bash
python -m pytest tests/test_rate_limit.py -v
```

Expected: all PASS

**Step 5: Write failing tests for logger**

`tests/test_log.py`:

```python
import json
import logging

from knotch_mcp.log import get_logger, ToolLogContext


def test_logger_outputs_json(capfd):
    logger = get_logger("test")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    from knotch_mcp.log import JsonFormatter
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("test message", extra={"tool": "find_contact", "duration_ms": 123})

    captured = capfd.readouterr()
    data = json.loads(captured.err)
    assert data["message"] == "test message"
    assert data["tool"] == "find_contact"
    assert data["duration_ms"] == 123
    assert "timestamp" in data


def test_tool_log_context():
    ctx = ToolLogContext(tool_name="find_contact")
    ctx.add_api_call("apollo")
    ctx.add_api_call("hubspot")
    result = ctx.finish()
    assert result["tool_name"] == "find_contact"
    assert result["apis_called"] == ["apollo", "hubspot"]
    assert "duration_ms" in result
    assert result["duration_ms"] >= 0
```

**Step 6: Implement log.py**

`src/knotch_mcp/log.py`:

```python
import json
import logging
import time
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("tool", "duration_ms", "apis_called", "tool_name"):
            val = getattr(record, key, None)
            if val is not None:
                log_data[key] = val
        return json.dumps(log_data)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class ToolLogContext:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.apis_called: list[str] = []
        self._start = time.monotonic()

    def add_api_call(self, api: str) -> None:
        self.apis_called.append(api)

    def finish(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "apis_called": self.apis_called,
            "duration_ms": int((time.monotonic() - self._start) * 1000),
        }
```

**Step 7: Run all tests, verify they pass**

```bash
python -m pytest tests/test_rate_limit.py tests/test_log.py -v
```

Expected: all PASS

**Step 8: Commit**

```bash
git add src/knotch_mcp/rate_limit.py src/knotch_mcp/log.py tests/test_rate_limit.py tests/test_log.py
git commit -m "feat: add token bucket rate limiter and structured JSON logger"
```

---

## Task 4: Apollo API Client

**Parallelizable with:** Tasks 5, 6

**Files:**

- Create: `src/knotch_mcp/clients/__init__.py`
- Create: `src/knotch_mcp/clients/apollo.py`
- Create: `tests/test_apollo.py`

**Step 1: Write failing tests**

`tests/test_apollo.py`:

```python
import pytest
import httpx
import respx

from knotch_mcp.clients.apollo import ApolloClient
from knotch_mcp.rate_limit import TokenBucket


@pytest.fixture
def apollo():
    bucket = TokenBucket(rate=100.0, capacity=100)
    return ApolloClient(api_key="test-key", rate_limiter=bucket)


@respx.mock
@pytest.mark.asyncio
async def test_people_match(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json={
            "person": {
                "id": "abc123",
                "first_name": "Jane",
                "last_name": "Smith",
                "title": "VP Engineering",
                "organization": {"name": "Stripe", "primary_domain": "stripe.com"},
                "email": "jane@stripe.com",
                "email_status": "verified",
                "linkedin_url": "https://linkedin.com/in/janesmith",
                "city": "San Francisco",
                "state": "California",
                "country": "United States",
                "phone_numbers": [{"raw_number": "+14155551234", "type": "mobile"}],
            }
        })
    )

    result = await apollo.people_match(
        first_name="Jane", last_name="Smith", domain="stripe.com"
    )
    assert result["id"] == "abc123"
    assert result["email"] == "jane@stripe.com"
    assert result["title"] == "VP Engineering"


@respx.mock
@pytest.mark.asyncio
async def test_people_match_not_found(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json={"person": None})
    )

    result = await apollo.people_match(
        first_name="Nobody", last_name="Exists", domain="fake.com"
    )
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_people_search(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_people/api_search").mock(
        return_value=httpx.Response(200, json={
            "people": [
                {"id": "p1", "first_name": "Jane", "last_name": "Smith", "title": "VP Engineering"},
                {"id": "p2", "first_name": "John", "last_name": "Doe", "title": "CTO"},
            ],
            "pagination": {"total_entries": 42},
        })
    )

    people, total = await apollo.people_search(
        titles=["VP Engineering", "CTO"],
        domain="stripe.com",
        per_page=3,
    )
    assert len(people) == 2
    assert total == 42


@respx.mock
@pytest.mark.asyncio
async def test_org_search(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_companies/search").mock(
        return_value=httpx.Response(200, json={
            "organizations": [
                {"name": "Stripe", "primary_domain": "stripe.com"},
            ]
        })
    )

    domain = await apollo.resolve_company_domain("Stripe")
    assert domain == "stripe.com"


@respx.mock
@pytest.mark.asyncio
async def test_org_search_no_result(apollo):
    respx.post("https://api.apollo.io/api/v1/mixed_companies/search").mock(
        return_value=httpx.Response(200, json={"organizations": []})
    )

    domain = await apollo.resolve_company_domain("FakeCompany12345")
    assert domain is None


@respx.mock
@pytest.mark.asyncio
async def test_people_match_with_phone(apollo):
    respx.post("https://api.apollo.io/api/v1/people/match").mock(
        return_value=httpx.Response(200, json={
            "person": {
                "id": "abc123",
                "first_name": "Jane",
                "last_name": "Smith",
                "title": "VP Engineering",
                "organization": {"name": "Stripe", "primary_domain": "stripe.com"},
                "email": "jane@stripe.com",
                "email_status": "verified",
                "linkedin_url": None,
                "city": None,
                "state": None,
                "country": None,
                "phone_numbers": [
                    {"raw_number": "+14155551234", "type": "mobile"},
                ],
            }
        })
    )

    result = await apollo.people_match(
        first_name="Jane", last_name="Smith", domain="stripe.com",
        reveal_phone_number=True,
    )
    assert result["phone_numbers"][0]["raw_number"] == "+14155551234"
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_apollo.py -v
```

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement Apollo client**

`src/knotch_mcp/clients/__init__.py`:

```python

```

`src/knotch_mcp/clients/apollo.py`:

```python
from __future__ import annotations

import httpx

from knotch_mcp.rate_limit import TokenBucket

BASE_URL = "https://api.apollo.io/api/v1"
TIMEOUT = 20.0


class ApolloClient:
    def __init__(self, api_key: str, rate_limiter: TokenBucket):
        self._api_key = api_key
        self._rate_limiter = rate_limiter
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)

    async def _post(self, path: str, body: dict) -> dict:
        await self._rate_limiter.acquire()
        body["api_key"] = self._api_key
        resp = await self._client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    async def people_match(
        self,
        first_name: str | None = None,
        last_name: str | None = None,
        domain: str | None = None,
        organization_name: str | None = None,
        email: str | None = None,
        linkedin_url: str | None = None,
        apollo_id: str | None = None,
        reveal_phone_number: bool = False,
        reveal_personal_emails: bool = False,
    ) -> dict | None:
        body: dict = {}
        if first_name:
            body["first_name"] = first_name
        if last_name:
            body["last_name"] = last_name
        if domain:
            body["domain"] = domain
        if organization_name:
            body["organization_name"] = organization_name
        if email:
            body["email"] = email
        if linkedin_url:
            body["linkedin_url"] = linkedin_url
        if apollo_id:
            body["id"] = apollo_id
        if reveal_phone_number:
            body["reveal_phone_number"] = True
        if reveal_personal_emails:
            body["reveal_personal_emails"] = True

        data = await self._post("/people/match", body)
        return data.get("person")

    async def people_search(
        self,
        titles: list[str] | None = None,
        domain: str | None = None,
        seniority: list[str] | None = None,
        per_page: int = 10,
        page: int = 1,
    ) -> tuple[list[dict], int]:
        body: dict = {"page": page, "per_page": per_page}
        if titles:
            body["person_titles"] = titles
        if domain:
            body["q_organization_domains_list"] = [domain]
        if seniority:
            body["person_seniorities"] = seniority

        data = await self._post("/mixed_people/api_search", body)
        people = data.get("people", [])
        total = data.get("pagination", {}).get("total_entries", 0)
        return people, total

    async def resolve_company_domain(self, company_name: str) -> str | None:
        body = {"q_organization_name": company_name, "per_page": 1, "page": 1}
        data = await self._post("/mixed_companies/search", body)
        orgs = data.get("organizations", [])
        if orgs:
            return orgs[0].get("primary_domain")
        return None

    async def close(self) -> None:
        await self._client.aclose()
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_apollo.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/knotch_mcp/clients/ tests/test_apollo.py
git commit -m "feat: add Apollo API client with people match, search, and org lookup"
```

---

## Task 5: Clay API Client

**Parallelizable with:** Tasks 4, 6

**Files:**

- Create: `src/knotch_mcp/clients/clay.py`
- Create: `tests/test_clay.py`

**Step 1: Write failing tests**

`tests/test_clay.py`:

```python
import pytest
import httpx
import respx

from knotch_mcp.clients.clay import ClayClient


@pytest.fixture
def clay():
    return ClayClient(api_key="test-key")


@respx.mock
@pytest.mark.asyncio
async def test_find_and_enrich_contacts(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-abc"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-abc").mock(
        return_value=httpx.Response(200, json={
            "status": "completed",
            "results": [
                {
                    "contactName": "Jane Smith",
                    "email": "jane@stripe.com",
                    "phone": "+14155551234",
                    "linkedinUrl": "https://linkedin.com/in/janesmith",
                }
            ],
            "creditsUsed": 3,
        })
    )

    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Jane Smith", "companyIdentifier": "stripe.com"}],
    )
    assert result["status"] == "completed"
    assert len(result["results"]) == 1
    assert result["results"][0]["email"] == "jane@stripe.com"


@respx.mock
@pytest.mark.asyncio
async def test_find_contacts_at_company(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-contacts-at-company").mock(
        return_value=httpx.Response(200, json={"taskId": "task-xyz"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-xyz").mock(
        return_value=httpx.Response(200, json={
            "status": "completed",
            "results": [
                {"contactName": "John Doe", "title": "CTO"},
            ],
            "creditsUsed": 2,
        })
    )

    result = await clay.find_contacts_at_company(
        company_domain="stripe.com",
        job_title_keywords=["CTO"],
    )
    assert result["status"] == "completed"
    assert result["results"][0]["title"] == "CTO"


@respx.mock
@pytest.mark.asyncio
async def test_poll_task_timeout(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-slow"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-slow").mock(
        return_value=httpx.Response(200, json={"status": "processing"})
    )

    # Use a short timeout for testing
    clay._poll_timeout = 0.5
    clay._poll_initial_delay = 0.1

    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Slow Person", "companyIdentifier": "slow.com"}],
    )
    assert result["status"] == "timeout"


@respx.mock
@pytest.mark.asyncio
async def test_find_and_enrich_with_custom_data_points(clay):
    respx.post("https://api.clay.com/v1/find-and-enrich-list-of-contacts").mock(
        return_value=httpx.Response(200, json={"taskId": "task-phone"})
    )
    respx.get("https://api.clay.com/v1/tasks/task-phone").mock(
        return_value=httpx.Response(200, json={
            "status": "completed",
            "results": [{"contactName": "Jane Smith", "phone": "+14155551234"}],
            "creditsUsed": 5,
        })
    )

    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": "Jane Smith", "companyIdentifier": "stripe.com"}],
        contact_data_points=[
            {"type": "Email"},
            {"type": "Custom", "dataPointName": "Phone Number", "dataPointDescription": "Find direct phone number"},
        ],
    )
    assert result["status"] == "completed"
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_clay.py -v
```

Expected: FAIL

**Step 3: Implement Clay client**

`src/knotch_mcp/clients/clay.py`:

```python
from __future__ import annotations

import asyncio
import time

import httpx

BASE_URL = "https://api.clay.com/v1"
TIMEOUT = 30.0
POLL_TIMEOUT = 45.0
POLL_INITIAL_DELAY = 2.0
POLL_BACKOFF_FACTOR = 2.0
POLL_MAX_DELAY = 8.0


class ClayClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)
        self._poll_timeout = POLL_TIMEOUT
        self._poll_initial_delay = POLL_INITIAL_DELAY

    async def _post(self, path: str, body: dict) -> dict:
        resp = await self._client.post(
            path, json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _get(self, path: str) -> dict:
        resp = await self._client.get(
            path,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_task(self, task_id: str) -> dict:
        delay = self._poll_initial_delay
        deadline = time.monotonic() + self._poll_timeout

        while time.monotonic() < deadline:
            result = await self._get(f"/tasks/{task_id}")
            if result.get("status") not in ("processing", "pending", "queued"):
                return result
            await asyncio.sleep(delay)
            delay = min(delay * POLL_BACKOFF_FACTOR, POLL_MAX_DELAY)

        return {"status": "timeout", "taskId": task_id}

    async def find_and_enrich_contacts(
        self,
        contacts: list[dict],
        contact_data_points: list[dict] | None = None,
        company_data_points: list[dict] | None = None,
    ) -> dict:
        body: dict = {"contactIdentifiers": contacts}
        data_points: dict = {}
        if contact_data_points:
            data_points["contactDataPoints"] = contact_data_points
        if company_data_points:
            data_points["companyDataPoints"] = company_data_points
        if data_points:
            body["dataPoints"] = data_points

        resp = await self._post("/find-and-enrich-list-of-contacts", body)
        task_id = resp["taskId"]
        return await self._poll_task(task_id)

    async def find_contacts_at_company(
        self,
        company_domain: str,
        job_title_keywords: list[str] | None = None,
        locations: list[str] | None = None,
    ) -> dict:
        body: dict = {"companyIdentifier": company_domain}
        filters: dict = {}
        if job_title_keywords:
            filters["job_title_keywords"] = job_title_keywords
        if locations:
            filters["locations"] = locations
        if filters:
            body["contactFilters"] = filters

        resp = await self._post("/find-and-enrich-contacts-at-company", body)
        task_id = resp["taskId"]
        return await self._poll_task(task_id)

    async def close(self) -> None:
        await self._client.aclose()
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_clay.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/knotch_mcp/clients/clay.py tests/test_clay.py
git commit -m "feat: add Clay API client with async task polling and exponential backoff"
```

---

## Task 6: HubSpot API Client

**Parallelizable with:** Tasks 4, 5

**Files:**

- Create: `src/knotch_mcp/clients/hubspot.py`
- Create: `tests/test_hubspot.py`

**Step 1: Write failing tests**

`tests/test_hubspot.py`:

```python
import pytest
import httpx
import respx

from knotch_mcp.clients.hubspot import HubSpotClient


@pytest.fixture
def hubspot():
    return HubSpotClient(access_token="test-token", portal_id="12345")


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_by_email(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={
            "total": 1,
            "results": [{
                "id": "501",
                "properties": {
                    "firstname": "Jane",
                    "lastname": "Smith",
                    "email": "jane@stripe.com",
                    "jobtitle": "VP Engineering",
                },
            }],
        })
    )

    results = await hubspot.search_contacts_by_email("jane@stripe.com")
    assert len(results) == 1
    assert results[0]["id"] == "501"


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_by_linkedin(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={
            "total": 1,
            "results": [{"id": "502", "properties": {"hs_linkedin_url": "https://linkedin.com/in/janesmith"}}],
        })
    )

    results = await hubspot.search_contacts_by_linkedin("https://linkedin.com/in/janesmith")
    assert len(results) == 1


@respx.mock
@pytest.mark.asyncio
async def test_search_contact_not_found(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "results": []})
    )

    results = await hubspot.search_contacts_by_email("nobody@fake.com")
    assert len(results) == 0


@respx.mock
@pytest.mark.asyncio
async def test_get_contact(hubspot):
    respx.get("https://api.hubapi.com/crm/v3/objects/contacts/501").mock(
        return_value=httpx.Response(200, json={
            "id": "501",
            "properties": {
                "firstname": "Jane",
                "lastname": "Smith",
                "email": "jane@stripe.com",
                "jobtitle": "VP Engineering",
                "phone": "",
                "hs_linkedin_url": "https://linkedin.com/in/janesmith",
                "city": "",
                "state": "",
                "country": "",
                "company": "Stripe",
            },
        })
    )

    contact = await hubspot.get_contact("501")
    assert contact["properties"]["email"] == "jane@stripe.com"
    assert contact["properties"]["phone"] == ""


@respx.mock
@pytest.mark.asyncio
async def test_create_contact(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(201, json={
            "id": "601",
            "properties": {"email": "new@stripe.com", "firstname": "New"},
        })
    )

    contact = await hubspot.create_contact({
        "email": "new@stripe.com",
        "firstname": "New",
        "lastname": "Person",
    })
    assert contact["id"] == "601"


@respx.mock
@pytest.mark.asyncio
async def test_update_contact(hubspot):
    respx.patch("https://api.hubapi.com/crm/v3/objects/contacts/501").mock(
        return_value=httpx.Response(200, json={
            "id": "501",
            "properties": {"jobtitle": "SVP Engineering"},
        })
    )

    contact = await hubspot.update_contact("501", {"jobtitle": "SVP Engineering"})
    assert contact["properties"]["jobtitle"] == "SVP Engineering"


@respx.mock
@pytest.mark.asyncio
async def test_search_company_by_domain(hubspot):
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={
            "total": 1,
            "results": [{"id": "801", "properties": {"name": "Stripe", "domain": "stripe.com"}}],
        })
    )

    results = await hubspot.search_companies_by_domain("stripe.com")
    assert len(results) == 1
    assert results[0]["id"] == "801"


@respx.mock
@pytest.mark.asyncio
async def test_associate_contact_company(hubspot):
    respx.put(
        "https://api.hubapi.com/crm/v3/objects/contacts/501/associations/companies/801/default"
    ).mock(return_value=httpx.Response(200, json={}))

    await hubspot.associate_contact_to_company("501", "801")


def test_build_contact_url(hubspot):
    url = hubspot.build_contact_url("501")
    assert url == "https://app.hubspot.com/contacts/12345/contact/501"
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_hubspot.py -v
```

Expected: FAIL

**Step 3: Implement HubSpot client**

`src/knotch_mcp/clients/hubspot.py`:

```python
from __future__ import annotations

import httpx

BASE_URL = "https://api.hubapi.com"
TIMEOUT = 10.0

CONTACT_PROPERTIES = [
    "firstname", "lastname", "email", "jobtitle", "phone",
    "hs_linkedin_url", "city", "state", "country", "company",
]


class HubSpotClient:
    def __init__(self, access_token: str, portal_id: str):
        self._portal_id = portal_id
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def _search(self, object_type: str, filters: list[dict], properties: list[str] | None = None) -> list[dict]:
        body: dict = {
            "filterGroups": [{"filters": filters}],
            "limit": 10,
        }
        if properties:
            body["properties"] = properties

        resp = await self._client.post(f"/crm/v3/objects/{object_type}/search", json=body)
        resp.raise_for_status()
        return resp.json().get("results", [])

    async def search_contacts_by_email(self, email: str) -> list[dict]:
        return await self._search(
            "contacts",
            [{"propertyName": "email", "operator": "EQ", "value": email}],
            properties=CONTACT_PROPERTIES,
        )

    async def search_contacts_by_linkedin(self, linkedin_url: str) -> list[dict]:
        return await self._search(
            "contacts",
            [{"propertyName": "hs_linkedin_url", "operator": "EQ", "value": linkedin_url}],
            properties=CONTACT_PROPERTIES,
        )

    async def get_contact(self, contact_id: str) -> dict:
        resp = await self._client.get(
            f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": ",".join(CONTACT_PROPERTIES)},
        )
        resp.raise_for_status()
        return resp.json()

    async def create_contact(self, properties: dict) -> dict:
        resp = await self._client.post(
            "/crm/v3/objects/contacts",
            json={"properties": properties},
        )
        resp.raise_for_status()
        return resp.json()

    async def update_contact(self, contact_id: str, properties: dict) -> dict:
        resp = await self._client.patch(
            f"/crm/v3/objects/contacts/{contact_id}",
            json={"properties": properties},
        )
        resp.raise_for_status()
        return resp.json()

    async def search_companies_by_domain(self, domain: str) -> list[dict]:
        return await self._search(
            "companies",
            [{"propertyName": "domain", "operator": "EQ", "value": domain}],
            properties=["name", "domain"],
        )

    async def associate_contact_to_company(self, contact_id: str, company_id: str) -> None:
        resp = await self._client.put(
            f"/crm/v3/objects/contacts/{contact_id}/associations/companies/{company_id}/default"
        )
        resp.raise_for_status()

    def build_contact_url(self, contact_id: str) -> str:
        return f"https://app.hubspot.com/contacts/{self._portal_id}/contact/{contact_id}"

    async def close(self) -> None:
        await self._client.aclose()
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_hubspot.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/knotch_mcp/clients/hubspot.py tests/test_hubspot.py
git commit -m "feat: add HubSpot API client with search, CRUD, and company association"
```

---

## Task 7: MCP Tool Definitions

**Parallelizable with:** None (depends on Tasks 4, 5, 6)

This is the largest task. It implements all 6 MCP tools with orchestration logic. Each tool function uses the API clients and returns structured results.

**Files:**

- Create: `src/knotch_mcp/tools.py`
- Create: `tests/test_tools.py`

**Step 1: Write failing tests**

`tests/test_tools.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from knotch_mcp.models import (
    ContactResult, FindContactsResult, FindPhoneResult,
    EnrichContactResult, AddToHubSpotResult, ClayEnrichResult,
)


@pytest.fixture
def mock_apollo():
    client = AsyncMock()
    client.resolve_company_domain = AsyncMock(return_value="stripe.com")
    client.people_match = AsyncMock(return_value={
        "id": "abc123",
        "first_name": "Jane",
        "last_name": "Smith",
        "title": "VP Engineering",
        "organization": {"name": "Stripe", "primary_domain": "stripe.com"},
        "email": "jane@stripe.com",
        "email_status": "verified",
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "city": "San Francisco",
        "state": "California",
        "country": "United States",
        "phone_numbers": [],
    })
    client.people_search = AsyncMock(return_value=(
        [
            {"id": "p1", "first_name": "Jane", "last_name": "Smith", "title": "VP Engineering"},
            {"id": "p2", "first_name": "John", "last_name": "Doe", "title": "CTO"},
        ],
        42,
    ))
    return client


@pytest.fixture
def mock_hubspot():
    client = AsyncMock()
    client.search_contacts_by_email = AsyncMock(return_value=[])
    client.search_contacts_by_linkedin = AsyncMock(return_value=[])
    client.build_contact_url = lambda cid: f"https://app.hubspot.com/contacts/12345/contact/{cid}"
    client.get_contact = AsyncMock(return_value={
        "id": "501",
        "properties": {
            "firstname": "Jane", "lastname": "Smith", "email": "jane@stripe.com",
            "jobtitle": "", "phone": "", "hs_linkedin_url": "",
            "city": "", "state": "", "country": "", "company": "Stripe",
        },
    })
    client.create_contact = AsyncMock(return_value={"id": "601", "properties": {}})
    client.update_contact = AsyncMock(return_value={"id": "501", "properties": {}})
    client.search_companies_by_domain = AsyncMock(return_value=[
        {"id": "801", "properties": {"name": "Stripe", "domain": "stripe.com"}},
    ])
    client.associate_contact_to_company = AsyncMock()
    return client


@pytest.fixture
def mock_clay():
    client = AsyncMock()
    client.find_and_enrich_contacts = AsyncMock(return_value={
        "status": "completed",
        "results": [{"contactName": "Jane Smith", "phone": "+14155551234"}],
        "creditsUsed": 3,
    })
    return client


@pytest.mark.asyncio
async def test_find_contact_by_details_found_not_in_hubspot(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _find_contact_by_details
    result = await _find_contact_by_details(
        "Jane", "Smith", "Stripe", None, None,
        mock_apollo, mock_hubspot,
    )
    assert isinstance(result, ContactResult)
    assert result.name == "Jane Smith"
    assert result.email == "jane@stripe.com"
    assert result.hubspot_status == "not_found"
    assert "add_to_hubspot" in result.suggested_actions
    assert "phone" in result.gaps
    mock_apollo.resolve_company_domain.assert_called_once_with("Stripe")


@pytest.mark.asyncio
async def test_find_contact_by_details_already_in_hubspot(mock_apollo, mock_hubspot, mock_clay):
    mock_hubspot.search_contacts_by_email.return_value = [
        {"id": "501", "properties": {"email": "jane@stripe.com"}},
    ]
    from knotch_mcp.tools import _find_contact_by_details
    result = await _find_contact_by_details(
        "Jane", "Smith", "stripe.com", "jane@stripe.com", None,
        mock_apollo, mock_hubspot,
    )
    assert result.hubspot_status == "found"
    assert result.hubspot_contact_id == "501"
    assert "add_to_hubspot" not in result.suggested_actions


@pytest.mark.asyncio
async def test_find_contact_not_found_in_apollo(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = None
    from knotch_mcp.tools import _find_contact_by_details
    result = await _find_contact_by_details(
        "Nobody", "Exists", "fake.com", None, None,
        mock_apollo, mock_hubspot,
    )
    assert result.name == "Nobody Exists"
    assert result.sources == []
    assert "apollo_returned_no_match" in result.gaps


@pytest.mark.asyncio
async def test_find_contacts_by_role(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _find_contacts_by_role
    result = await _find_contacts_by_role(
        ["VP Engineering", "CTO"], "Stripe", None, 3,
        mock_apollo, mock_hubspot,
    )
    assert isinstance(result, FindContactsResult)
    assert result.total_available == 42
    assert len(result.candidates) >= 1


@pytest.mark.asyncio
async def test_find_phone_found(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = {
        "id": "abc123", "first_name": "Jane", "last_name": "Smith",
        "phone_numbers": [{"raw_number": "+14155551234", "type": "mobile"}],
        "email": "jane@stripe.com", "title": "VP", "organization": {},
        "email_status": None, "linkedin_url": None, "city": None, "state": None, "country": None,
    }
    from knotch_mcp.tools import _find_phone
    result = await _find_phone(None, "jane@stripe.com", None, None, mock_apollo)
    assert isinstance(result, FindPhoneResult)
    assert result.found is True
    assert result.phone == "+14155551234"


@pytest.mark.asyncio
async def test_find_phone_not_found(mock_apollo, mock_hubspot, mock_clay):
    mock_apollo.people_match.return_value = {
        "id": "abc123", "first_name": "Jane", "last_name": "Smith",
        "phone_numbers": [],
        "email": "jane@stripe.com", "title": "VP", "organization": {},
        "email_status": None, "linkedin_url": None, "city": None, "state": None, "country": None,
    }
    from knotch_mcp.tools import _find_phone
    result = await _find_phone(None, "jane@stripe.com", None, None, mock_apollo)
    assert result.found is False
    assert result.suggested_action == "clay_enrich"


@pytest.mark.asyncio
async def test_enrich_contact(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _enrich_contact
    result = await _enrich_contact("501", mock_apollo, mock_hubspot, mock_clay)
    assert isinstance(result, EnrichContactResult)
    assert "jobtitle" in result.filled
    assert "email" in result.already_populated
    assert "apollo" in result.sources_used


@pytest.mark.asyncio
async def test_add_to_hubspot_creates_new(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _add_to_hubspot
    result = await _add_to_hubspot(
        "Jane", "Smith", "jane@stripe.com", "VP Engineering",
        "Stripe", "stripe.com", "https://linkedin.com/in/janesmith",
        None, None, None,
        mock_hubspot,
    )
    assert isinstance(result, AddToHubSpotResult)
    assert result.action == "created"
    assert result.hubspot_contact_id == "601"
    assert result.company_associated is True
    assert result.company_name == "Stripe"


@pytest.mark.asyncio
async def test_add_to_hubspot_updates_existing(mock_apollo, mock_hubspot, mock_clay):
    mock_hubspot.search_contacts_by_email.return_value = [
        {"id": "501", "properties": {"email": "jane@stripe.com"}},
    ]
    from knotch_mcp.tools import _add_to_hubspot
    result = await _add_to_hubspot(
        "Jane", "Smith", "jane@stripe.com", "VP Engineering",
        "Stripe", "stripe.com", None, None, None, None,
        mock_hubspot,
    )
    assert result.action == "updated"
    assert result.hubspot_contact_id == "501"


@pytest.mark.asyncio
async def test_clay_enrich_success(mock_apollo, mock_hubspot, mock_clay):
    from knotch_mcp.tools import _clay_enrich
    result = await _clay_enrich("Jane", "Smith", "stripe.com", ["phone", "email"], mock_clay)
    assert isinstance(result, ClayEnrichResult)
    assert result.task_status == "completed"
    assert "phone" in result.enriched_fields
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_tools.py -v
```

Expected: FAIL

**Step 3: Implement tools.py**

`src/knotch_mcp/tools.py`:

```python
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
    return "." in company


def _extract_contact(person: dict) -> ContactResult:
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
    )


async def _check_hubspot(
    contact: ContactResult, hubspot: HubSpotClient
) -> ContactResult:
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


async def _find_contact_by_details(
    first_name: str,
    last_name: str,
    company: str,
    email: str | None,
    linkedin_url: str | None,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> ContactResult:
    log_ctx = ToolLogContext("find_contact_by_details")

    domain = company if _is_domain(company) else await apollo.resolve_company_domain(company)
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


async def _find_contacts_by_role(
    titles: list[str],
    company: str,
    seniority: str | None,
    limit: int,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
) -> FindContactsResult:
    log_ctx = ToolLogContext("find_contacts_by_role")

    domain = company if _is_domain(company) else await apollo.resolve_company_domain(company)
    if not _is_domain(company):
        log_ctx.add_api_call("apollo")

    seniority_list = [seniority] if seniority else None
    people_raw, total = await apollo.people_search(
        titles=titles, domain=domain, seniority=seniority_list, per_page=limit,
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


async def _find_phone(
    apollo_id: str | None,
    email: str | None,
    linkedin_url: str | None,
    name: str | None,
    apollo: ApolloClient,
) -> FindPhoneResult:
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
        return FindPhoneResult(found=False, source="apollo", suggested_action="clay_enrich")

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


async def _enrich_contact(
    hubspot_contact_id: str,
    apollo: ApolloClient,
    hubspot: HubSpotClient,
    clay: ClayClient,
) -> EnrichContactResult:
    log_ctx = ToolLogContext("enrich_contact")

    hs_contact = await hubspot.get_contact(hubspot_contact_id)
    log_ctx.add_api_call("hubspot")
    props = hs_contact.get("properties", {})

    enrichable_fields = ["jobtitle", "phone", "hs_linkedin_url", "city", "state", "country", "company"]
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

    if still_missing:
        first = props.get("firstname", "")
        last = props.get("lastname", "")
        domain = props.get("company", "")

        if first and last and domain:
            clay_data_points = [{"type": "Email"}]
            if "phone" in still_missing:
                clay_data_points.append({
                    "type": "Custom",
                    "dataPointName": "Phone Number",
                    "dataPointDescription": "Find direct phone number",
                })

            clay_result = await clay.find_and_enrich_contacts(
                contacts=[{"contactName": f"{first} {last}", "companyIdentifier": domain}],
                contact_data_points=clay_data_points,
            )
            log_ctx.add_api_call("clay")

            if clay_result.get("status") == "completed":
                sources.append("clay")
                clay_contacts = clay_result.get("results", [])
                if clay_contacts:
                    c = clay_contacts[0]
                    clay_field_map = {
                        "phone": "phone",
                        "hs_linkedin_url": "linkedinUrl",
                        "city": "city",
                        "state": "state",
                        "country": "country",
                    }
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


async def _clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str],
    clay: ClayClient,
) -> ClayEnrichResult:
    log_ctx = ToolLogContext("clay_enrich")

    contact_data_points: list[dict] = []
    if "email" in requested_data:
        contact_data_points.append({"type": "Email"})
    if "phone" in requested_data:
        contact_data_points.append({
            "type": "Custom",
            "dataPointName": "Phone Number",
            "dataPointDescription": "Find direct phone number",
        })
    if "work_history" in requested_data:
        contact_data_points.append({"type": "Summarize Work History"})

    result = await clay.find_and_enrich_contacts(
        contacts=[{"contactName": f"{first_name} {last_name}", "companyIdentifier": company_domain}],
        contact_data_points=contact_data_points if contact_data_points else None,
    )
    log_ctx.add_api_call("clay")

    enriched_fields: dict[str, str] = {}
    if result.get("status") == "completed":
        contacts = result.get("results", [])
        if contacts:
            c = contacts[0]
            for key in ("email", "phone", "linkedinUrl", "title", "workHistory"):
                if c.get(key):
                    enriched_fields[key] = str(c[key])

    logger.info("tool completed", extra=log_ctx.finish())
    return ClayEnrichResult(
        enriched_fields=enriched_fields,
        credits_used=result.get("creditsUsed", 0),
        task_status=result.get("status", "unknown"),
    )
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_tools.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/knotch_mcp/tools.py tests/test_tools.py
git commit -m "feat: implement all 6 MCP tools with Apollo/Clay/HubSpot orchestration"
```

---

## Task 8: Server Bootstrap + Auth

**Parallelizable with:** None (depends on Task 7)

**Files:**

- Create: `src/knotch_mcp/server.py`
- Create: `tests/test_server.py`

**Step 1: Write failing tests**

`tests/test_server.py`:

```python
import pytest
from unittest.mock import patch, AsyncMock

from knotch_mcp.config import Settings


def test_settings_load():
    s = Settings(
        mcp_auth_token="tok",
        apollo_api_key="ak",
        clay_api_key="ck",
        hubspot_private_app_token="ht",
    )
    assert s.mcp_auth_token == "tok"
    assert s.port == 8080


def test_auth_check_rejects_bad_token():
    from knotch_mcp.server import _check_auth
    assert _check_auth("wrong-token", "correct-token") is False


def test_auth_check_accepts_good_token():
    from knotch_mcp.server import _check_auth
    assert _check_auth("correct-token", "correct-token") is True


def test_auth_check_accepts_bearer_prefix():
    from knotch_mcp.server import _check_auth
    assert _check_auth("Bearer correct-token", "correct-token") is True


def test_mcp_instance_exists():
    from knotch_mcp.server import mcp
    assert mcp is not None
    assert mcp.name == "KnotchMCP"
```

**Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_server.py -v
```

Expected: FAIL

**Step 3: Implement server.py**

`src/knotch_mcp/server.py`:

```python
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
    _clay_enrich,
    _enrich_contact,
    _find_contact_by_details,
    _find_contacts_by_role,
    _find_phone,
)

logger = get_logger("knotch_mcp.server")
settings = Settings()

mcp = FastMCP("KnotchMCP")

_rate_limiter = TokenBucket(rate=settings.apollo_rate_limit / 60.0, capacity=settings.apollo_rate_limit)
_apollo = ApolloClient(api_key=settings.apollo_api_key, rate_limiter=_rate_limiter)
_clay = ClayClient(api_key=settings.clay_api_key)
_hubspot = HubSpotClient(access_token=settings.hubspot_private_app_token, portal_id=settings.hubspot_portal_id)


def _check_auth(provided: str, expected: str) -> bool:
    token = provided.removeprefix("Bearer ").strip()
    return token == expected


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
        first_name, last_name, email, title, company, company_domain,
        linkedin_url, location, phone, apollo_id, _hubspot
    )
    return result.model_dump()


@mcp.tool()
async def clay_enrich(
    first_name: str,
    last_name: str,
    company_domain: str,
    requested_data: list[str] | None = None,
) -> dict:
    """Enrich a contact using Clay. Use this as a follow-up when Apollo didn't
    return phone, email, or other data. requested_data options: phone, email,
    work_history. Consumes Clay credits."""
    data = requested_data or ["phone", "email"]
    result = await _clay_enrich(first_name, last_name, company_domain, data, _clay)
    return result.model_dump()
```

**Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_server.py -v
```

Expected: all PASS

**Step 4b: Update `tests/conftest.py` for module-level imports**

`server.py` instantiates `Settings()` at module level. Tests that import from `server` will fail without env vars. Add these lines **at the top** of `tests/conftest.py` (before any imports from knotch_mcp):

```python
import os

os.environ.setdefault("MCP_AUTH_TOKEN", "test-token")
os.environ.setdefault("APOLLO_API_KEY", "test-key")
os.environ.setdefault("CLAY_API_KEY", "test-key")
os.environ.setdefault("HUBSPOT_PRIVATE_APP_TOKEN", "test-key")
```

This must be done **before** running any tests in Step 4c.

**Step 4c: Run tests, verify they pass**

```bash
python -m pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Verify server starts in both modes**

```bash
# Test stdio mode (should start and accept input)
timeout 3 python -m knotch_mcp 2>/dev/null || true

# Test SSE mode (should start listening)
timeout 3 python -m knotch_mcp --http 2>/dev/null || true
```

**Step 6: Commit**

```bash
git add src/knotch_mcp/server.py tests/test_server.py tests/conftest.py
git commit -m "feat: add FastMCP server bootstrap with auth and all 6 tool registrations"
```

---

## Task 9: Deployment + Documentation

**Parallelizable with:** None (depends on Task 8)

**Files:**

- Create: `Dockerfile`
- Create: `railway.toml`
- Create: `README.md`

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "-m", "knotch_mcp", "--http"]
```

**Step 2: Create railway.toml**

```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "python -m knotch_mcp --http"
healthcheckPath = "/sse"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3
```

**Step 3: Create README.md**

Write a comprehensive README covering:

- What KnotchMCP is and what it does
- Prerequisites (Python 3.11+, API keys)
- Local development setup (clone, venv, pip install, .env)
- How each tool works (one paragraph + input/output summary per tool)
- How to run locally (stdio mode and SSE mode)
- How to deploy to Railway (step by step)
- Claude Desktop / Cowork client config snippets
- Environment variable reference

The README should include this client config example:

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

**Step 4: Verify Docker build**

```bash
docker build -t knotch-mcp .
```

Expected: successful build

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass

**Step 6: Commit**

```bash
git add Dockerfile railway.toml README.md
git commit -m "feat: add Dockerfile, Railway config, and comprehensive README"
```

---

## Recovery State

<!-- Auto-updated during execution. Read this first if resuming after compaction. -->

**Last completed:** {not started}
**Next:** Wave 1, Task 1
**Branch:** main
**Test baseline:** 0 passing (greenfield)
**Key decisions:** {none yet}
**Blockers:** {none}
