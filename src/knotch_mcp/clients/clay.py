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
            path,
            json=body,
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
