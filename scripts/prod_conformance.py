#!/usr/bin/env python3
"""Measure what production actually serves, per product, and print a table.

WHY THIS EXISTS
Ten production properties get asked about routinely — is the backend healthy, is swagger
reachable, does the frontend actually serve a loadable Module-Federation remote, is the
product registered and enabled in the portal, is it exposed publicly when it should not
be — and until now nothing measured any of them. The answer was a human counting menu
entries, and a repo that BUILDS a thing was repeatedly mistaken for a cluster that RUNS it.

WHY IT RUNS IN-CLUSTER
`runs-on: staging` is an ARC runner inside this cluster, so it can reach every ClusterIP
Service by its DNS name. That matters more than convenience: it means no product needs a
public hostname for this to work, and the probe therefore does not create a reason to keep
one. Probing from outside would have required exactly the per-product public ingress that
governance/ingress policy is trying to retire.

WHAT IT DELIBERATELY DOES NOT DO
It never reports UNKNOWN as a pass. Every cell is YES, NO, NA or UNVERIFIED-with-a-reason,
and `--strict` exits non-zero when anything a manifest claims should exist does not answer.
A probe that goes green when it could not measure is the failure mode this replaces.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 6

# Paths tried in order. First 2xx wins; the path that answered is reported, because
# "swagger is up" and "swagger is up at a path nobody documented" are different facts.
HEALTH_PATHS = ("/health", "/healthz", "/api/health", "/api/v1/health", "/_health")
SWAGGER_PATHS = ("/api-docs", "/api-docs/", "/swagger.json", "/openapi.json",
                 "/docs", "/api/docs", "/swagger/v1/swagger.json")
REMOTE_PATHS = ("/assets/remoteEntry.js", "/remoteEntry.js", "/static/remoteEntry.js")

# A Module-Federation container exposes these at module scope. Serving 200 on
# remoteEntry.js is not enough — an SPA with a catch-all route happily returns index.html
# for any path, which is exactly how a remote that cannot be mounted still looks fine.
MF_MARKERS = ("get:", "init:", "__federation", "moduleMap", "shareScope")


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def kget(*args: str) -> dict:
    return json.loads(sh("kubectl", *args, "-o", "json"))


# urlopen() honours every scheme urllib knows, including file:// and ftp://. Both
# URLs this script opens are built from values it does not control — one from
# kubectl-discovered Service names, one from the --portal-base argument — so
# "https://..." is a convention here, not a guarantee. A --portal-base of
# file:///etc/shadow would be read and its first 4096 bytes printed into a CI log.
#
# Pin the scheme at the one place that opens a socket, so no call site can opt out
# by construction. Flagged by Semgrep as
# python.lang.security.audit.dynamic-urllib-use-detected on both call sites; this
# is the fix, not a nosem.
_ALLOWED_SCHEMES = ("http", "https")


def _require_http(url: str) -> str:
    """Return url if it is http(s); raise ValueError otherwise."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"refusing to open {scheme or '<no>'}:// URL — only "
            f"{'/'.join(_ALLOWED_SCHEMES)} are allowed: {url!r}"
        )
    return url


