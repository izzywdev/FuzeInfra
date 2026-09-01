#!/usr/bin/env python3
"""Preflight the fleet target list that runner-watch.yml just fetched from the hub.

WHY THIS FILE EXISTS AT ALL.

`scripts/runner_watch.py` is a PORT — its authoring home is FuzeSDLC (PR #269) and it is
kept as close to byte-identical with that copy as the move allows, so that the next change
upstream can be ported by reading a diff rather than by re-deriving an argument. Everything
the move genuinely required went into the two places that could absorb it without touching
the decision logic: the workflow, and this file.

WHAT THE MOVE REQUIRED. In FuzeSDLC the fleet list `governance/ruleset-fleet.json` sat in
the same repo as the watcher, so `fleet_repos()` was a plain local file read. FuzeInfra runs
the INSTANCE but the hub still owns the LIST (see runner_watch.py's "WHERE THIS RUNS",
point 2 — a second copy is a second thing to forget), so the file now arrives over the
network, and a network fetch has failure modes a local read does not:

  * the request fails                 -> handled in the workflow (`gh api` non-zero => exit 1)
  * the request SUCCEEDS but the body is not the file you wanted

The second is the dangerous one and is why this is a script rather than a `test -s`. A
`gh api` call that 404s or hits a permissions wall can still exit 0 in some shapes and
write a JSON ERROR OBJECT to the output path. That file is valid JSON, so
`fleet_repos()` would parse it happily, find no `repos` key, and return an EMPTY LIST — and
an empty fleet is not an error to the watcher, it is a clean sweep of nothing. The run goes
green having looked at zero repos. That is precisely the silent-blindness failure the whole
design refuses ("a watcher that cannot see must never report all-clear"), so the fleet list
is validated before either driver is invoked, and an empty or malformed one is FATAL.

Deliberately NOT here: any fallback to a vendored copy, and any "0 repos is fine if the
file looked ok". Both convert a loud stop into a quiet no-op.

Offline-testable: `preflight()` is a pure function over the parsed document.
"""

from __future__ import annotations

import json
import sys
from typing import Dict, List


class FleetPreflightError(RuntimeError):
    """The fetched document cannot be trusted as the fleet list."""


def preflight(doc: object) -> List[str]:
    """Return the effective `owner/name` targets, or raise FleetPreflightError.

    Mirrors `runner_watch.fleet_repos()`'s reduction (owner + repos - excluded) rather than
    re-deriving it: this must fail on exactly the documents that would make that function
    return nothing useful, not on a different set.
    """
    if not isinstance(doc, dict):
        raise FleetPreflightError(
            f"fleet list is a {type(doc).__name__}, not a JSON object — this is what a "
            f"fetch that silently returned something other than the file looks like"
        )

    # A GitHub API error body is a valid JSON object with a `message`. Name it explicitly:
    # the generic 'no repos' message below would be true but would send the reader hunting
    # in the wrong file.
    if "message" in doc and "repos" not in doc:
        raise FleetPreflightError(
            f"fetched a GitHub API ERROR body, not the fleet list: "
            f"{doc.get('message')!r}. Check the fuze-agent App's contents:read grant on "
            f"the hub repo."
        )

    owner = doc.get("owner")
    if not isinstance(owner, str) or not owner:
        raise FleetPreflightError("fleet list has no `owner` — refusing to guess one")

    repos = doc.get("repos")
    if not isinstance(repos, list):
        raise FleetPreflightError("fleet list has no `repos` array")

    excluded: Dict[str, str] = doc.get("excluded") or {}
    targets = [f"{owner}/{n}" for n in repos if n not in set(excluded)]

    if not targets:
        raise FleetPreflightError(
            "fleet list parsed but is EMPTY after exclusions. An empty fleet is NOT a "
            "clean run — the watcher would sweep zero repos and report green, which is "
            "indistinguishable from a healthy fleet. Stopping instead."
        )
    return targets


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: runner_watch_fleet_preflight.py <fleet-file>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print(
            f"::error title=runner-watch::fleet list at {path} is unreadable or not JSON "
            f"({exc}). Refusing to sweep an unknown fleet and call it green.",
            file=sys.stderr,
        )
        return 1

    try:
        targets = preflight(doc)
    except FleetPreflightError as exc:
        print(f"::error title=runner-watch::{exc}", file=sys.stderr)
        return 1

    excluded = doc.get("excluded") or {}
    print(f"fleet: {len(targets)} repo(s) under owner {doc.get('owner')!r} "
          f"({len(excluded)} excluded: {', '.join(sorted(excluded)) or 'none'})")
    for slug in targets:
        print(f"  {slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
