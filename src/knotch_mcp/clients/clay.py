from __future__ import annotations

import asyncio
import uuid

import httpx

CALLBACK_TIMEOUT = 50.0
POLL_INTERVAL = 2.0


class ClayClient:
    def __init__(self, webhook_url: str = "", webhook_token: str = ""):
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._client = httpx.AsyncClient(timeout=30.0)
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, dict] = {}
        self._pending_lookups: dict[str, str] = {}

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
        lookup_key = (
            f"{first_name.lower()}|{last_name.lower()}|{company_domain.lower()}"
        )
        event = asyncio.Event()
        self._pending[correlation_id] = event
        self._pending_lookups[lookup_key] = correlation_id

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
            self._pending_lookups.pop(lookup_key, None)
            return {
                "status": "webhook_error",
                "message": "Failed to POST to Clay webhook",
            }

        try:
            await asyncio.wait_for(event.wait(), timeout=CALLBACK_TIMEOUT)
            result = self._results.pop(correlation_id, {"status": "unknown"})
        except asyncio.TimeoutError:
            result = {
                "status": "timeout",
                "message": (
                    f"Clay enrichment triggered for {first_name} {last_name} at "
                    f"{company_domain} but callback not received within "
                    f"{int(CALLBACK_TIMEOUT)}s. Check Clay table for results."
                ),
                "correlationId": correlation_id,
            }
        finally:
            self._pending.pop(correlation_id, None)
            self._pending_lookups.pop(lookup_key, None)
            self._results.pop(correlation_id, None)

        return result

    def receive_callback(self, data: dict) -> bool:
        # Primary match: correlationId
        correlation_id = data.get("correlationId") or data.get("correlation_id")
        if correlation_id and correlation_id in self._pending:
            self._results[correlation_id] = data
            self._pending[correlation_id].set()
            return True

        # Fallback match: name + domain (Clay may not return the correlationId)
        first = (data.get("firstName") or data.get("first_name") or "").lower()
        last = (data.get("lastName") or data.get("last_name") or "").lower()
        domain = (data.get("companyDomain") or data.get("company_domain") or "").lower()
        if first and last and domain:
            lookup_key = f"{first}|{last}|{domain}"
            matched_id = self._pending_lookups.get(lookup_key)
            if matched_id and matched_id in self._pending:
                self._results[matched_id] = data
                self._pending[matched_id].set()
                return True

        return False

    async def close(self) -> None:
        await self._client.aclose()
