"""Cloudflare for SaaS (Custom Hostnames) provider.

Why this and not cert-manager HTTP-01
-------------------------------------
FuzeInfra's prod ingress is tunnel-only: Traefik is pinned to ClusterIP so k3s
servicelb never binds :80/:443, and every request arrives through the Cloudflare
tunnel with TLS already terminated at the edge. An HTTP-01 (or TLS-ALPN) ACME
solver needs a publicly reachable origin on those ports, which would mean
un-pinning Traefik and punching a hole in exactly the invariant the tunnel
exists to hold. Cloudflare for SaaS issues and deploys the certificate at the
edge, where TLS is already being terminated, so the origin stays HTTP-only and
nothing about the tunnel model changes.

How traffic flows once active
-----------------------------
    browser --TLS(app.corpabc.com)--> Cloudflare edge
        (custom hostname cert; Host preserved)
      --> fallback origin (saas-origin.<zone>, a proxied CNAME to the tunnel)
      --> cloudflared --> traefik.kube-system:80
      --> the Ingress materialized by app.routing --> consumer Service
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from ..errors import quota_exceeded, upstream_error
from ..models import (
    Certificate,
    DnsStatus,
    Provider,
    ProviderName,
    TlsStatus,
    Verification,
    VerificationMethod,
    VerificationPurpose,
    VerificationRecord,
)
from .base import HostnameProvider, ProviderRecord

# Cloudflare custom-hostname `status` -> our DnsStatus.
# Anything unlisted falls back to `pending`, so a new upstream state never
# reads as a failure to a consumer UI.
_DNS_STATUS = {
    "pending": DnsStatus.pending,
    "pending_migration": DnsStatus.pending,
    "pending_provisioned": DnsStatus.pending,
    "pending_deletion": DnsStatus.pending,
    "provisioned": DnsStatus.active,
    "active": DnsStatus.active,
    "active_redeploying": DnsStatus.active,
    "moved": DnsStatus.moved,
    "blocked": DnsStatus.blocked,
    "pending_blocked": DnsStatus.blocked,
    "deleted": DnsStatus.error,
    "test_failed": DnsStatus.error,
}

# Cloudflare `ssl.status` -> our TlsStatus.
_TLS_STATUS = {
    "initializing": TlsStatus.pending_validation,
    "pending_validation": TlsStatus.pending_validation,
    "pending_issuance": TlsStatus.pending_issuance,
    "pending_deployment": TlsStatus.pending_deployment,
    "holding_deployment": TlsStatus.pending_deployment,
    "active": TlsStatus.active,
    "backup_issued": TlsStatus.active,
    "pending_expiration": TlsStatus.expired,
    "expired": TlsStatus.expired,
    "pending_deletion": TlsStatus.failed,
    "deactivating": TlsStatus.failed,
    "inactive": TlsStatus.failed,
    "deleted": TlsStatus.failed,
}


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class CloudflareProvider(HostnameProvider):
    name = ProviderName.cloudflare_for_saas.value

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.cloudflare_api_base,
            timeout=settings.request_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.cloudflare_api_token}",
                "Content-Type": "application/json",
            },
        )

    # -- HTTP plumbing -------------------------------------------------------

    @property
    def _zone_path(self) -> str:
        return f"/zones/{self._settings.cloudflare_zone_id}/custom_hostnames"

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise upstream_error("Cloudflare API is unreachable.", str(exc)) from exc

        # 404 is meaningful (unknown hostname) — hand it back rather than raising.
        if response.status_code == 404:
            return {"success": False, "errors": [{"code": 404, "message": "not found"}]}

        try:
            payload = response.json()
        except ValueError as exc:
            raise upstream_error(
                "Cloudflare returned a non-JSON response.",
                f"HTTP {response.status_code}",
            ) from exc

        if not payload.get("success", False):
            errors = payload.get("errors") or []
            detail = "; ".join(
                f"{e.get('code', '?')}: {e.get('message', '')}" for e in errors
            ) or f"HTTP {response.status_code}"
            # 1414 = "custom hostname quota reached" on the account's plan.
            if any(e.get("code") == 1414 for e in errors):
                raise quota_exceeded("Cloudflare custom hostname quota reached.", detail)
            raise upstream_error("Cloudflare rejected the request.", detail)

        return payload

    # -- mapping -------------------------------------------------------------

    def _to_record(self, result: dict[str, Any]) -> ProviderRecord:
        ssl = result.get("ssl") or {}
        raw_status = str(result.get("status") or "pending")
        raw_ssl_status = str(ssl.get("status") or "initializing")

        dns_status = _DNS_STATUS.get(raw_status, DnsStatus.pending)
        tls_status = _TLS_STATUS.get(raw_ssl_status, TlsStatus.pending_validation)

        # Validation errors are terminal until the customer fixes DNS. Surface
        # them as `failed` so a UI can stop spinning and show the reason.
        errors: list[str] = []
        for source in (ssl.get("validation_errors") or [], result.get("verification_errors") or []):
            for item in source:
                message = item.get("message") if isinstance(item, dict) else str(item)
                if message:
                    errors.append(str(message))
        if errors and tls_status not in {TlsStatus.active, TlsStatus.expired}:
            tls_status = TlsStatus.failed

        records: list[VerificationRecord] = []

        # (1) Ownership: proves the customer controls the domain.
        ownership = result.get("ownership_verification") or {}
        if ownership.get("name") and ownership.get("value"):
            records.append(
                VerificationRecord(
                    method=VerificationMethod.txt,
                    record=str(ownership["name"]),
                    value=str(ownership["value"]),
                    purpose=VerificationPurpose.ownership,
                )
            )

        # (2) Certificate DCV: satisfies the CA's domain control challenge.
        for entry in ssl.get("validation_records") or []:
            if entry.get("txt_name") and entry.get("txt_value"):
                records.append(
                    VerificationRecord(
                        method=VerificationMethod.txt,
                        record=str(entry["txt_name"]),
                        value=str(entry["txt_value"]),
                        purpose=VerificationPurpose.certificate,
                    )
                )

        # (3) Routing: the record that actually sends traffic to the platform.
        records.append(
            VerificationRecord(
                method=VerificationMethod.cname,
                record=result.get("hostname") or "",
                value=self._settings.cname_target,
                purpose=VerificationPurpose.routing,
            )
        )

        primary = records[0]
        verification = Verification(
            method=primary.method,
            record=primary.record,
            value=primary.value,
            records=records,
        )

        certificate = None
        certs = ssl.get("certificates") or []
        if certs:
            cert = certs[0]
            certificate = Certificate(
                issuer=cert.get("issuer"),
                not_before=_parse_ts(cert.get("not_before")),
                not_after=_parse_ts(cert.get("not_after")),
                serial_number=cert.get("serial_number"),
            )

        return ProviderRecord(
            domain=str(result.get("hostname") or ""),
            dns_status=dns_status,
            tls_status=tls_status,
            verification=verification,
            provider=Provider(
                name=ProviderName.cloudflare_for_saas,
                id=result.get("id"),
                status=raw_status,
                ssl_status=raw_ssl_status,
            ),
            certificate=certificate,
            error="; ".join(errors) or None,
            created_at=_parse_ts(result.get("created_at")) or datetime.now(timezone.utc),
            updated_at=_parse_ts(ssl.get("uploaded_on")) or _parse_ts(result.get("created_at")),
        )

    # -- provider API --------------------------------------------------------

    async def _find(self, domain: str) -> dict[str, Any] | None:
        payload = await self._request("GET", self._zone_path, params={"hostname": domain})
        for result in payload.get("result") or []:
            if str(result.get("hostname", "")).lower() == domain:
                return result
        return None

    async def create(self, domain: str) -> tuple[ProviderRecord, bool]:
        existing = await self._find(domain)
        if existing is not None:
            return self._to_record(existing), False

        if self._settings.max_custom_hostnames:
            current = await self.list()
            if len(current) >= self._settings.max_custom_hostnames:
                raise quota_exceeded(
                    "Custom hostname cap reached for this platform.",
                    f"{len(current)}/{self._settings.max_custom_hostnames} in use; "
                    "raise customHostnameApi.maxCustomHostnames after confirming "
                    "the Cloudflare billing impact.",
                )

        body = {
            "hostname": domain,
            "ssl": {
                # TXT DCV works before the customer cuts DNS over to us, so the
                # UI can show a deterministic, pollable record set up front —
                # HTTP DCV would require traffic to already be arriving here.
                "method": "txt",
                "type": "dv",
                "wildcard": False,
                "bundle_method": "ubiquitous",
                "settings": {"min_tls_version": "1.2"},
            },
        }
        payload = await self._request("POST", self._zone_path, json=body)
        return self._to_record(payload.get("result") or {}), True

    async def get(self, domain: str) -> ProviderRecord | None:
        result = await self._find(domain)
        return self._to_record(result) if result else None

    async def delete(self, domain: str) -> None:
        result = await self._find(domain)
        if result is None:
            return
        await self._request("DELETE", f"{self._zone_path}/{result['id']}")

    async def list(self) -> list[ProviderRecord]:
        records: list[ProviderRecord] = []
        page = 1
        while True:
            payload = await self._request(
                "GET", self._zone_path, params={"page": page, "per_page": 50}
            )
            results = payload.get("result") or []
            records.extend(self._to_record(r) for r in results)
            info = payload.get("result_info") or {}
            if page >= int(info.get("total_pages") or 1) or not results:
                break
            page += 1
        return records

    async def health(self) -> tuple[bool, str | None]:
        try:
            await self._request("GET", self._zone_path, params={"per_page": 1})
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            return False, str(getattr(exc, "detail", exc))
        return True, None

    async def aclose(self) -> None:
        await self._client.aclose()
