from __future__ import annotations

import asyncio
import uuid

import httpx

CALLBACK_TIMEOUT = 120.0


class ClayClient:
    def __init__(self, webhook_url: str = "", webhook_token: str = ""):
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._client = httpx.AsyncClient(timeout=30.0)
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, dict] = {}

    @property
    def configured(self) -> bool:
        return bool(self._webhook_url)

    async def enrich_contact(
        self,
        first_name: str,
        last_name: str,
        company_domain: str,
        requested_data: list[str] | None = None,
        linkedin_url: str | None = None,
    ) -> dict:
        if not self._webhook_url:
            return {
                "status": "not_configured",
                "message": "Clay webhook URL not configured. Set CLAY_WEBHOOK_URL env var.",
            }

        correlation_id = str(uuid.uuid4())
        event = asyncio.Event()
        self._pending[correlation_id] = event

        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "companyDomain": company_domain,
            "requestedData": requested_data or ["phone", "email"],
            "correlationId": correlation_id,
        }
        if linkedin_url:
            payload["linkedinUrl"] = linkedin_url
        headers: dict[str, str] = {}
        if self._webhook_token:
            headers["x-clay-webhook-auth"] = self._webhook_token

        try:
            resp = await self._client.post(
                self._webhook_url, json=payload, headers=headers
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            self._pending.pop(correlation_id, None)
            return {
                "status": "webhook_error",
                "message": "Failed to POST to Clay webhook",
            }

        try:
            await asyncio.wait_for(event.wait(), timeout=CALLBACK_TIMEOUT)
            result = self._results.pop(correlation_id, {"status": "unknown"})
        except asyncio.TimeoutError:
            result = {"status": "timeout", "correlationId": correlation_id}
        finally:
            self._pending.pop(correlation_id, None)
            self._results.pop(correlation_id, None)

        return result

    def receive_callback(self, data: dict) -> bool:
        correlation_id = data.get("correlationId")
        if not correlation_id or correlation_id not in self._pending:
            return False
        self._results[correlation_id] = data
        self._pending[correlation_id].set()
        return True

    async def close(self) -> None:
        await self._client.aclose()
