"""In-memory/file-backed stub provider for kind and laptop development.

There is no Cloudflare account behind a kind cluster, but EPIC-16 still has to
be developable locally. This provider returns the same shapes as the Cloudflare
one and walks the same state machine on a timer, so a consumer's polling UI,
retry logic, and status rendering can all be exercised end to end offline.

Deliberately NOT production-grade storage: a JSON file, last-write-wins. It
exists to make local development honest, not to survive a restart storm.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
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


class StubProvider(HostnameProvider):
    name = ProviderName.stub.value

    def __init__(self, settings: Settings):
        self._settings = settings
        self._path = Path(settings.stub_state_path)
        self._lock = asyncio.Lock()

    # -- persistence ---------------------------------------------------------

    def _read(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, state: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a crash mid-write cannot leave a truncated file that
        # silently reads back as "no domains provisioned".
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    # -- state machine -------------------------------------------------------

    def _to_record(self, entry: dict) -> ProviderRecord:
        """Advance the simulated lifecycle based on elapsed time, then map it.

        pending_validation -> pending_issuance -> pending_deployment -> active
        across `stub_activate_after_seconds`, so a UI sees real transitions
        rather than an instant flip.
        """
        created = datetime.fromisoformat(entry["created_at"])
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        window = max(self._settings.stub_activate_after_seconds, 0.001)

        if entry.get("forced_status"):
            tls_status = TlsStatus(entry["forced_status"])
        elif elapsed >= window:
            tls_status = TlsStatus.active
        elif elapsed >= window * 0.66:
            tls_status = TlsStatus.pending_deployment
        elif elapsed >= window * 0.33:
            tls_status = TlsStatus.pending_issuance
        else:
            tls_status = TlsStatus.pending_validation

        dns_status = DnsStatus.active if tls_status is TlsStatus.active else DnsStatus.pending

        domain = entry["domain"]
        records = [
            VerificationRecord(
                method=VerificationMethod.txt,
                record=f"_cf-custom-hostname.{domain}",
                value=entry["ownership_token"],
                purpose=VerificationPurpose.ownership,
            ),
            VerificationRecord(
                method=VerificationMethod.txt,
                record=f"_acme-challenge.{domain}",
                value=entry["dcv_token"],
                purpose=VerificationPurpose.certificate,
            ),
            VerificationRecord(
                method=VerificationMethod.cname,
                record=domain,
                value=self._settings.cname_target,
                purpose=VerificationPurpose.routing,
            ),
        ]

        certificate = None
        if tls_status is TlsStatus.active:
            certificate = Certificate(
                issuer="CN=FuzeInfra Local Stub CA,O=FuzeInfra",
                not_before=created,
                not_after=created + timedelta(days=90),
                serial_number=entry["ownership_token"].replace("-", "")[:16],
            )

        return ProviderRecord(
            domain=domain,
            dns_status=dns_status,
            tls_status=tls_status,
            verification=Verification(
                method=records[0].method,
                record=records[0].record,
                value=records[0].value,
                records=records,
            ),
            provider=Provider(
                name=ProviderName.stub,
                id=entry["id"],
                status="active" if dns_status is DnsStatus.active else "pending",
                ssl_status=tls_status.value,
            ),
            certificate=certificate,
            created_at=created,
            updated_at=datetime.now(timezone.utc),
        )

    # -- provider API --------------------------------------------------------

    async def create(self, domain: str) -> tuple[ProviderRecord, bool]:
        async with self._lock:
            state = self._read()
            if domain in state:
                return self._to_record(state[domain]), False
            state[domain] = {
                "id": str(uuid.uuid4()),
                "domain": domain,
                "ownership_token": str(uuid.uuid4()),
                "dcv_token": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(state)
            return self._to_record(state[domain]), True

    async def get(self, domain: str) -> ProviderRecord | None:
        entry = self._read().get(domain)
        return self._to_record(entry) if entry else None

    async def delete(self, domain: str) -> None:
        async with self._lock:
            state = self._read()
            if state.pop(domain, None) is not None:
                self._write(state)

    async def list(self) -> list[ProviderRecord]:
        return [self._to_record(entry) for entry in self._read().values()]

    async def health(self) -> tuple[bool, str | None]:
        return True, "stub provider — no upstream certificate authority"
