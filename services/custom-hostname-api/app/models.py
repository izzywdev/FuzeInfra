"""Pydantic models mirroring the frozen contract in ``openapi.yaml``.

``tests/test_contract.py`` diffs the schema FastAPI generates from these models
against ``openapi.yaml``, so any drift between code and contract fails CI.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A conservative FQDN: 2+ labels, no leading/trailing hyphen, no wildcard.
DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class DnsStatus(str, Enum):
    pending = "pending"
    active = "active"
    moved = "moved"
    blocked = "blocked"
    error = "error"


class TlsStatus(str, Enum):
    pending_validation = "pending_validation"
    pending_issuance = "pending_issuance"
    pending_deployment = "pending_deployment"
    active = "active"
    expired = "expired"
    failed = "failed"


class VerificationMethod(str, Enum):
    txt = "txt"
    cname = "cname"


class VerificationPurpose(str, Enum):
    ownership = "ownership"
    certificate = "certificate"
    routing = "routing"


class ProviderName(str, Enum):
    cloudflare_for_saas = "cloudflare_for_saas"
    stub = "stub"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCustomHostnameRequest(_Strict):
    domain: str = Field(min_length=4, max_length=253, examples=["app.corpabc.com"])
    profile: str | None = Field(default=None, examples=["fuzefront"])

    @field_validator("domain")
    @classmethod
    def _normalize(cls, value: str) -> str:
        # Normalize before validating so "APP.CorpABC.com." and "app.corpabc.com"
        # can never become two separate records for the same domain.
        candidate = value.strip().rstrip(".").lower()
        if not DOMAIN_RE.match(candidate):
            raise ValueError(
                "domain must be a fully-qualified hostname with at least two "
                "labels and no wildcard"
            )
        return candidate


class VerificationRecord(_Strict):
    method: VerificationMethod
    record: str
    value: str
    purpose: VerificationPurpose | None = None


class Verification(_Strict):
    method: VerificationMethod
    record: str
    value: str
    records: list[VerificationRecord] = Field(default_factory=list)


class Routing(_Strict):
    cname_target: str
    ingress_ready: bool
    ingress_name: str | None = None


class Certificate(_Strict):
    issuer: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    serial_number: str | None = None


class Provider(BaseModel):
    # Deliberately permissive: this is a debugging passthrough and consumers are
    # told not to branch on it, so new upstream fields must not break clients.
    model_config = ConfigDict(extra="allow")

    name: ProviderName
    id: str | None = None
    status: str | None = None
    ssl_status: str | None = None


class CustomHostname(_Strict):
    domain: str
    profile: str
    active: bool
    dns_status: DnsStatus
    tls_status: TlsStatus
    verification: Verification
    routing: Routing | None = None
    certificate: Certificate | None = None
    error: str | None = None
    provider: Provider | None = None
    created_at: datetime
    updated_at: datetime | None = None


class CustomHostnameList(_Strict):
    items: list[CustomHostname]
    next_cursor: str | None = None


class Health(_Strict):
    status: str
    provider: ProviderName | None = None
    detail: str | None = None


class Error(_Strict):
    error: str
    message: str
    detail: str | None = None
