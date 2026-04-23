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
        q_keywords: str | None = None,
    ) -> tuple[list[dict], int]:
        body: dict = {"page": page, "per_page": per_page}
        if titles:
            body["person_titles"] = titles
        if domain:
            body["q_organization_domains_list"] = [domain]
        if seniority:
            body["person_seniorities"] = seniority
        if q_keywords:
            body["q_keywords"] = q_keywords
        data = await self._post("/mixed_people/api_search", body)
        people = data.get("people", [])
        total = data.get("pagination", {}).get("total_entries", 0)
        return people, total

    async def resolve_company_domain(self, company_name: str) -> str | None:
        body = {"q_organization_name": company_name, "per_page": 1, "page": 1}
        data = await self._post("/mixed_companies/search", body)
        orgs = data.get("organizations") or data.get("accounts") or []
        if orgs:
            return orgs[0].get("primary_domain")
        return None

    async def resolve_company_domains(
        self, company_name: str, limit: int = 3
    ) -> list[tuple[str, str]]:
        body = {"q_organization_name": company_name, "per_page": limit, "page": 1}
        data = await self._post("/mixed_companies/search", body)
        orgs = data.get("organizations") or data.get("accounts") or []
        return [
            (o.get("name", ""), o.get("primary_domain", ""))
            for o in orgs
            if o.get("primary_domain")
        ]

    async def close(self) -> None:
        await self._client.aclose()
