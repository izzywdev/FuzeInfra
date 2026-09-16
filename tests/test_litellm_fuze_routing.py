"""Static invariants for the @fuze handler routed through the LiteLLM gateway.

Mirrors test_litellm_ci_routing.py, but targets fuze.yml and
scripts/mint-litellm-fuze-key.sh instead of a2a-maintain.yml and
mint-litellm-ci-key.sh.

WHY THIS FILE CHANGED SHAPE, AND WHY IT ISN'T A REVERT. This suite used to read
`ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_*` /
`CLAUDE_CODE_DISABLE_*` out of `jobs.fuze.env` and required `runs-on: staging`.
That was true of the FuzeInfra-only fork of this file (#560, 2026-08-19). PR #836
(2026-09-02) replaced that fork with the stamped FuzeSDLC canonical — the SAME
migration test_litellm_ci_routing.py already documents for a2a-maintain.yml — and
this suite was never updated to match, so it failed on every push since. The
canonical `fuze.yml` is the UNPRIVILEGED baseline installed in every onboarded
repo: it holds no env block, no model pins, and runs on `ubuntu-latest` on
purpose (pinning it to a self-hosted pool that exists only in this repo would
queue forever in the other 21). It resolves the gateway through
`./.github/actions/fuze-code-action` -> `./.github/actions/llm-endpoint`, which
probes generically and falls back to a direct vendor key.

The old fork's shape (in-cluster env pins, `runs-on: staging`) didn't disappear —
it lives on, by design, in the two workflows that genuinely run in-cluster and
are deliberately `# fuze:fork` (never reconciled from the canonical):
`governance-nightly.yml` and `nightly-integration.yml`. Neither of those is the
`@fuze` mention handler this suite is about.

So, like test_litellm_ci_routing.py, the source of truth for "which models the
@fuze handler may ask the gateway for" is now the fuze-handler VIRTUAL KEY's own
allowlist: the `MODELS` array in scripts/mint-litellm-fuze-key.sh. Reading the
ACL directly means this suite cannot be satisfied by a pin the gateway will
refuse.

Offline: reads local files only, no cluster, no network.
"""

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/fuze.yml"
GATEWAY_VALUES = ROOT / "helm/litellm/values.yaml"
GATEWAY_PROD_VALUES = ROOT / "helm/litellm/values-contabo.yaml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _gateway_models() -> list[dict]:
    return yaml.safe_load(GATEWAY_VALUES.read_text())["models"]


def _model_names() -> set[str]:
    return {m["name"] for m in _gateway_models()}


def _mint_script() -> str:
    return (ROOT / "scripts/mint-litellm-fuze-key.sh").read_text()


def _key_allowlist() -> set[str]:
    raw = _mint_script()
    body = raw.split("MODELS='[", 1)[1].split("]'", 1)[0]
    return {line.strip().strip('",') for line in body.splitlines() if line.strip().strip('",')}


# ── Canonical, not a re-fork ────────────────────────────────────────────────

def test_the_workflow_is_the_stamped_canonical_not_a_local_fork():
    """A fork of this file is exactly what produced the stale-pin drift this suite
    hit: the fork's env/runner shape was baked in, the canonical moved on, and
    nothing reconciled it. The `fuze:managed` marker is what keeps governance-sync
    reconciling it instead of it drifting again."""
    first = WORKFLOW.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# fuze:managed template=fuze.yml"), (
        f"fuze.yml is no longer stamped from the FuzeSDLC canonical: {first!r}. "
        "Editing it here re-forks it; change the upstream template instead."
    )


# ── Workflow holds no provider admin key, no direct provider call ──────────

def test_workflow_never_calls_a_provider_directly_or_holds_the_admin_key():
    """The handler must reach providers only via fuze-code-action (which resolves
    the gateway through ./llm-endpoint), and must never hold the gateway's own
    admin key. Holding `secrets.ANTHROPIC_API_KEY` is fine and intentional here —
    it is the documented direct-vendor FALLBACK for when the gateway probe fails,
    forwarded to llm-endpoint rather than used to call a provider inline."""
    raw = WORKFLOW.read_text()
    assert "uses: ./.github/actions/fuze-code-action" in raw
    assert "secrets.LITELLM_FUZE_KEY" in raw
    assert "api.anthropic.com" not in raw, (
        "fuze.yml must reach providers only via fuze-code-action/llm-endpoint, "
        "never call a provider endpoint directly"
    )
    assert "secrets.LITELLM_MASTER_KEY" not in raw, (
        "LITELLM_MASTER_KEY is the gateway ADMIN key — it can mint keys, read the "
        "proxy config and see every consumer's spend. The @fuze handler needs none "
        "of that; it should hold only the scoped LITELLM_FUZE_KEY."
    )


# ── Gateway addressing ───────────────────────────────────────────────────────

