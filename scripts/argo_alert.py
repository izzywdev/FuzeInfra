#!/usr/bin/env python3
"""Turn an Argo CD `argo-out-of-sync` dispatch into an actionable alert.

Called by .github/workflows/argo-outofsync-autofix.yml. The payload is built by
argocd/notifications/argocd-notifications-cm.yaml (template.app-unhealthy).

Why this exists, rather than shell in the workflow (#1278):

* THE REASON NEVER ARRIVED. FuzeFront could not deploy for six days. Every
  sync failed with `one or more synchronization tasks are not valid`, and the
  only line that named the cause —
      ClusterRole ... is not permitted in project fuzefront
  — lives in operationState.syncResult.resources[].message, which the payload
  did not carry. The alert said "unhealthy" and nothing an owner could act on.
* WRONG OWNER. A consumer app's alert went only to the consumer repo. An
  AppProject denial is a boundary question; the consumer cannot widen its
  project past the gate (scripts/validate_consumer_appproject.py, R3) and
  should not try. Those now ALSO go to FuzeInfra.
* SILENCE AFTER THE FIRST ALERT. With one issue open per app, every later
  alert exited without a word — and the title match `"$APP in:title"` meant an
  open `fuzefront-sealed` issue also silenced `fuzefront`. Now an open issue
  gets a comment per new failing revision, and matching is by exact title.
* INJECTION. The workflow spliced `${{ toJSON(...conditions) }}` into a
  single-quoted shell string: a `'` in any Argo message broke the step, and a
  crafted dispatch could run commands on the runner holding GH_TOKEN. The
  payload now arrives in an env var and is only ever parsed here as data.

Stdlib only. Every value from the payload is treated as untrusted text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
PERMISSION_MARK = "not permitted in project"
MAX_LINES = 15
MAX_MSG = 400
FENCE = "~~~"


def _text(value, limit: int = MAX_MSG) -> str:
    """Untrusted value -> single-line, bounded, fence-safe text."""
    s = "" if value is None else str(value)
    s = " ".join(s.split())
    s = s.replace(FENCE, "~ ~ ~")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def load(raw: str) -> dict:
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    app = payload.get("app", "")
    if not isinstance(app, str) or not APP_RE.match(app):
        raise ValueError(f"rejected app name: {app!r}")
    failed = payload.get("failed_resources") or []
    if not isinstance(failed, list):
        failed = []
    conditions = payload.get("conditions") or []
    if not isinstance(conditions, list):
        conditions = []
    return {
        "app": app,
        "sync": _text(payload.get("sync"), 40),
        "health": _text(payload.get("health"), 40),
        "phase": _text(payload.get("phase"), 40),
        "message": _text(payload.get("message")),
        "revision": _text(payload.get("revision"), 64),
        "failed": [r for r in failed if isinstance(r, dict)],
        "conditions": [c for c in conditions if isinstance(c, dict)],
    }


def failed_lines(p: dict) -> list[str]:
    lines = []
    for r in p["failed"]:
        where = "/".join(x for x in (_text(r.get("namespace"), 63), _text(r.get("name"), 253)) if x)
        lines.append(
            f'{_text(r.get("kind"), 63)} {where}: {_text(r.get("status"), 30)} — {_text(r.get("message"))}'
        )
    for c in p["conditions"]:
        lines.append(f'condition {_text(c.get("type"), 63)}: {_text(c.get("message"))}')
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"… {len(lines) - MAX_LINES} more"]
    return lines


def classify(p: dict) -> str:
    haystack = " ".join([p["message"]] + failed_lines(p))
    if PERMISSION_MARK in haystack:
        return "project-permission"
    if p["phase"] in ("Failed", "Error"):
        return "sync-failed"
    if p["sync"] == "Unknown":
        return "render-error"
    if p["health"] == "Degraded":
        return "degraded"
    return "out-of-sync"


def marker(p: dict) -> str:
    """Idempotency key: one report per app / revision / state."""
    key = "|".join([p["app"], p["revision"] or "-", p["phase"] or "-", p["sync"], p["health"]])
    return f"<!-- argo-autofix:{key} -->"


def title(p: dict, audience: str) -> str:
    if audience == "boundary":
        return f"ArgoCD AppProject blocks: {p['app']}"
    return f"ArgoCD unhealthy: {p['app']} ({p['health']}/{p['sync']})"


def title_prefix(p: dict, audience: str) -> str:
    """Exact-match prefix. `fuzefront` must not match `fuzefront-sealed`."""
    if audience == "boundary":
        return f"ArgoCD AppProject blocks: {p['app']}"
    return f"ArgoCD unhealthy: {p['app']} ("


ASKS = {
    "project-permission": (
        "Argo refused the sync because a rendered object is **not permitted by the "
        "app's AppProject** (a cluster-scoped kind outside the whitelist, or a "
        "namespace outside its destinations). One invalid task fails the whole sync, "
        "so nothing in this app is deploying.\n\n"
        "Fix it by removing the object from the chart, or by rendering it into an "
        "allowed namespace. Do **not** widen the AppProject: consumer projects may "
        "whitelist only `Namespace` among cluster-scoped kinds (#629) and never target "
        "`fuzeinfra` (#99) — `argocd-register` rejects anything wider "
        "(`scripts/validate_consumer_appproject.py`). If the workload genuinely needs "
        "a cluster-scoped grant, FuzeInfra provisions it (see "
        "`helm/fuzeinfra/templates/workload-identity-rbac.yaml`)."
    ),
    "sync-failed": (
        "The manifests rendered but the apply was **rejected**, so the desired state "
        "was never reached and Argo will not retry this revision. Typical causes: an "
        "invalid manifest, an immutable-field change (Jobs need "
        "`argocd.argoproj.io/sync-options: Force=true,Replace=true`), a failed hook."
    ),
    "render-error": "Manifest generation failed (`Unknown`/ComparisonError): a values or kustomize render error.",
    "degraded": "Applied, but the running workload is unhealthy (ImagePullBackOff, CrashLoopBackOff, unready pods, failed hooks).",
    "out-of-sync": "Live state drifted from a renderable desired state.",
}


def body(p: dict, audience: str, owner_repo: str, is_self: bool) -> str:
    cls = classify(p)
    lines = failed_lines(p) or ["(the payload named no failing resource — read the Application directly)"]
    if audience == "boundary":
        intro = (
            f"Consumer app **`{p['app']}`** (`{owner_repo}`) is blocked by its AppProject. "
            "Raised here as well because the project boundary is FuzeInfra's policy, "
            "and the consumer cannot resolve it by widening the project."
        )
        handler = (
            "@fuze please confirm which object is denied and whether the right fix is "
            "in the consumer chart or a FuzeInfra-provisioned grant. Read-only: "
            "`cluster-query.yml` with `-n argocd describe application "
            f"{p['app']}`. Never widen a consumer project past R3/R4."
        )
    elif is_self:
        intro = f"ArgoCD reports app **`{p['app']}`** unhealthy on the shared FuzeInfra cluster."
        handler = (
            "@fuze please diagnose with prod cluster reads (`kubectl get application "
            f"{p['app']} -n argocd -o json`; compare live vs the Helm-rendered desired) "
            "and FIX the chart under `helm/fuzeinfra/` so it converges, then open a PR. "
            "If the state is transient, say so and make no change. Never kubectl-patch "
            "chart-managed resources — reconcile via chart + PR."
        )
    else:
        intro = f"ArgoCD reports app **`{p['app']}`** unhealthy on the shared FuzeInfra cluster."
        handler = (
            "FuzeInfra runs the Argo instance but does not edit consumer charts. @fuze "
            "please diagnose from this repo's deploy manifests (Helm/kustomize/Argo "
            "Application) and open a fix PR."
        )
    out = [
        intro,
        "",
        f"- class: `{cls}`",
        f"- sync: `{p['sync']}` · health: `{p['health']}` · last operation: `{p['phase'] or 'n/a'}`",
        f"- revision: `{p['revision'] or 'n/a'}`",
        f"- operation message: {p['message'] or 'n/a'}",
        "",
        "Failing resources (the actual reason):",
        FENCE,
        *lines,
        FENCE,
        "",
        ASKS[cls],
        "",
        handler,
        "",
        marker(p),
    ]
    return "\n".join(out)


def telegram(p: dict, owner_repo: str) -> str:
    cls = classify(p)
    first = (failed_lines(p) or [p["message"] or "no reason in payload"])[0]
    return (
        f"🔴 ArgoCD {cls}: {p['app']}\n"
        f"sync={p['sync']} health={p['health']} op={p['phase'] or 'n/a'}\n"
        f"repo={owner_repo}\n"
        f"why: {_text(first, 300)}\n"
        f"Review: https://argocd.prod.fuzefront.com/applications/{p['app']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render an Argo alert from $PAYLOAD.")
    ap.add_argument("command", choices=["classify", "marker", "title", "title-prefix", "body", "telegram"])
    ap.add_argument("--audience", choices=["owner", "boundary"], default="owner")
    ap.add_argument("--owner-repo", default="")
    ap.add_argument("--is-self", choices=["true", "false"], default="false")
    args = ap.parse_args(argv)
    try:
        p = load(os.environ.get("PAYLOAD", ""))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    out = {
        "classify": lambda: classify(p),
        "marker": lambda: marker(p),
        "title": lambda: title(p, args.audience),
        "title-prefix": lambda: title_prefix(p, args.audience),
        "body": lambda: body(p, args.audience, args.owner_repo, args.is_self == "true"),
        "telegram": lambda: telegram(p, args.owner_repo),
    }[args.command]()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
