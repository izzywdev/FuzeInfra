"""Every `with:` key passed to a LOCAL composite action must be an input that
action actually declares.

WHY THIS EXISTS. GitHub Actions does not fail on an unknown input. It emits
`Unexpected input(s) 'x'` as a *warning* and carries on, so a misspelled or
invented parameter is silently dropped and the intent behind it is never
expressed. Nothing in CI reads that warning.

The cost of that on 2026-09-07 was the whole fleet's code review:
`fuze-code-action` passed `fallback-vendor: anthropic` to `llm-endpoint`, which
declares no such input. The line looked like it selected a fallback vendor; it
did nothing. `llm-endpoint` builds its fallback chain from the vendors whose key
input is non-empty, and only `fallback-anthropic-key` was being forwarded, so the
chain collapsed to a single vendor. A single-vendor anthropic chain deliberately
skips the runner-local LiteLLM container and calls Anthropic directly -- so the
gateway that exists precisely to survive one provider being down had nothing to
fall over to. When Anthropic returned "Credit balance is too low", every
`fuze-code-review` and `mcp-maintain` run failed closed, with two other
configured providers sitting unused.

A one-word typo in a `with:` block, invisible to every existing gate.

This test is offline and reads only files in the repo.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[1]
ACTIONS_DIR = REPO / ".github" / "actions"
SEARCH_DIRS = [REPO / ".github" / "workflows", ACTIONS_DIR]

LOCAL_USES_PREFIX = "./.github/actions/"


def _load(path: pathlib.Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a malformed file is another test's problem
        return None


def _declared_inputs(action_name: str) -> set[str] | None:
    """Inputs declared by a local composite action, or None if it has no action.yml."""
    for fname in ("action.yml", "action.yaml"):
        p = ACTIONS_DIR / action_name / fname
        if p.exists():
            doc = _load(p) or {}
            return set((doc.get("inputs") or {}).keys())
    return None


def _iter_steps(node):
    """Yield every step mapping anywhere in a workflow or composite action."""
    if isinstance(node, dict):
        steps = node.get("steps")
        if isinstance(steps, list):
            for s in steps:
                if isinstance(s, dict):
                    yield s
        for v in node.values():
            yield from _iter_steps(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_steps(v)


def _call_sites():
    """(file, action_name, with_keys) for every local-composite-action call."""
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.rglob("*.y*ml")):
            doc = _load(path)
            if not isinstance(doc, dict):
                continue
            for step in _iter_steps(doc):
                uses = step.get("uses")
                if not isinstance(uses, str) or not uses.startswith(LOCAL_USES_PREFIX):
                    continue
                action = uses[len(LOCAL_USES_PREFIX):].strip("/")
                with_block = step.get("with") or {}
                if not isinstance(with_block, dict):
                    continue
                yield path, action, set(with_block.keys())


def test_repo_has_local_action_call_sites():
    """Guard the guard: if the discovery breaks, this test must not silently pass."""
    sites = list(_call_sites())
    assert sites, (
        "No local composite-action call sites discovered. Either the repo genuinely "
        "has none (then delete this test) or the discovery is broken -- in which case "
        "every assertion below is vacuously true and this file protects nothing."
    )


def test_every_with_key_is_a_declared_input():
    problems = []
    for path, action, keys in _call_sites():
        declared = _declared_inputs(action)
        if declared is None:
            problems.append(
                f"{path.relative_to(REPO)}: uses ./.github/actions/{action}, "
                f"which has no action.yml"
            )
            continue
        unknown = sorted(keys - declared)
        if unknown:
            problems.append(
                f"{path.relative_to(REPO)} -> {action}: unknown input(s) "
                f"{unknown}. Declared: {sorted(declared)}"
            )
    assert not problems, (
        "Unknown input(s) passed to a local composite action. GitHub only WARNS "
        "about these, so the value is silently dropped and whatever it was meant to "
        "configure never happens:\n  " + "\n  ".join(problems)
    )


def test_fuze_code_action_forwards_every_vendor_key_it_accepts():
    """The specific regression: a vendor key accepted but not forwarded is a
    fallback chain that silently collapses to fewer vendors than configured."""
    action = ACTIONS_DIR / "fuze-code-action" / "action.yml"
    if not action.exists():
        pytest.skip("fuze-code-action not present in this repo")
    doc = _load(action) or {}
    accepted = set((doc.get("inputs") or {}).keys())

    llm_steps = [
        s for s in _iter_steps(doc)
        if isinstance(s.get("uses"), str) and s["uses"].endswith("/llm-endpoint")
    ]
    assert llm_steps, "fuze-code-action no longer calls ./.github/actions/llm-endpoint"
    forwarded = set()
    for s in llm_steps:
        forwarded |= set((s.get("with") or {}).keys())

    missing = []
    for vendor in ("anthropic", "openai", "gemini"):
        if f"{vendor}-api-key" in accepted and f"fallback-{vendor}-key" not in forwarded:
            missing.append(vendor)

    assert not missing, (
        "fuze-code-action accepts a key for "
        f"{missing} but does not forward it to llm-endpoint as fallback-<vendor>-key. "
        "llm-endpoint filters its fallback chain to vendors whose key is non-empty, so "
        "an un-forwarded key removes that vendor from the chain entirely. That is how a "
        "three-vendor gateway degraded to a single vendor and took the fleet's code "
        "review down when that one vendor ran out of credit."
    )
