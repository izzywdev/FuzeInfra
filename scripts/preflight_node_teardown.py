#!/usr/bin/env python3
"""Refuse to wipe a node while doing so would destroy the last copies of data.

WHY THIS EXISTS -- and why the check it replaces was not merely unenforced, but WRONG.

`ca-reinstall-controlplane.yml` already carried this instruction, as step 3 of a block
headed "BEFORE RUNNING, non-negotiable":

    confirm every Longhorn volume has replicas OFF this node

On 2026-09-07 that check was run before reinstalling `vmi3383846`. It PASSED. The
Prometheus TSDB was destroyed anyway, and every metric the cluster had was lost.

"Has a replica elsewhere" is not the same as "is safe to wipe":

  * `vmi3396106` had been rebuilt 25 minutes earlier, so `fuzeinfra-prometheus-data`
    was `robustness: degraded` and its replicas were still REBUILDING. A half-built
    replica satisfies "a replica exists elsewhere" and protects nothing.
  * The volume had sat at `numberOfReplicas: 1` for days. It was raised to 3 minutes
    before the teardown, which starts a rebuild -- it does not create redundancy.
  * The surviving count was therefore 1-ish, not 2. Wiping the second node took it
    to 0, and Longhorn reported `robustness: faulted` with both replica records
    pointing at node names that no longer existed.

So the gate is not "is there a replica somewhere". It is "how many INTACT copies
survive the teardown", plus "is a previous teardown's rebuild still settling".

DESIGN NOTES

* Fails CLOSED. Unreadable input, zero volumes parsed, or an unparseable timestamp
  all block. A safety check that cannot see the cluster must never report "safe" --
  that is precisely how the `node-orphan-cleanup` name-filter bug deleted live nodes.
* Counts only replicas that are BOTH `currentState == running` AND have no
  `failedAt`. Longhorn keeps failed replica records around; counting them is how the
  original check was fooled.
* Excludes the target node from the surviving count, which is the whole question
  being asked.
* Handles the already-dead-node case correctly without a special case: if the target
  is already gone its replicas are already failed, so they were never counted, and
  the gate blocks only if the REMAINING nodes genuinely lack redundancy.
* The cooldown catches the specific sequence that caused the loss: two teardowns
  close enough together that the first one's rebuild had not finished.

Usage:
    kubectl -n longhorn-system get volumes.longhorn.io -o json  > v.json
    kubectl -n longhorn-system get replicas.longhorn.io -o json > r.json
    python3 scripts/preflight_node_teardown.py --node <name> --volumes v.json --replicas r.json

    # or let it call kubectl itself:
    python3 scripts/preflight_node_teardown.py --node <name>

Exit codes:
    0  safe to proceed
    1  REFUSED -- tearing this node down risks data loss
    2  usage / input error (also a refusal: fail closed)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys

#: Copies that must survive the teardown. Two, not one: a single surviving copy means
#: the next disk problem -- or the next node rebuild -- is unrecoverable, which is the
#: state Prometheus was in for days before it was lost.
DEFAULT_MIN_SURVIVING = 2

#: A replica that failed this recently indicates a rebuild still in flight from an
#: earlier teardown. 30 minutes is deliberately longer than the 25 that elapsed
#: between the two rebuilds on 2026-09-07.
DEFAULT_COOLDOWN_MINUTES = 30


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def _load(path, kubectl_args):
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(_run(kubectl_args))


def _parse_ts(value):
    """RFC3339 -> aware datetime. Unparseable is an error, never 'old enough'."""
    if not value:
        return None
    txt = value.replace("Z", "+00:00")
    return _dt.datetime.fromisoformat(txt)


def replica_is_intact(replica, volume_attached):
    """Does this replica hold a usable copy of the data?

    `failedAt` is the primary signal and applies always: Longhorn retains records for
    replicas whose node was wiped, and they keep a plausible-looking `nodeID`. Counting
    those is exactly how the old check passed while both real copies were gone.

    `currentState == running` is only meaningful for an ATTACHED volume. A DETACHED
    volume has no running replicas by definition -- its data is intact, it simply is
    not mounted anywhere. Requiring `running` unconditionally marks every detached
    volume as having zero copies, which made the gate refuse every teardown forever.
    Caught by running this against live cluster state, where 7 detached volumes were
    reported at risk; the unit tests had not modelled a detached volume at all.
    """
    spec = replica.get("spec") or {}
    status = replica.get("status") or {}
    if spec.get("failedAt"):
        return False
    if volume_attached and status.get("currentState") != "running":
        return False
    return True


def evaluate(node, volumes, replicas, min_surviving=DEFAULT_MIN_SURVIVING,
             cooldown_minutes=DEFAULT_COOLDOWN_MINUTES, now=None):
    """Return (ok: bool, blockers: list[str], summary: dict). Pure; unit-testable."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    blockers = []

    vol_items = volumes.get("items")
    rep_items = replicas.get("items")
    if vol_items is None or rep_items is None:
        return False, ["could not read Longhorn volumes/replicas (fail closed)"], {}
    if not vol_items:
        return False, [
            "zero Longhorn volumes parsed. Either Longhorn is not installed or the "
            "query failed; refusing rather than reporting 'safe' from an empty set"
        ], {}

    by_volume = {}
    for r in rep_items:
        by_volume.setdefault((r.get("spec") or {}).get("volumeName"), []).append(r)

    # --- cooldown: is an earlier teardown's rebuild still settling? -------------
    recent = []
    for r in rep_items:
        failed_at = (r.get("spec") or {}).get("failedAt")
        if not failed_at:
            continue
        try:
            ts = _parse_ts(failed_at)
        except ValueError:
            return False, [f"unparseable failedAt {failed_at!r} (fail closed)"], {}
        age_min = (now - ts).total_seconds() / 60.0
        if age_min < cooldown_minutes:
            recent.append((r.get("metadata", {}).get("name", "?"),
                           (r.get("spec") or {}).get("nodeID"), round(age_min, 1)))
    if recent:
        detail = ", ".join(f"{n} on {nd} ({a}m ago)" for n, nd, a in recent[:4])
        blockers.append(
            f"{len(recent)} replica(s) failed within the last {cooldown_minutes}m "
            f"[{detail}]. A rebuild from a previous teardown is still in flight; "
            f"its replicas are not yet real redundancy. Wait for every volume to "
            f"report robustness=healthy."
        )

    # --- per-volume surviving redundancy --------------------------------------
    at_risk, checked = [], 0
    for v in vol_items:
        name = v.get("metadata", {}).get("name", "?")
        spec, status = v.get("spec") or {}, v.get("status") or {}
        reps = by_volume.get(name, [])
        # A volume with no replica records at all is already broken; say so.
        if not reps:
            at_risk.append((name, 0, spec.get("numberOfReplicas"),
                            status.get("robustness"), "no replica records"))
            continue
        attached = status.get("state") == "attached"
        surviving = [r for r in reps
                     if r["spec"].get("nodeID") != node
                     and replica_is_intact(r, attached)]
        checked += 1
        if len(surviving) < min_surviving:
            at_risk.append((name, len(surviving), spec.get("numberOfReplicas"),
                            status.get("robustness"),
                            "on " + ", ".join(sorted(
                                r["spec"].get("nodeID") or "?" for r in surviving)) or "-"))

    if at_risk:
        lines = [
            f"  {n}: {s} intact replica(s) would survive "
            f"(numberOfReplicas={want}, robustness={rob}) [{where}]"
            for n, s, want, rob, where in at_risk
        ]
        blockers.append(
            f"{len(at_risk)} volume(s) would be left with fewer than {min_surviving} "
            f"intact copies after wiping {node}:\n" + "\n".join(lines)
        )

    return (not blockers), blockers, {
        "node": node,
        "volumes_total": len(vol_items),
        "volumes_checked": checked,
        "volumes_at_risk": len(at_risk),
        "recent_replica_failures": len(recent),
        "min_surviving_required": min_surviving,
        "cooldown_minutes": cooldown_minutes,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node", required=True, help="node about to be wiped/reinstalled")
    ap.add_argument("--volumes", help="volumes.longhorn.io JSON (default: call kubectl)")
    ap.add_argument("--replicas", help="replicas.longhorn.io JSON (default: call kubectl)")
    ap.add_argument("--min-surviving", type=int, default=DEFAULT_MIN_SURVIVING)
    ap.add_argument("--cooldown-minutes", type=int, default=DEFAULT_COOLDOWN_MINUTES)
    ap.add_argument("--kubectl", default="kubectl")
    args = ap.parse_args(argv)

    try:
        volumes = _load(args.volumes, [args.kubectl, "-n", "longhorn-system",
                                       "get", "volumes.longhorn.io", "-o", "json"])
        replicas = _load(args.replicas, [args.kubectl, "-n", "longhorn-system",
                                         "get", "replicas.longhorn.io", "-o", "json"])
    except Exception as exc:  # noqa: BLE001 - any failure to see the cluster blocks
        print(f"REFUSED: cannot read Longhorn state ({exc}). Failing closed.",
              file=sys.stderr)
        return 2

    ok, blockers, summary = evaluate(
        args.node, volumes, replicas,
        min_surviving=args.min_surviving, cooldown_minutes=args.cooldown_minutes)

    print(f"preflight-node-teardown: node={summary.get('node')} "
          f"volumes={summary.get('volumes_total')} "
          f"at_risk={summary.get('volumes_at_risk')} "
          f"recent_failures={summary.get('recent_replica_failures')}")
    if ok:
        print(f"OK: every volume keeps >= {args.min_surviving} intact replicas "
              f"after wiping {args.node}.")
        return 0

    print(f"\nREFUSED: wiping {args.node} risks destroying the last copy of data.\n",
          file=sys.stderr)
    for b in blockers:
        print(f"- {b}", file=sys.stderr)
    print(
        "\nThis is the check that was missing on 2026-09-07, when the Prometheus TSDB "
        "was destroyed by two node rebuilds 25 minutes apart.\n"
        "To clear it: wait until every volume reports robustness=healthy (not "
        "degraded), and raise numberOfReplicas on any single-copy volume BEFORE the "
        "teardown, not during it.",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
