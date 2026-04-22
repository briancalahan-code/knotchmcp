"""Tests for Pydantic models — tool inputs and outputs."""

import pytest
from pydantic import ValidationError

from knotch_mcp.models import (
    AddToHubSpotInput,
    AddToHubSpotResult,
    ClayEnrichInput,
    ClayEnrichResult,
    ContactResult,
    EnrichContactInput,
    EnrichContactResult,
    FindContactInput,
    FindContactsByRoleInput,
    FindContactsResult,
    FindPhoneInput,
    FindPhoneResult,
)


# ── ContactResult ──────────────────────────────────────────────────


class TestContactResult:
    def test_contact_result_defaults(self):
        c = ContactResult(name="Jane Doe")
        assert c.name == "Jane Doe"
        assert c.title is None
        assert c.company is None
        assert c.email is None
        assert c.email_status is None
        assert c.linkedin_url is None
        assert c.location is None
        assert c.apollo_id is None
        assert c.phone is None
        assert c.phone_type is None
        assert c.sources == []
        assert c.hubspot_status == "not_checked"
        assert c.hubspot_contact_id is None
        assert c.hubspot_url is None
        assert c.gaps == []
        assert c.suggested_actions == []

    def test_contact_result_with_gaps(self):
        c = ContactResult(
            name="Jane Doe",
            email="jane@example.com",
            gaps=["phone", "title"],
            suggested_actions=["find_phone", "enrich_contact"],
        )
        assert c.email == "jane@example.com"
        assert c.gaps == ["phone", "title"]
        assert c.suggested_actions == ["find_phone", "enrich_contact"]


# ── FindContactInput ───────────────────────────────────────────────


class TestFindContactInput:
    def test_find_contact_input_required_fields(self):
        inp = FindContactInput(
            first_name="Jane",
            last_name="Doe",
            company="Acme Corp",
        )
        assert inp.first_name == "Jane"
        assert inp.last_name == "Doe"
        assert inp.company == "Acme Corp"
        assert inp.email is None
        assert inp.linkedin_url is None

    def test_find_contact_input_missing_required_raises(self):
        with pytest.raises(ValidationError):
            FindContactInput(first_name="Jane", last_name="Doe")  # missing company


# ── FindContactsByRoleInput ────────────────────────────────────────


class TestFindContactsByRoleInput:
    def test_find_contacts_by_role_defaults(self):
        inp = FindContactsByRoleInput(
            titles=["VP Marketing", "CMO"],
            company="Acme Corp",
        )
        assert inp.titles == ["VP Marketing", "CMO"]
        assert inp.company == "Acme Corp"
        assert inp.seniority is None
        assert inp.limit == 3


# ── FindPhoneInput ─────────────────────────────────────────────────


class TestFindPhoneInput:
    def test_find_phone_input_accepts_any_identifier(self):
        by_apollo = FindPhoneInput(apollo_id="abc123")
        assert by_apollo.apollo_id == "abc123"
        assert by_apollo.email is None

        by_email = FindPhoneInput(email="jane@example.com")
        assert by_email.email == "jane@example.com"

        by_linkedin = FindPhoneInput(linkedin_url="https://linkedin.com/in/jane")
        assert by_linkedin.linkedin_url == "https://linkedin.com/in/jane"

        by_name = FindPhoneInput(name="Jane Doe")
        assert by_name.name == "Jane Doe"


# ── AddToHubSpotInput ─────────────────────────────────────────────


class TestAddToHubSpotInput:
    def test_add_to_hubspot_input(self):
        inp = AddToHubSpotInput(
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            title="VP Marketing",
            company="Acme Corp",
            company_domain="acme.com",
            linkedin_url="https://linkedin.com/in/jane",
            location="New York, NY",
            phone="+15551234567",
            apollo_id="abc123",
        )
        assert inp.first_name == "Jane"
        assert inp.last_name == "Doe"
        assert inp.email == "jane@example.com"
        assert inp.title == "VP Marketing"
        assert inp.company == "Acme Corp"
        assert inp.company_domain == "acme.com"

    def test_add_to_hubspot_input_minimal(self):
        inp = AddToHubSpotInput(first_name="Jane", last_name="Doe")
        assert inp.email is None
        assert inp.title is None
        assert inp.company is None
        assert inp.company_domain is None
        assert inp.linkedin_url is None
        assert inp.location is None
        assert inp.phone is None
        assert inp.apollo_id is None


