"""Provider interface for custom-hostname edge provisioning.

A provider owns the DNS-validation + certificate half of the lifecycle. The
in-cluster routing half (materializing an Ingress so Traefik host-routes the
domain) is provider-independent and lives in ``app.routing``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

from ..models import (
    Certificate,
    DnsStatus,
    Provider,
    TlsStatus,
    Verification,
)


@dataclass
class ProviderRecord:
    """What a provider knows about one custom hostname.

    Deliberately narrower than the API's `CustomHostname`: the provider has no
    notion of route profiles or Ingress readiness, and must not grow one.
    """

    domain: str
    dns_status: DnsStatus
    tls_status: TlsStatus
    verification: Verification
    provider: Provider
    certificate: Certificate | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class HostnameProvider(abc.ABC):
    """Edge provisioning backend (Cloudflare for SaaS, or the local stub)."""

    name: str

    @abc.abstractmethod
    async def create(self, domain: str) -> tuple[ProviderRecord, bool]:
        """Register `domain`.

        Returns `(record, created)` where `created` is False when the domain was
        already registered — the caller turns that into a 200 rather than a 201
        so a consumer's reconcile loop is safe to re-run.
        """

    @abc.abstractmethod
    async def get(self, domain: str) -> ProviderRecord | None:
        """Current state, or None if the domain is not registered."""

    @abc.abstractmethod
    async def delete(self, domain: str) -> None:
        """Deprovision. Must be idempotent."""

    @abc.abstractmethod
    async def list(self) -> list[ProviderRecord]:
        """Every hostname registered with this provider."""

    @abc.abstractmethod
    async def health(self) -> tuple[bool, str | None]:
        """`(ready, detail)` — whether the backend is reachable."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
