from __future__ import annotations

import asyncio

import httpx

BASE_URL = "https://api.hubapi.com"

CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "jobtitle",
    "phone",
    "hs_linkedin_url",
    "city",
    "state",
    "country",
    "company",
]


class HubSpotClient:
    def __init__(
        self,
        access_token: str,
        portal_id: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: float = 15.0,
    ):
        self._portal_id = portal_id
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.request(method, url, json=json, params=params)
                if resp.status_code == 429:
                    if attempt < self._max_retries:
                        delay = float(
                            resp.headers.get(
                                "Retry-After",
                                self._base_delay * (2**attempt),
                            )
                        )
                        await asyncio.sleep(min(delay, 10.0))
                        continue
                resp.raise_for_status()
                return resp
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in (429, 502, 503)
                    and attempt < self._max_retries
                ):
                    last_exc = exc
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    async def _search(
        self,
        object_type: str,
        filters: list[dict],
        properties: list[str] | None = None,
    ) -> list[dict]:
        body: dict = {
            "filterGroups": [{"filters": filters}],
            "limit": 10,
        }
        if properties:
            body["properties"] = properties
        resp = await self._request_with_retry(
            "POST", f"/crm/v3/objects/{object_type}/search", json=body
        )
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
            [
                {
                    "propertyName": "hs_linkedin_url",
                    "operator": "EQ",
                    "value": linkedin_url,
                }
            ],
            properties=CONTACT_PROPERTIES,
        )

    async def get_contact(self, contact_id: str) -> dict:
        resp = await self._request_with_retry(
            "GET",
            f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": ",".join(CONTACT_PROPERTIES)},
        )
        return resp.json()

    async def create_contact(self, properties: dict) -> dict:
        resp = await self._request_with_retry(
            "POST", "/crm/v3/objects/contacts", json={"properties": properties}
        )
        return resp.json()

    async def update_contact(self, contact_id: str, properties: dict) -> dict:
        resp = await self._request_with_retry(
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            json={"properties": properties},
        )
        return resp.json()

    async def create_company(self, properties: dict) -> dict:
        resp = await self._request_with_retry(
            "POST", "/crm/v3/objects/companies", json={"properties": properties}
        )
        return resp.json()

    async def search_companies_by_domain(self, domain: str) -> list[dict]:
        return await self._search(
            "companies",
            [{"propertyName": "domain", "operator": "EQ", "value": domain}],
            properties=["name", "domain"],
        )

    async def associate_contact_to_company(
        self, contact_id: str, company_id: str
    ) -> None:
        await self._request_with_retry(
            "PUT",
            f"/crm/v3/objects/contacts/{contact_id}/associations/companies/{company_id}/default",
        )

    def build_contact_url(self, contact_id: str) -> str:
        return (
            f"https://app.hubspot.com/contacts/{self._portal_id}/contact/{contact_id}"
        )

    async def close(self) -> None:
        await self._client.aclose()
