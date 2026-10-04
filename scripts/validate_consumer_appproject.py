#!/usr/bin/env python3
"""Apply-time policy gate for consumer-owned Argo CD AppProjects.

`argocd-register.yml` applies a consumer repo's `deploy/argocd/` to the shared
cluster, AppProjects included. That is deliberate: the owner decided (#639)
that products own their AppProject, so they can manage their own allow-lists
without a FuzeInfra PR for every change.

#639 moved the FILE. It did not move the POLICY. In #639's own words, #625's
"fleet-wide permission narrowing delivered in #629 — stand[s] and [is] not in
question", and the boundary from #99 (a consumer never deploys into
`fuzeinfra`) is restated in every consumer project's header. Nothing enforced
either once ownership moved, and both were broken in practice:

* FuzeFront#1198 whitelisted ClusterRole + ClusterRoleBinding in FuzeFront's own
  project. Argo whitelists by KIND, not by name, and that project also holds
  the `argocd` destination for its app-of-apps — so it could bind itself
  `cluster-admin`, or rewrite AppProjects, including its own.
* The same project existed in BOTH repos (FuzeInfra's argocd/projects/ copy was
  never deleted, and #1147 made deploy-prod re-apply every file there). Two
  writers, last-write-wins: FuzeFront deploys flapped, then froze from
  2026-10-02 until #1278.

This gate is the enforcement point, run BEFORE anything is applied. A rejected
registration applies nothing (fail closed) and says exactly why.

Rules (each one has a decision behind it):

  R1 not-reserved     A consumer may not define `fuzeinfra` or `default`.
                      Overwriting those would replace the infra boundary itself.
  R2 single-writer    A consumer may not define a project FuzeInfra still holds
                      in argocd/projects/. One writer per project (#1278).
  R3 cluster-scope    clusterResourceWhitelist may contain only core/Namespace
                      (#629; kept by #639). Anything wider is cluster admin.
  R4 destinations     No `fuzeinfra` or `kube-system` namespace (#99), no
                      wildcard namespace, and only the in-cluster server.
  R5 source-repos     No wildcard source repo.
  R6 app-divergence   An Application FuzeInfra also holds in argocd/applications/
                      must be IDENTICAL in spec. #639 moved only AppProjects, so
                      a held Application stays FuzeInfra's (ONBOARDING doc) and an
                      identical consumer copy is harmless; a DIVERGENT one is the
                      same last-write-wins flapping as R2, one level down.

Usage:
  validate_consumer_appproject.py --infra-projects argocd/projects \
      --infra-applications argocd/applications <dir-or-file>...
Exit 0 = everything passes (or nothing to check). Exit 1 = violations printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

RESERVED_PROJECTS = {"fuzeinfra", "default"}
FORBIDDEN_NAMESPACES = {"fuzeinfra", "kube-system"}
IN_CLUSTER_SERVER = "https://kubernetes.default.svc"
IN_CLUSTER_NAME = "in-cluster"
ALLOWED_CLUSTER_KINDS = {("", "Namespace")}


def infra_held_projects(projects_dir: Path) -> set[str]:
    """Names of AppProjects FuzeInfra applies itself (deploy-prod globs these)."""
    names = set()
    for path in sorted(projects_dir.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc and doc.get("kind") == "AppProject":
                names.add(doc["metadata"]["name"])
    return names


def infra_held_applications(apps_dir: Path) -> dict[str, dict]:
    """name -> spec for every Application FuzeInfra applies via deploy-prod."""
    held = {}
    for path in sorted(apps_dir.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc and doc.get("kind") == "Application":
                held[doc["metadata"]["name"]] = doc.get("spec") or {}
    return held


def check_application(doc: dict, held: dict[str, dict]) -> list[str]:
    name = (doc.get("metadata") or {}).get("name", "<unnamed>")
    if name not in held or (doc.get("spec") or {}) == held[name]:
        return []
    return [
        f"Application '{name}': R6 app-divergence: FuzeInfra also applies "
        f"Application '{name}' from argocd/applications/ on every deploy, with a "
        "DIFFERENT spec. Two writers with different content flap last-write-wins "
        "(the #1278 failure mode). Change it in one place: make the specs match, "
        "or remove one copy"
    ]


def check_project(doc: dict, infra_held: set[str], *, consumer: bool = True) -> list[str]:
    """Violations for one AppProject document; empty list means it passes.

    `consumer=False` skips R1/R2, for FuzeInfra-held consumer projects that are
    checked against the same boundary rules but are, by definition, held here.
    """
    name = (doc.get("metadata") or {}).get("name", "<unnamed>")
    spec = doc.get("spec") or {}
    errors: list[str] = []

    if consumer and name in RESERVED_PROJECTS:
        errors.append(
            f"R1 not-reserved: a consumer may not define AppProject '{name}' — "
            "that would replace FuzeInfra's own boundary"
        )
    if consumer and name in infra_held:
        errors.append(
            f"R2 single-writer: AppProject '{name}' is also held by FuzeInfra in "
            f"argocd/projects/{name}.yaml, which deploy-prod re-applies on every "
            "deploy. Two writers is last-write-wins and froze FuzeFront deploys "
            "(#1278). Delete one copy first — FuzeInfra's, if the product owns "
            "this project (#639)"
        )

    for entry in spec.get("clusterResourceWhitelist") or []:
        gk = (entry.get("group", ""), entry.get("kind", ""))
        if gk not in ALLOWED_CLUSTER_KINDS:
            group = gk[0] or "core"
            errors.append(
                f"R3 cluster-scope: clusterResourceWhitelist may contain only "
                f"core/Namespace (#629, kept by #639); found {group}/{gk[1]}. Argo "
                "whitelists by kind, not name — with the argocd destination that "
                "is cluster-admin. Ask FuzeInfra to provision the cluster-scoped "
                "grant instead (see helm/fuzeinfra/templates/workload-identity-rbac.yaml)"
            )

    for dest in spec.get("destinations") or []:
        ns = dest.get("namespace", "")
        if ns in FORBIDDEN_NAMESPACES:
            errors.append(
                f"R4 destinations: '{ns}' is an infrastructure namespace and is "
                "never a consumer destination (#99). Reach infra services over "
                "in-cluster DNS instead"
            )
        if "*" in ns or ns == "":
            errors.append(
                f"R4 destinations: wildcard/empty namespace {ns!r} would include "
                "fuzeinfra and kube-system (#99)"
            )
        server, cluster = dest.get("server"), dest.get("name")
        if server is not None and server != IN_CLUSTER_SERVER:
            errors.append(
                f"R4 destinations: server {server!r} — only {IN_CLUSTER_SERVER} is allowed"
            )
        if cluster is not None and cluster != IN_CLUSTER_NAME:
            errors.append(
                f"R4 destinations: cluster name {cluster!r} — only {IN_CLUSTER_NAME!r} is allowed"
            )
        if server is None and cluster is None:
            errors.append("R4 destinations: destination names neither server nor cluster")

    for repo in spec.get("sourceRepos") or []:
        if "*" in repo:
            errors.append(f"R5 source-repos: wildcard source repo {repo!r} is not allowed")

    return [f"AppProject '{name}': {e}" for e in errors]


def iter_manifests(paths: list[Path]):
    for p in paths:
        files = [p] if p.is_file() else sorted(
            f for f in p.rglob("*") if f.suffix in (".yaml", ".yml")
        )
        for f in files:
            for doc in yaml.safe_load_all(f.read_text()):
                if isinstance(doc, dict):
                    yield f, doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--infra-projects", required=True, type=Path,
                    help="FuzeInfra's argocd/projects directory")
    ap.add_argument("--infra-applications", type=Path,
                    help="FuzeInfra's argocd/applications directory (enables R6)")
    ap.add_argument("paths", nargs="+", type=Path)
    args = ap.parse_args(argv)

    infra_held = infra_held_projects(args.infra_projects)
    held_apps = infra_held_applications(args.infra_applications) if args.infra_applications else {}
    violations, seen = [], 0
    for path, doc in iter_manifests(args.paths):
        kind = doc.get("kind")
        if kind == "AppProject":
            seen += 1
            violations += [f"{path}: {v}" for v in check_project(doc, infra_held)]
        elif kind == "Application" and held_apps:
            violations += [f"{path}: {v}" for v in check_application(doc, held_apps)]

    for v in violations:
        print(f"::error::{v}")
    if violations:
        print(f"REJECTED: {len(violations)} policy violation(s); nothing was applied.")
        return 1
    print(f"OK: {seen} AppProject(s) checked against R1-R5; Applications checked against R6.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
