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

DEAL_CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "jobtitle",
    "company",
    "hs_buying_role",
    "hs_persona",
    "seniority_level__knotch_",
    "hubspot_owner_id",
    "notes_last_updated",
    "num_notes",
    "hs_linkedin_url",
    "hs_lead_status",
    "lifecyclestage",
    "phone",
]

DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "pipeline",
    "amount",
    "closedate",
    "hubspot_owner_id",
    "hs_lastmodifieddate",
    "num_associated_contacts",
    "description",
    "notes_last_updated",
    "num_contacted_notes",
    "createdate",
]

MEETING_PROPERTIES = [
    "hs_meeting_title",
    "hs_meeting_start_time",
    "hs_meeting_outcome",
    "hs_meeting_body",
]

EMAIL_PROPERTIES = [
    "hs_email_subject",
    "hs_timestamp",
    "hs_email_direction",
    "hs_email_from_email",
    "hs_email_to_email",
    "hs_email_text",
]

COMPANY_PROPERTIES = [
    "name",
    "domain",
    "industry",
    "numberofemployees",
    "annualrevenue",
    "hubspot_owner_id",
    "description",
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

    # ── Deal methods ─────────────────────────────────────────────────

    async def get_deal(self, deal_id: str, properties: list[str] | None = None) -> dict:
        props = properties or DEAL_PROPERTIES
        resp = await self._request_with_retry(
            "GET",
            f"/crm/v3/objects/deals/{deal_id}",
            params={"properties": ",".join(props)},
        )
        return resp.json()

    async def search_deals(self, name: str) -> list[dict]:
        return await self._search(
            "deals",
            [
                {
                    "propertyName": "dealname",
                    "operator": "CONTAINS_TOKEN",
                    "value": name,
                }
            ],
            properties=DEAL_PROPERTIES,
        )

    async def update_deal(self, deal_id: str, properties: dict) -> dict:
        resp = await self._request_with_retry(
            "PATCH",
            f"/crm/v3/objects/deals/{deal_id}",
            json={"properties": properties},
        )
        return resp.json()

    async def search_deals_by_company(self, company_id: str) -> list[dict]:
        body: dict = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "associations.company",
                            "operator": "EQ",
                            "value": company_id,
                        }
                    ]
                }
            ],
            "properties": ["dealname", "dealstage", "pipeline", "amount"],
            "limit": 20,
        }
        resp = await self._request_with_retry(
            "POST", "/crm/v3/objects/deals/search", json=body
        )
        return resp.json().get("results", [])

    # ── Company methods ───────────────────────────────────────────────

    async def get_company(
        self, company_id: str, properties: list[str] | None = None
    ) -> dict:
        props = properties or COMPANY_PROPERTIES
        resp = await self._request_with_retry(
            "GET",
            f"/crm/v3/objects/companies/{company_id}",
            params={"properties": ",".join(props)},
        )
        return resp.json()

    async def update_company(self, company_id: str, properties: dict) -> dict:
        resp = await self._request_with_retry(
            "PATCH",
            f"/crm/v3/objects/companies/{company_id}",
            json={"properties": properties},
        )
        return resp.json()

    # ── Association methods (v4 API) ──────────────────────────────────

    async def get_associations(
        self, object_type: str, object_id: str, to_type: str
    ) -> list[dict]:
        resp = await self._request_with_retry(
            "GET",
            f"/crm/v4/objects/{object_type}/{object_id}/associations/{to_type}",
        )
        return resp.json().get("results", [])

    async def associate_objects(
        self,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
    ) -> None:
        _ALLOWED_TYPES = {"contacts", "deals", "companies"}
        if from_type not in _ALLOWED_TYPES or to_type not in _ALLOWED_TYPES:
            raise ValueError(
                f"Write scope limited to {_ALLOWED_TYPES}. "
                f"Got from_type={from_type}, to_type={to_type}"
            )
        await self._request_with_retry(
            "PUT",
            f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}/{to_id}",
            json=[
                {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": _association_type_id(from_type, to_type),
                }
            ],
        )

    # ── Paginated search ─────────────────────────────────────────────

    async def search_paginated(
        self,
        object_type: str,
        filters: list[dict],
        properties: list[str] | None = None,
    ) -> list[dict]:
        """Paginated search returning all matching results (up to HubSpot's 10K cap)."""
        results: list[dict] = []
        after: str | None = None
        while True:
            body: dict = {
                "filterGroups": [{"filters": filters}],
                "limit": 100,
            }
            if properties:
                body["properties"] = properties
            if after:
                body["after"] = after
            resp = await self._request_with_retry(
                "POST", f"/crm/v3/objects/{object_type}/search", json=body
            )
            data = resp.json()
            results.extend(data.get("results", []))
            paging = data.get("paging", {}).get("next", {})
            after = paging.get("after")
            if not after:
                break
        return results

    # ── Batch methods ─────────────────────────────────────────────────

    async def batch_read(
        self, object_type: str, ids: list[str], properties: list[str]
    ) -> list[dict]:
        if not ids:
            return []
        results: list[dict] = []
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            body = {
                "inputs": [{"id": oid} for oid in chunk],
                "properties": properties,
            }
            resp = await self._request_with_retry(
                "POST",
                f"/crm/v3/objects/{object_type}/batch/read",
                json=body,
            )
            results.extend(resp.json().get("results", []))
        return results

    async def batch_get_associated_ids(
        self,
        from_type: str,
        to_type: str,
        ids: list[str],
    ) -> set[str]:
        """Batch read associations, returning the unique set of target object IDs."""
        if not ids:
            return set()
        target_ids: set[str] = set()
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            body = {"inputs": [{"id": oid} for oid in chunk]}
            resp = await self._request_with_retry(
                "POST",
                f"/crm/v4/associations/{from_type}/{to_type}/batch/read",
                json=body,
            )
            for result in resp.json().get("results", []):
                for assoc in result.get("to", []):
                    aid = assoc.get("toObjectId") or assoc.get("id")
                    if aid:
                        target_ids.add(str(aid))
        return target_ids

    # ── Owner methods ────────────────────────────────────────────────

    async def get_owners(self) -> list[dict]:
        resp = await self._request_with_retry("GET", "/crm/v3/owners")
        return resp.json().get("results", [])

    async def get_owner_emails(self) -> set[str]:
        owners = await self.get_owners()
        emails: set[str] = set()
        for owner in owners:
            email = owner.get("email", "")
            if email:
                emails.add(email.lower())
        return emails

    # ── Pipeline methods ──────────────────────────────────────────────

    async def get_pipelines(self) -> list[dict]:
        resp = await self._request_with_retry("GET", "/crm/v3/pipelines/deals")
        return resp.json().get("results", [])

    # ── Generic object fetch ──────────────────────────────────────────

    async def get_object(
        self, object_type: str, object_id: str, properties: list[str]
    ) -> dict:
        resp = await self._request_with_retry(
            "GET",
            f"/crm/v3/objects/{object_type}/{object_id}",
            params={"properties": ",".join(properties)},
        )
        return resp.json()

    # ── URL builders ──────────────────────────────────────────────────

    def build_contact_url(self, contact_id: str) -> str:
        return (
            f"https://app.hubspot.com/contacts/{self._portal_id}/contact/{contact_id}"
        )

    def build_deal_url(self, deal_id: str) -> str:
        return f"https://app.hubspot.com/contacts/{self._portal_id}/deal/{deal_id}"

    async def close(self) -> None:
        await self._client.aclose()


def _association_type_id(from_type: str, to_type: str) -> int:
    _MAP = {
        ("contacts", "deals"): 4,
        ("deals", "contacts"): 3,
        ("contacts", "companies"): 1,
        ("companies", "contacts"): 2,
        ("deals", "companies"): 341,
        ("companies", "deals"): 342,
    }
    key = (from_type, to_type)
    if key not in _MAP:
        raise ValueError(f"No association type mapping for {from_type} → {to_type}")
    return _MAP[key]