def probe(url: str) -> tuple[int | None, str, str]:
    """(status, body_prefix, error). Never raises — an unreachable service is data."""
    try:
        _require_http(url)
    except ValueError as e:
        return None, "", str(e)
    req = urllib.request.Request(url, headers={"User-Agent": "prod-conformance/1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(4096).decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:                                    # noqa: BLE001 - see docstring
        return None, "", type(e).__name__


def first_ok(base: str, paths) -> tuple[str, str]:
    """(path_that_answered, body) or ('', '')."""
    for p in paths:
        status, body, _ = probe(base + p)
        if status and 200 <= status < 300:
            return p, body
    return "", ""


def namespaces() -> list[str]:
    return sorted(
        n["metadata"]["name"]
        for n in kget("get", "ns")["items"]
        if n["metadata"]["name"].startswith("fuze")
        and n["metadata"]["name"] not in ("fuzeinfra",)   # platform, not a product
    )


def services(ns: str) -> list[dict]:
    return kget("get", "svc", "-n", ns)["items"]


def public_hosts(ns: str) -> list[str]:
    out = []
    for ing in kget("get", "ingress", "-n", ns)["items"]:
        for rule in ing["spec"].get("rules", []):
            if rule.get("host"):
                out.append(rule["host"])
    return sorted(set(out))


def ready_pods(ns: str) -> dict[str, bool]:
    """Deployment-name prefix -> at least one Ready pod. Ready, not Running: a pod that
    is Running with a failing readiness probe serves nothing."""
    out: dict[str, bool] = {}
    for p in kget("get", "pods", "-n", ns)["items"]:
        name = p["metadata"]["name"]
        ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                    for c in (p.get("status", {}).get("conditions") or []))
        out[name] = ready
    return out


def classify(svc: dict) -> str:
    n = svc["metadata"]["name"]
    if "mcp" in n:
        return "mcp"
    if "a2a" in n:
        return "a2a"
    if any(k in n for k in ("frontend", "-ui", "ui", "picker", "portal")):
        return "frontend"
    if any(k in n for k in ("postgres", "redis", "rabbitmq", "mongo", "kafka")):
        return "datastore"
    return "backend"


def http_port(svc: dict) -> int | None:
    for p in svc["spec"].get("ports", []):
        if p.get("protocol", "TCP") != "TCP":
            continue
        return p["port"]
    return None


def measure(ns: str) -> dict:
    row = {"namespace": ns, "backend": "NO", "backendDetail": "", "swagger": "NO",
           "swaggerDetail": "", "remote": "NO", "remoteDetail": "",
           "mcp": "NO", "a2a": "NO", "public": [], "services": []}
    row["public"] = public_hosts(ns)
    pods = ready_pods(ns)
    for svc in services(ns):
        name = svc["metadata"]["name"]
        kind = classify(svc)
        port = http_port(svc)
        if kind == "datastore" or port is None:
            continue
        base = f"http://{name}.{ns}.svc.cluster.local:{port}"
        has_ready = any(v for k, v in pods.items() if k.startswith(name))
        entry = {"service": name, "kind": kind, "port": port, "readyPod": has_ready}

        if kind in ("backend", "frontend"):
            hp, _ = first_ok(base, HEALTH_PATHS)
            entry["health"] = hp or ""
            if hp and kind == "backend":
                row["backend"], row["backendDetail"] = "YES", f"{name}{hp}"
            sp, _ = first_ok(base, SWAGGER_PATHS)
            entry["swagger"] = sp or ""
            if sp:
                row["swagger"], row["swaggerDetail"] = "YES", f"{name}{sp}"

        if kind == "frontend":
            rp, body = first_ok(base, REMOTE_PATHS)
            if rp and any(m in body for m in MF_MARKERS):
                row["remote"], row["remoteDetail"] = "YES", f"{name}{rp}"
                entry["remote"] = rp
            elif rp:
                # Served something at the remote path that is not a federation container.
                # Almost always an SPA catch-all returning index.html.
                row["remote"] = "NO"
                row["remoteDetail"] = f"{name}{rp} served non-MF content"
                entry["remote"] = f"{rp} (not MF)"

        if kind == "mcp":
            hp, _ = first_ok(base, HEALTH_PATHS)
            row["mcp"] = "YES" if (hp and has_ready) else "NO"
            entry["health"] = hp or ""
        if kind == "a2a":
            hp, _ = first_ok(base, HEALTH_PATHS)
            row["a2a"] = "YES" if (hp and has_ready) else "NO"
            entry["health"] = hp or ""

        row["services"].append(entry)
    return row


def portal(base: str, token: str) -> tuple[list[dict], str]:
    """(apps, reason_unverified). Every portal read path is authenticated, so with no
    token this returns UNVERIFIED rather than an empty list — 'no apps' and 'could not
    ask' must never render the same."""
    if not token:
        return [], ("PORTAL_READ_TOKEN is unset, so registration/activation/nav could not "
                    "be read. This is UNVERIFIED, not zero.")
    try:
        url = _require_http(base + "/api/apps")
    except ValueError as e:
        return [], f"portal query refused: {e}"
    req = urllib.request.Request(url,
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
        return (data if isinstance(data, list) else data.get("apps", [])), ""
    except Exception as e:                                     # noqa: BLE001
        return [], f"portal query failed: {type(e).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="prod-conformance")
    ap.add_argument("--portal-base",
                    default="http://fuzefront-applications.fuzefront.svc.cluster.local:3003")
    ap.add_argument("--portal-token", default="")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any deployed product fails its own health probe")
    args = ap.parse_args()

    rows = [measure(ns) for ns in namespaces()]
    apps, portal_reason = portal(args.portal_base, args.portal_token)
    by_slug = {}
    for a in apps:
        slug = (a.get("slug") or a.get("name") or "").lower().replace(" ", "")
        by_slug[slug] = a

    print("| namespace | backend | swagger | MF remote | MCP | A2A | portal | public hosts |")
    print("|---|---|---|---|---|---|---|---|")
    failures = []
    for r in rows:
        ns = r["namespace"]
        if portal_reason:
            pcell = "UNVERIFIED"
        else:
            a = by_slug.get(ns)
            # Registered AND enabled are separate facts and are reported as such: a row
            # the portal knows about but has switched off is not the same as one it has
            # never heard of, and collapsing them is what made "8 of 19" unactionable.
            pcell = "YES" if (a and a.get("isActive")) else ("REGISTERED-OFF" if a else "NO")
        pub = ", ".join(r["public"]) or "-"
        print(f"| {ns} | {r['backend']} | {r['swagger']} | {r['remote']} | "
              f"{r['mcp']} | {r['a2a']} | {pcell} | {pub} |")
        if r["backend"] == "NO" and any(s["kind"] == "backend" for s in r["services"]):
            failures.append(f"{ns}: a backend Service exists but no health path answered")

    print()
    if portal_reason:
        print(f"> **portal column is UNVERIFIED** — {portal_reason}")
    for f in failures:
        print(f"> - {f}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "portalReason": portal_reason,
                       "portalApps": apps}, fh, indent=2, ensure_ascii=False)

    if args.strict and failures:
        print(f"::error title=prod-conformance::{len(failures)} product(s) serve no health endpoint")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