def test_gateway_base_url_is_configurable_not_hardcoded_standalone():
    """fuze.yml is installed on a hosted runner in every onboarded repo, so the
    in-cluster DNS name can only ever be a DEFAULT, never the sole literal — a
    verbatim in-cluster URL would fail DNS everywhere but FuzeInfra. It must stay
    overridable via `vars.FUZE_LITELLM_BASE_URL`, falling back to the in-cluster
    host (harmless elsewhere because llm-endpoint falls back to a vendor key when
    the probe fails)."""
    raw = WORKFLOW.read_text()
    assert "vars.FUZE_LITELLM_BASE_URL" in raw, (
        "the gateway URL must be overridable per-repo via a variable, not a bare literal"
    )
    assert "http://litellm.fuzeinfra.svc.cluster.local:4000" in raw, (
        "the in-cluster gateway must stay the default when the var is unset"
    )


# ── Runner class ─────────────────────────────────────────────────────────────

def test_job_runs_on_the_unprivileged_hosted_runner():
    """`fuze.yml` is the baseline `@fuze` entrypoint installed fleet-wide; the
    cluster-capable variant that must run on `staging` lives in fuze-cluster.yml,
    opt-in per-repo. Pinning THIS file to a self-hosted pool that exists only in
    FuzeInfra would queue forever in every other onboarded repo
    (governance/ci-runners.md) and would hand a privileged runner label to repos
    that never asked for it."""
    job = _workflow()["jobs"]["fuze"]
    assert job["runs-on"] == "ubuntu-latest"


# ── Key allowlist reflects what the gateway actually serves ────────────────

def test_every_model_the_fuze_key_may_request_is_served_by_the_gateway():
    """The virtual key's ACL is what the gateway enforces at dispatch, so it is
    the set that matters — see the module docstring for why this no longer reads
    a workflow env block."""
    served = _model_names()
    allow = _key_allowlist()
    assert allow, "the mint script's MODELS array is empty; @fuze could request nothing"
    for name in sorted(allow):
        assert name in served, (
            f"{name!r} is on the fuze-handler virtual key's allowlist but not in the "
            f"gateway's model list {sorted(served)}. Either add it to "
            f"helm/litellm/values.yaml or drop it from MODELS in "
            f"scripts/mint-litellm-fuze-key.sh."
        )


def test_no_allowed_model_carries_the_extended_context_suffix():
    """`claude-opus-5[1m]` is what the original outage requested. It is not a model."""
    for name in sorted(_key_allowlist()):
        assert not re.search(r"\[\d+m\]$", name), (
            f"{name!r} carries an extended-context suffix; the gateway serves no such name."
        )


def test_key_allowlist_covers_every_fallback_hop():
    """A key allowed only the Claude names breaks failover — the subtle invariant.

    `models` is enforced on the model actually dispatched, so such a key passes in
    normal operation and is rejected at the exact moment the router fails over —
    converting the cross-provider fallback into an outage on the one day it matters.
    """
    allow = _key_allowlist()
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    for entry in fallbacks:
        for primary, alts in entry.items():
            if primary not in allow:
                continue  # @fuze cannot ask for it, so its hops are irrelevant here
            for alt in alts:
                assert alt in allow, (
                    f"{primary!r} falls back to {alt!r} but the fuze-handler key "
                    f"does not allow {alt!r} — failover would be rejected by the "
                    f"key's own ACL"
                )


def test_mint_script_sets_no_rate_limits():
    """rpm/tpm limits poison every provider payload (BerriAI/litellm#28146)."""
    raw = _mint_script()
    body = raw.split("BODY=$(cat <<JSON", 1)[1].split("JSON", 1)[0]
    for forbidden in ("rpm_limit", "tpm_limit", "max_parallel_requests"):
        assert forbidden not in body, (
            f"{forbidden} is set on the fuze-handler virtual key; see "
            f"BerriAI/litellm#28146 before adding it"
        )


def test_mint_script_sets_a_budget_and_a_stable_alias():
    raw = _mint_script()
    assert "max_budget" in raw and "budget_duration" in raw
    assert 'ALIAS="fuze-handler"' in raw, (
        "the alias is the cost-attribution key in the gateway's spend report; "
        "changing it silently splits this consumer's cost history"
    )


def test_fuze_key_alias_differs_from_ci_key_alias():
    """Two separate virtual keys — separate budgets and separate spend attribution."""
    fuze_raw = _mint_script()
    ci_raw = (ROOT / "scripts/mint-litellm-ci-key.sh").read_text()
    fuze_alias = next(l.split('"')[1] for l in fuze_raw.splitlines() if l.startswith('ALIAS="'))
    ci_alias = next(l.split('"')[1] for l in ci_raw.splitlines() if l.startswith('ALIAS="'))
    assert fuze_alias != ci_alias, (
        f"fuze-handler and a2a-maintain-ci share the alias {fuze_alias!r}; "
        f"they must have distinct aliases for separate cost attribution"
    )
