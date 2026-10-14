#!/usr/bin/env python3
"""
mcp_check.py — the DETERMINISTIC half of mcp-maintain.

WHY THIS EXISTS. `mcp-maintain` used to be an LLM agent on every PR: it read the repo and
decided whether the MCP surface was in sync. That made a CI check whose pass/fail depended on
an LLM call — so when every vendor rung was out of credit the check went red for a reason that
had nothing to do with the reviewed change (measured 2026-10-13, FuzeInfra PR #1363). Most of
what the maintainer does is not a judgement call at all: whether a Dockerfile/gateway spec
exists, which endpoints become tools, whether `tools.json` matches the spec, and whether the
tool count is within bounds are all MECHANICAL. This module does that mechanically and offline,
so the agent runs ONLY for the residue that genuinely needs language judgement (drafting a
missing tool description), and a billing outage can no longer turn an MCP PR red.

GROUNDED IN THE TREE, NOT A WISH. Measured state this was written against (re-measure before
amending — this family has shipped standards written from a document that the tree contradicted):

  - The fleet standardised on the SHARED gateway dialect: `mcp.servers[]` carries
    `{image, spec, overrides, manifest, endpoints}` and the gateway PROJECTS the repo's
    OpenAPI `spec` into tools. There is no per-repo server code to inspect. (repo-manifest
    .schema.json `properties.mcp`; scripts/bootstrap/caps/mcp_gateway.py.)
  - The gateway image `ghcr.io/izzywdev/fuze-mcp-gateway` is NOT published yet
    (FuzeService#37). So this checker reasons about the SPEC FILE on disk, never a running
    gateway — it is correct and runnable today, before the image ships, and stays the offline
    source of truth after.
  - `x-mcp-expose` is a NEW selector introduced by this standard; no spec in the tree carries
    it yet. The "no annotations present" path below is therefore the common case, not the
    exception, which is exactly why it must have a deterministic default rather than erroring.

WHAT IT IS NOT. It does not boot the gateway, call a model, touch the network, write a secret,
or deploy. It reads the manifest + the OpenAPI spec + an optional `mcp/tool-selection.yaml`,
computes the tool set the gateway WOULD project under the coverage rule, and compares it to the
committed `mcp/tools.json`. `--write` reconciles the manifest; without it, drift fails.

THE COVERAGE RULE (governance/mcp-tool-coverage-standard.md §3 is authoritative; this is the
executable copy):

  E  = eligible operations  = spec operations that are NOT deprecated and NOT excluded
                              (exclude.pathGlobs / exclude.operationIds / exclude by tag).
  T  = selected tools:
         if ANY eligible op carries `x-mcp-expose: true`  -> T = those ops           (explicit)
         else                                             -> T = eligible SAFE-READ  (default)
                                                              ops (GET returning a
                                                              collection or a resource)
  BOUNDS (this is the "at least N tools" rule):
         floor:  |T| >= min(E, minTools)      default minTools = 3
         cap:    |T| <= maxTools              default maxTools = 40

  Over the cap  -> FAIL asking for curation (`x-mcp-expose` / `exclude` / collapse-by-resource).
                   We never silently truncate: picking which of 41 endpoints to drop is a
                   product decision, so the deterministic answer is "a human decides", not a
                   guess. (`mode: ranked` + `x-mcp-priority` opts into deterministic trimming.)
  Under the floor -> FAIL asking for `x-mcp-expose`. A declared MCP surface that would expose
                   fewer tools than its own floor is the defect — usually an API with eligible
                   write endpoints but no safe reads and no annotations. E==0 is NOT a floor
                   violation: a spec with nothing eligible has no surface to cover.

`mutates` is derived from the HTTP method (GET/HEAD => false, everything else => true) and can
never be weakened by config — a handler that mutates under `mutates:false` is the bug, not the
classification. `manifest.irreversibleTools` is cross-checked: each must resolve to a selected
tool with `mutates:true`, or the guard protects nothing while reading as present (the same
vacuous shape mcp_gateway.py already refuses).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

DEFAULT_MAX_TOOLS = 40
DEFAULT_MIN_TOOLS = 3
DEFAULT_MANIFEST_PATH = "mcp/tools.json"

# HTTP methods that are real operations. `head`/`options`/`trace` are not projected as tools.
_TOOL_METHODS = ("get", "post", "put", "patch", "delete")
_SAFE_READ_METHODS = ("get",)


class McpCheckError(Exception):
    """A usage/environment error (missing spec, unparseable file) — distinct from a policy
    violation, which is data the caller reports, not an exception."""


# --------------------------------------------------------------------------- spec loading


def _load_yaml_or_json(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # OpenAPI specs and the selection file are normally YAML.
    except ImportError:  # pragma: no cover - exercised only on a yaml-less runner
        return json.loads(text)
    doc = yaml.safe_load(text)
    if doc is None:
        return {}
    return doc


def gateway_spec_path(manifest):
    """The OpenAPI spec the gateway projects, from the mcp block, or None.

    Mirrors mcp_gateway.py: the spec lives per-SERVER, not at the top of the block, and the
    first server that names one wins (the fleet uses a single gateway server per repo)."""
    block = manifest.get("mcp") or {}
    for server in block.get("servers") or []:
        if isinstance(server, dict) and server.get("spec"):
            return server["spec"]
    return None


def tools_manifest_path(manifest):
    """Where the generated tool manifest lives (`servers[].manifest`), default mcp/tools.json."""
    block = manifest.get("mcp") or {}
    for server in block.get("servers") or []:
        if isinstance(server, dict) and server.get("manifest"):
            return server["manifest"]
    return DEFAULT_MANIFEST_PATH


# --------------------------------------------------------------------------- operations


class Operation:
    __slots__ = ("path", "method", "operation_id", "summary", "description",
                 "deprecated", "tags", "expose", "priority", "raw")

    def __init__(self, path, method, raw):
        self.path = path
        self.method = method.lower()
        self.raw = raw if isinstance(raw, dict) else {}
        self.operation_id = self.raw.get("operationId")
        self.summary = self.raw.get("summary")
        self.description = self.raw.get("description")
        self.deprecated = bool(self.raw.get("deprecated"))
        self.tags = list(self.raw.get("tags") or [])
        # `x-mcp-expose` is tri-state: True (force in), False (force out), absent (defer to rule).
        self.expose = self.raw.get("x-mcp-expose")
        self.priority = self.raw.get("x-mcp-priority")

    @property
    def tool_name(self):
        """operationId is the tool name. Fall back to `method_path` only so a spec missing
        operationIds still yields stable, unique names rather than crashing — but the standard
        wants operationId, and the agent fallback flags a spec that lacks them."""
        if self.operation_id:
            return self.operation_id
        slug = self.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        return f"{self.method}_{slug}" if slug else self.method

    @property
    def mutates(self):
        return self.method not in _SAFE_READ_METHODS and self.method != "head"

    @property
    def is_safe_read(self):
        return self.method in _SAFE_READ_METHODS


def iter_operations(spec):
    """Every proj(path, method) operation in the spec, in document order."""
    ops = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return ops
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, raw in methods.items():
            if method.lower() in _TOOL_METHODS:
                ops.append(Operation(path, method, raw))
    return ops


# --------------------------------------------------------------------------- selection policy


class Policy:
    def __init__(self, data=None):
        data = data or {}
        self.max_tools = int(data.get("maxTools", DEFAULT_MAX_TOOLS))
        self.min_tools = int(data.get("minTools", DEFAULT_MIN_TOOLS))
        self.mode = data.get("mode", "curated")  # curated | ranked
        exclude = data.get("exclude") or {}
        self.exclude_path_globs = list(exclude.get("pathGlobs") or [])
        self.exclude_operation_ids = set(exclude.get("operationIds") or [])
        self.exclude_tags = set(exclude.get("tags") or [])
        self.exclude_deprecated = bool(exclude.get("deprecated", True))

    @classmethod
    def load(cls, path):
        if path and os.path.isfile(path):
            return cls(_load_yaml_or_json(path))
        return cls()


def is_excluded(op, policy):
    if policy.exclude_deprecated and op.deprecated:
        return True
    if op.operation_id and op.operation_id in policy.exclude_operation_ids:
        return True
    if policy.exclude_tags and set(op.tags) & policy.exclude_tags:
        return True
    for glob in policy.exclude_path_globs:
        if fnmatch.fnmatch(op.path, glob):
            return True
    return False


def eligible_operations(ops, policy):
    return [op for op in ops if not is_excluded(op, policy)]


def select_tools(eligible, policy):
    """Apply the coverage rule's SELECTION step (bounds are enforced separately).

    Returns (selected, mode) where mode is 'explicit' | 'default-coverage' | 'empty'."""
    explicit_in = [op for op in eligible if op.expose is True]
    if explicit_in:
        return explicit_in, "explicit"
    # No explicit opt-ins: default-coverage = safe reads, minus any explicit opt-OUT.
    default = [op for op in eligible if op.is_safe_read and op.expose is not False]
    if default:
        return default, "default-coverage"
    return [], "empty"


# --------------------------------------------------------------------------- bounds


def enforce_bounds(selected, eligible_count, policy):
    """The 'at least N tools / at most maxTools' rule. Returns a list of violation strings
    (empty == within bounds)."""
    violations = []
    n = len(selected)
    if n > policy.max_tools:
        violations.append(
            f"selection has {n} tools, over the cap of {policy.max_tools}. Curate the surface "
            f"with `x-mcp-expose: true` on the operations that should be tools, `exclude` the "
            f"rest, or collapse CRUD endpoints by resource. (Set `mode: ranked` + `x-mcp-priority` "
            f"to opt into deterministic trimming instead of this hard stop — we never guess which "
            f"endpoints to drop.)")
    effective_min = min(eligible_count, policy.min_tools)
    if eligible_count > 0 and n < effective_min:
        violations.append(
            f"selection has {n} tools, under the floor of {effective_min} "
            f"(min(eligible={eligible_count}, minTools={policy.min_tools})). Mark the operations "
            f"that should be tools with `x-mcp-expose: true` — a declared MCP surface that exposes "
            f"fewer tools than its floor covers too little of its own API to be worth a pod.")
    return violations


def apply_ranked_trim(selected, policy):
    """When `mode: ranked`, trim an over-cap selection deterministically by `x-mcp-priority`
    (lower number = higher priority; missing priority sorts last), tie-broken by path+method so
    the result is stable across runs. Only called when the caller has opted in."""
    def key(op):
        pri = op.priority if isinstance(op.priority, int) else 10 ** 9
        return (pri, op.path, op.method)
    return sorted(selected, key=key)[: policy.max_tools]


# --------------------------------------------------------------------------- projection


def _input_schema(op):
    """Merge path/query parameters and a requestBody JSON-Schema into one object schema. This
    is a deterministic shape, NOT a claim to match the gateway byte-for-byte — the gateway owns
    runtime projection; this is the offline expectation `tools.json` is diffed against."""
    props = {}
    required = []
    for param in op.raw.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not name:
            continue
        schema = param.get("schema") or {}
        props[name] = {"type": schema.get("type", "string")}
        if param.get("description"):
            props[name]["description"] = param["description"]
        if param.get("required"):
            required.append(name)
    body = op.raw.get("requestBody") or {}
    content = body.get("content") or {}
    json_body = content.get("application/json") or {}
    body_schema = json_body.get("schema")
    if isinstance(body_schema, dict):
        props["body"] = body_schema
        if body.get("required"):
            required.append("body")
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = sorted(required)
    return schema


def project_tool(op):
    return {
        "name": op.tool_name,
        "description": (op.summary or op.description or "").strip(),
        "inputSchema": _input_schema(op),
        "mutates": op.mutates,
    }


def build_tools_json(selected):
    """The canonical tools.json content: tools sorted by name so the file is stable and a diff
    reflects a real change, not reordering."""
    tools = sorted((project_tool(op) for op in selected), key=lambda t: t["name"])
    return {"tools": tools}


# --------------------------------------------------------------------------- the check


class Result:
    def __init__(self):
        self.status = "in-sync"      # in-sync | reconciled | drift | violation | no-surface
        self.violations = []
        self.needs_agent = False
        self.agent_reasons = []
        self.messages = []
        self.expected = None         # the tools.json we computed
        self.wrote = False

    @property
    def ok(self):
        return self.status in ("in-sync", "reconciled", "no-surface")


def _irreversible_violations(manifest, selected):
    declared = (manifest.get("mcp") or {}).get("irreversibleTools") or []
    if not declared:
        return []
    by_name = {op.tool_name: op for op in selected}
    out = []
    for name in declared:
        op = by_name.get(name)
        if op is None:
            out.append(
                f"irreversibleTools names `{name}`, which is not a selected tool. A confirmation "
                f"guard on a tool that is not exposed protects nothing while reading as present.")
        elif not op.mutates:
            out.append(
                f"irreversibleTools names `{name}`, but it is a safe-read (mutates:false). An "
                f"irreversible guard on a read is a contradiction.")
    return out


def check(manifest, repo_root, policy, write=False):
    """The whole deterministic check. Pure except for the optional `--write` to tools.json."""
    result = Result()

    spec_rel = gateway_spec_path(manifest)
    if not spec_rel:
        # No gateway spec: either the stdio dialect (a hand-written server this checker does not
        # own) or no surface at all. Deterministic check has nothing to do; the agent decides.
        result.status = "no-surface"
        result.needs_agent = bool(manifest.get("mcp"))
        if result.needs_agent:
            result.agent_reasons.append(
                "mcp block present but no gateway `spec` — a stdio server this checker does not "
                "project; the agent verifies it starts and lists tools.")
        result.messages.append("no gateway spec declared; deterministic projection skipped")
        return result

    spec_path = os.path.join(repo_root, spec_rel)
    if not os.path.isfile(spec_path):
        result.status = "violation"
        result.violations.append(
            f"mcp server `spec` points at `{spec_rel}`, which does not exist. The gateway would "
            f"project zero tools from a missing spec.")
        return result

    try:
        spec = _load_yaml_or_json(spec_path)
    except Exception as exc:  # noqa: BLE001 - surface any parse error as a violation, not a crash
        result.status = "violation"
        result.violations.append(f"could not parse spec `{spec_rel}`: {exc}")
        return result

    ops = iter_operations(spec)
    eligible = eligible_operations(ops, policy)
    selected, mode = select_tools(eligible, policy)
    result.messages.append(
        f"spec={spec_rel} operations={len(ops)} eligible={len(eligible)} "
        f"selected={len(selected)} mode={mode}")

    if not eligible:
        # A declared surface whose spec has no eligible operation is suspect but not this gate's
        # call to fail — flag for the agent (the spec may be a stub, or everything got excluded).
        result.status = "no-surface"
        result.needs_agent = True
        result.agent_reasons.append(
            "spec declares no eligible operations (all excluded or none present) — verify the "
            "spec is the real contract, not a stub.")
        return result

    if len(selected) > policy.max_tools and policy.mode == "ranked":
        selected = apply_ranked_trim(selected, policy)
        result.messages.append(f"ranked-trim applied -> {len(selected)} tools")

    result.violations.extend(enforce_bounds(selected, len(eligible), policy))
    result.violations.extend(_irreversible_violations(manifest, selected))

    # The one legitimate LLM fallback: a selected tool with no human-written text. The gateway
    # can project it, but a tool with an empty description is close to useless to a session, and
    # inventing product copy is not this checker's job. Non-gating — a suggestion, not a red.
    undocumented = [op.tool_name for op in selected
                    if not (op.summary or op.description)]
    if undocumented:
        result.needs_agent = True
        result.agent_reasons.append(
            "selected tools have no summary/description: " + ", ".join(sorted(undocumented)[:10])
            + (" …" if len(undocumented) > 10 else ""))

    result.expected = build_tools_json(selected)

    if result.violations:
        result.status = "violation"
        return result

    # Compare against the committed manifest; reconcile or report drift.
    manifest_rel = tools_manifest_path(manifest)
    manifest_path = os.path.join(repo_root, manifest_rel)
    committed = None
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                committed = json.load(f)
        except (OSError, json.JSONDecodeError):
            committed = None

    if committed == result.expected:
        result.status = "in-sync"
        return result

    if write:
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(result.expected, f, indent=2, sort_keys=False)
            f.write("\n")
        result.wrote = True
        result.status = "reconciled"
        result.messages.append(f"wrote {manifest_rel} ({len(result.expected['tools'])} tools)")
    else:
        result.status = "drift"
        result.messages.append(
            f"{manifest_rel} is out of date vs the spec; re-run with --write to regenerate it")
    return result


# --------------------------------------------------------------------------- CLI


def _emit_github_output(result):
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"status={result.status}\n")
            f.write(f"needs-agent={'true' if result.needs_agent else 'false'}\n")
            f.write(f"ok={'true' if result.ok else 'false'}\n")
    except OSError:
        # Best-effort: emitting GitHub step outputs must never crash the checker. The job's
        # pass/fail is decided by the process exit code in main(), not by these outputs, so a
        # transiently unwritable $GITHUB_OUTPUT is a tolerable degradation, not an error to
        # propagate (which would turn a successful check into a spurious failure).
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deterministic MCP tool-coverage checker.")
    parser.add_argument("--manifest", default=".fuze/manifest.json",
                        help="path to the repo manifest (default .fuze/manifest.json)")
    parser.add_argument("--repo-root", default=".",
                        help="repo root the spec/manifest paths are relative to (default .)")
    parser.add_argument("--selection", default=None,
                        help="path to mcp/tool-selection.yaml (default: <repo-root>/mcp/"
                             "tool-selection.yaml if present)")
    parser.add_argument("--write", action="store_true",
                        help="regenerate the tools manifest on drift instead of failing")
    args = parser.parse_args(argv)

    manifest_path = args.manifest
    if not os.path.isabs(manifest_path):
        manifest_path = os.path.join(args.repo_root, manifest_path)
    if not os.path.isfile(manifest_path):
        print(f"::notice title=mcp-check::no manifest at {args.manifest}; nothing to check")
        return 0
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error title=mcp-check::cannot read manifest {args.manifest}: {exc}")
        return 1

    if not manifest.get("mcp"):
        print("::notice title=mcp-check::manifest declares no mcp block; nothing to check")
        return 0

    selection_path = args.selection or os.path.join(args.repo_root, "mcp", "tool-selection.yaml")
    policy = Policy.load(selection_path)

    result = check(manifest, args.repo_root, policy, write=args.write)

    for msg in result.messages:
        print(f"::notice title=mcp-check::{msg}")
    for reason in result.agent_reasons:
        print(f"::notice title=mcp-check::needs-agent: {reason}")
    for v in result.violations:
        print(f"::error title=mcp-check::{v}")

    _emit_github_output(result)

    if result.status == "drift":
        print("::error title=mcp-check::tools.json is out of date; run "
              "`python3 scripts/mcp_check.py --write` and commit.")
        return 1
    if result.status == "violation":
        return 1
    print(f"::notice title=mcp-check::status={result.status} "
          f"needs-agent={'true' if result.needs_agent else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