# ── ClayEnrichInput ────────────────────────────────────────────────


class TestClayEnrichInput:
    def test_clay_enrich_input_defaults(self):
        inp = ClayEnrichInput(
            first_name="Jane",
            last_name="Doe",
            company_domain="acme.com",
        )
        assert inp.first_name == "Jane"
        assert inp.last_name == "Doe"
        assert inp.company_domain == "acme.com"
        assert inp.requested_data == ["phone", "email"]


# ── FindContactsResult ─────────────────────────────────────────────


class TestFindContactsResult:
    def test_find_contacts_result(self):
        c1 = ContactResult(name="Jane Doe", email="jane@example.com")
        c2 = ContactResult(name="John Smith", email="john@example.com")
        result = FindContactsResult(candidates=[c1, c2], total_available=10)
        assert len(result.candidates) == 2
        assert result.total_available == 10
        assert result.candidates[0].name == "Jane Doe"


# ── FindPhoneResult ────────────────────────────────────────────────


class TestFindPhoneResult:
    def test_find_phone_result_found(self):
        result = FindPhoneResult(
            phone="+15551234567",
            phone_type="mobile",
            found=True,
            confidence="high",
        )
        assert result.phone == "+15551234567"
        assert result.phone_type == "mobile"
        assert result.source == "apollo"
        assert result.confidence == "high"
        assert result.found is True
        assert result.suggested_action is None

    def test_find_phone_result_not_found(self):
        result = FindPhoneResult(
            found=False,
            suggested_action="Try Clay enrichment for phone data",
        )
        assert result.phone is None
        assert result.phone_type is None
        assert result.found is False
        assert result.suggested_action == "Try Clay enrichment for phone data"


# ── EnrichContactResult ────────────────────────────────────────────


class TestEnrichContactResult:
    def test_enrich_contact_result(self):
        result = EnrichContactResult(
            filled={"phone": "+15551234567", "title": "VP Marketing"},
            already_populated=["email", "name"],
            not_found=["personal_email"],
            sources_used=["apollo", "clay"],
        )
        assert result.filled == {"phone": "+15551234567", "title": "VP Marketing"}
        assert result.already_populated == ["email", "name"]
        assert result.not_found == ["personal_email"]
        assert result.sources_used == ["apollo", "clay"]

    def test_enrich_contact_result_defaults(self):
        result = EnrichContactResult()
        assert result.filled == {}
        assert result.already_populated == []
        assert result.not_found == []
        assert result.sources_used == []


# ── AddToHubSpotResult ─────────────────────────────────────────────


class TestAddToHubSpotResult:
    def test_add_to_hubspot_result(self):
        result = AddToHubSpotResult(
            hubspot_contact_id="123",
            hubspot_url="https://app.hubspot.com/contacts/12345/contact/123",
            action="created",
            company_associated=True,
            company_name="Acme Corp",
        )
        assert result.hubspot_contact_id == "123"
        assert (
            result.hubspot_url == "https://app.hubspot.com/contacts/12345/contact/123"
        )
        assert result.action == "created"
        assert result.company_associated is True
        assert result.company_name == "Acme Corp"

    def test_add_to_hubspot_result_minimal(self):
        result = AddToHubSpotResult(
            hubspot_contact_id="123",
            hubspot_url="https://app.hubspot.com/contacts/12345/contact/123",
            action="updated",
        )
        assert result.company_associated is False
        assert result.company_name is None


# ── ClayEnrichResult ───────────────────────────────────────────────


class TestClayEnrichResult:
    def test_clay_enrich_result(self):
        result = ClayEnrichResult(
            enriched_fields={"phone": "+15551234567", "email": "jane@acme.com"},
            source="clay",
            credits_used=2,
            task_status="completed",
        )
        assert result.enriched_fields == {
            "phone": "+15551234567",
            "email": "jane@acme.com",
        }
        assert result.source == "clay"
        assert result.credits_used == 2
        assert result.task_status == "completed"

    def test_clay_enrich_result_defaults(self):
        result = ClayEnrichResult()
        assert result.enriched_fields == {}
        assert result.source == "clay"
        assert result.credits_used == 0
        assert result.task_status == "completed"
