from __future__ import annotations

import uuid

import httpx


class ClayClient:
    def __init__(self, webhook_url: str = "", webhook_token: str = ""):
        self._webhook_url = webhook_url
        self._webhook_token = webhook_token
        self._client = httpx.AsyncClient(timeout=30.0)
        self._callback_results: dict[str, dict] = {}
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
            self._pending_lookups.pop(lookup_key, None)
            return {
                "status": "webhook_error",
                "message": "Failed to POST to Clay webhook",
            }

        return {
            "status": "submitted",
            "correlationId": correlation_id,
        }

    def receive_callback(self, data: dict) -> bool:
        correlation_id = data.get("correlationId") or data.get("correlation_id")
        if correlation_id:
            self._callback_results[correlation_id] = data
            for key, cid in list(self._pending_lookups.items()):
                if cid == correlation_id:
                    self._pending_lookups.pop(key, None)
            return True

        first = (data.get("firstName") or data.get("first_name") or "").lower()
        last = (data.get("lastName") or data.get("last_name") or "").lower()
        domain = (data.get("companyDomain") or data.get("company_domain") or "").lower()
        if first and last and domain:
            lookup_key = f"{first}|{last}|{domain}"
            matched_id = self._pending_lookups.pop(lookup_key, None)
            if matched_id:
                self._callback_results[matched_id] = data
                return True

        return False

    def get_result(self, correlation_id: str) -> dict | None:
        return self._callback_results.pop(correlation_id, None)

    async def close(self) -> None:
        await self._client.aclose()
