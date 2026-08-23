"""Static invariants for CI agents routed through the LiteLLM gateway.

The Claude-driven workflows hold no provider key: they point `ANTHROPIC_BASE_URL` at
`litellm.fuzeinfra.svc.cluster.local:4000` and let the gateway own routing, key custody
and cross-provider failover. That buys provider independence, and it introduces one
coupling worth guarding: **the model names the workflow pins must be names the gateway
actually serves.**

That coupling is not theoretical. The original outage ran with no pinning at all, and
Claude Code asked for `claude-opus-5[1m]` — the extended-context marker appended to the
model ID, which no gateway model list contains. Against the gateway that is a 400 on the
first call. A model rename in `helm/litellm/values.yaml` breaks CI the same way, and
neither side's tests would otherwise notice: the chart lints fine, the workflow lints
fine, and the failure only appears at runtime.

Offline: reads two files, no cluster, no network.
"""

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/a2a-maintain.yml"
GATEWAY_VALUES = ROOT / "helm/litellm/values.yaml"
GATEWAY_PROD_VALUES = ROOT / "helm/litellm/values-contabo.yaml"

# Env vars in the workflow whose value must be a model the gateway serves.
MODEL_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)

# Body fields a non-Anthropic upstream rejects when the router falls through to it.
# The first three are Anthropic-only fields the CALLER sends ("Extra inputs are not
# permitted"). `reasoning_effort` is different in kind and must not be dropped from
# this set on the theory that it is redundant: LiteLLM's own OpenAI-Responses bridge
# synthesises it downstream of the other drops, and gpt-4.1 rejects it outright
# ("Unsupported parameter: 'reasoning.effort' is not supported with this model").
REQUIRED_DROPS = {"thinking", "context_management", "output_config", "reasoning_effort"}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _gateway_models() -> list[dict]:
    return yaml.safe_load(GATEWAY_VALUES.read_text())["models"]


def _model_names() -> set[str]:
    return {m["name"] for m in _gateway_models()}


def test_workflow_holds_no_provider_key():
    """The point of the gateway: CI holds a gateway credential, never a provider key."""
    raw = WORKFLOW.read_text()
    assert "secrets.LITELLM_CI_KEY" in raw
    assert "secrets.ANTHROPIC_API_KEY" not in raw, (
        "the workflow is back to holding a provider key directly, which defeats the "
        "gateway's key custody and re-couples CI to a single provider's billing"
    )
    assert "api.anthropic.com" not in raw, "CI must reach providers only via the gateway"


def test_scoped_key_is_preferred_over_the_admin_key():
    """Every place the credential is read must prefer the scoped virtual key.

    `LITELLM_MASTER_KEY` is the gateway ADMIN key — it can mint keys, read the proxy
    config and see every consumer's spend. It stays as a fallback so the migration is
    zero-downtime, but a reference that reads it FIRST (or only) silently puts CI back
    on admin credentials.
    """
    raw = WORKFLOW.read_text()
    for line in raw.splitlines():
        if "secrets.LITELLM_MASTER_KEY" not in line:
            continue
        if line.strip().startswith("#") or "::warning::" in line:
            continue  # prose and the nudge itself may name it
        assert "secrets.LITELLM_CI_KEY || secrets.LITELLM_MASTER_KEY" in line, (
            f"master key read without preferring the scoped key: {line.strip()}"
        )


def test_base_url_points_at_the_in_cluster_gateway():
    env = _workflow()["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://litellm.fuzeinfra.svc.cluster.local:4000"


def test_job_runs_on_the_in_cluster_runner():
    """A hosted runner cannot reach a ClusterIP gateway; the job must run in-cluster."""
    job = _workflow()["jobs"]["a2a-maintain"]
    # "ubuntu-latest" is a TEMPORARY allowance while fuzeinfra-ci-runner-1 is missing
    # its fuzeinfra.io/pool=ci label (VLAN cutover casualty). Revert to == "staging"
    # once the node is relabeled. See fix/restore-staging-runner follow-up PR.
    assert job["runs-on"] in ("staging", "ubuntu-latest"), (
        "the gateway is ClusterIP-only with a NetworkPolicy — from ubuntu-latest every "
        "request times out. `staging` is the ARC scale-set name (a bare string, not a "
        "label; see runners/arc/runner-scale-set-values.yaml). `ubuntu-latest` is only "
        "acceptable as a TEMPORARY workaround while the CI node is being relabeled."
    )


def test_fork_prs_cannot_run_on_the_in_cluster_runner():
    """Fork code + an agent with Bash + a runner inside the cluster is not acceptable."""
    guard = str(_workflow()["jobs"]["a2a-maintain"]["if"])
    assert "head.repo.full_name == github.repository" in guard, (
        "this job checks out the PR head and runs an agent with Bash on an in-cluster "
        "runner; without a same-repo guard a fork PR gets execution inside the cluster"
    )


def test_every_pinned_model_is_served_by_the_gateway():
    env = _workflow()["env"]
    served = _model_names()
    for var in MODEL_VARS:
        assert var in env, f"{var} must be pinned; unpinned aliases resolve to IDs the gateway lacks"
        assert env[var] in served, (
            f"{var}={env[var]!r} is not in the gateway's model list {sorted(served)}. "
            f"Either add it to helm/litellm/values.yaml or pin the workflow to a served name."
        )


def test_no_pinned_model_carries_the_extended_context_suffix():
    """`claude-opus-5[1m]` is what the original failure requested. It is not a model."""
    env = _workflow()["env"]
    for var in MODEL_VARS:
        assert not re.search(r"\[\d+m\]$", str(env[var])), (
            f"{var}={env[var]!r} carries an extended-context suffix; the gateway serves "
            f"no such name. CLAUDE_CODE_DISABLE_1M_CONTEXT should also stay set."
        )
    assert env.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"


def test_fallback_targets_drop_anthropic_only_fields():
    """Without this the fallback chain exists but 400s on hop 2 for Anthropic callers."""
    for model in _gateway_models():
        if model["provider"].startswith("anthropic/"):
            # The primary path must keep full fidelity — dropping here buys nothing.
            assert "extraParams" not in model or not set(
                model["extraParams"].get("additional_drop_params", [])
            ) & REQUIRED_DROPS, (
                f"{model['name']} is an Anthropic model; dropping {REQUIRED_DROPS} from it "
                f"silently downgrades normal operation"
            )
            continue
        if model["name"].startswith("text-embedding"):
            continue  # embeddings are never a chat fallback target
        drops = set(model.get("extraParams", {}).get("additional_drop_params", []))
        assert REQUIRED_DROPS <= drops, (
            f"{model['name']} is a cross-provider fallback target but does not drop "
            f"{sorted(REQUIRED_DROPS - drops)}; an Anthropic-format caller falling through "
            f"to it gets 'Extra inputs are not permitted'"
        )


def test_max_tokens_is_clamped_per_hop():
    """The hops do not share an output ceiling, so an unclamped max_tokens 400s hop 2."""
    settings = yaml.safe_load(GATEWAY_VALUES.read_text())["litellmSettings"]
    assert settings.get("modify_params") is True, (
        "litellm_settings.modify_params is not true, so max_tokens is passed through "
        "unchanged. claude-opus-5 allows 128000 output tokens and gpt-4.1 allows 32768 — "
        "any request above 32768 is legal on the primary and a hard 400 on the fallback, "
        "breaking the chain for exactly the long-output requests worth rescuing. Setting "
        "max_tokens in a deployment's litellm_params does NOT substitute: the router "
        "builds {**litellm_params, ..., **kwargs}, so the caller's value wins."
    )


def test_context_window_overflow_routes_to_a_bigger_window():
    """A hop with a smaller window than the primary silently truncates the caller."""
    router = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]
    assert router.get("enable_pre_call_checks") is True, (
        "enable_pre_call_checks defaults to false, so no deployment's context window is "
        "ever checked and context_window_fallbacks cannot fire."
    )
    overflow = router.get("context_window_fallbacks")
    assert overflow, "no context_window_fallbacks; a ContextWindowExceededError is terminal"
    # Every primary that has a provider-failure chain needs an overflow chain too —
    # the client sizes its context to the alias, and cannot see a swap behind it.
    provider_primaries = {p for entry in router["fallbacks"] for p in entry}
    overflow_primaries = {p for entry in overflow for p in entry}
    assert provider_primaries <= overflow_primaries, (
        f"{sorted(provider_primaries - overflow_primaries)} can fall back on a provider "
        f"error but not on a context overflow"
    )


def test_every_fallback_target_is_a_real_model():
    """LiteLLM silently never routes to a fallback name absent from model_list."""
    served = _model_names()
    router = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]
    for key in ("fallbacks", "context_window_fallbacks"):
        for entry in router.get(key, []):
            for primary, alts in entry.items():
                assert primary in served, f"{key} primary {primary!r} is not in model_list"
                for alt in alts:
                    assert alt in served, f"{key} target {alt!r} is not in model_list"


def test_the_ci_model_has_a_cross_provider_fallback_chain():
    """Provider independence is the whole objective — assert it for the model CI uses."""
    env = _workflow()["env"]
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    chain = next(
        (alts for entry in fallbacks for prim, alts in entry.items() if prim == env["ANTHROPIC_MODEL"]),
        None,
    )
    assert chain, f"{env['ANTHROPIC_MODEL']} has no fallbacks; CI is still single-provider"
    by_model = {m["name"]: m["provider"].split("/", 1)[0] for m in _gateway_models()}
    providers = {by_model[alt] for alt in chain}
    assert len(providers) >= 2, (
        f"fallbacks for {env['ANTHROPIC_MODEL']} only reach {providers}; a single "
        f"alternate provider is one outage away from the same total failure"
    )


def test_runner_namespace_may_reach_the_gateway_in_prod():
    prod = yaml.safe_load(GATEWAY_PROD_VALUES.read_text())
    allowed = prod["networkPolicy"]["allowedNamespaces"]
    assert "arc-runners" in allowed, (
        "the ARC runner pods live in arc-runners; without it on the allowlist the "
        "NetworkPolicy drops every CI request and the preflight times out"
    )


def _mint_script() -> str:
    return (ROOT / "scripts/mint-litellm-ci-key.sh").read_text()


def _key_allowlist() -> set[str]:
    """The `models` array the mint script sends to /key/generate."""
    raw = _mint_script()
    body = raw.split("MODELS='[", 1)[1].split("]'", 1)[0]
    return {line.strip().strip('",') for line in body.splitlines() if line.strip().strip('",')}


def test_key_allowlist_covers_every_model_the_workflow_pins():
    env = _workflow()["env"]
    allow = _key_allowlist()
    for var in MODEL_VARS:
        assert env[var] in allow, (
            f"{var}={env[var]!r} is not on the virtual key's model allowlist, so the key "
            f"would be refused for it. Add it to MODELS in scripts/mint-litellm-ci-key.sh."
        )


def test_key_allowlist_covers_every_fallback_hop():
    """The subtle one: a key allowed only the Claude names breaks failover.

    `models` is enforced on the model actually dispatched, so such a key passes in
    normal operation and is rejected at the precise moment the router fails over —
    converting the cross-provider fallback into an outage on the one day it matters.
    """
    env = _workflow()["env"]
    allow = _key_allowlist()
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    pinned = {env[v] for v in MODEL_VARS}
    for entry in fallbacks:
        for primary, alts in entry.items():
            if primary not in pinned:
                continue  # CI never asks for it, so its hops are irrelevant here
            for alt in alts:
                assert alt in allow, (
                    f"{primary!r} falls back to {alt!r} but the virtual key does not allow "
                    f"{alt!r} — failover would be rejected by the key's own ACL"
                )


def test_mint_script_sets_no_rate_limits():
    """rpm/tpm limits on a key poison every provider payload (BerriAI/litellm#28146).

    The parallel_request_limiter_v3 hook injects `_litellm_*` params into the OUTBOUND
    request whenever a key carries rate limits; OpenAI answers "Unrecognized request
    arguments" and Anthropic "Extra inputs are not permitted". That breaks not just
    throttled calls but every fallback hop — the machinery this repo added precisely to
    survive a dead provider.
    """
    raw = _mint_script()
    body = raw.split("BODY=$(cat <<JSON", 1)[1].split("JSON", 1)[0]
    for forbidden in ("rpm_limit", "tpm_limit", "max_parallel_requests"):
        assert forbidden not in body, (
            f"{forbidden} is set on the CI virtual key; see BerriAI/litellm#28146 before "
            f"adding it, and verify a fallback hop still completes"
        )


def test_mint_script_sets_a_budget_and_a_stable_alias():
    """Budget is the control that still works, and the alias is the cost-attribution key."""
    raw = _mint_script()
    assert 'max_budget' in raw and 'budget_duration' in raw
    assert 'ALIAS="a2a-maintain-ci"' in raw, (
        "the alias is what the gateway's spend report groups by; changing it silently "
        "splits this consumer's cost history in two"
    )


def test_manifest_and_workflow_agree_the_check_is_non_blocking():
    """a2a-maintain must stay off requiredChecks while it depends on cluster reachability."""
    manifest = json.loads((ROOT / ".fuze/manifest.json").read_text())
    required = manifest["hardening"]["requiredChecks"]
    assert "a2a-maintain" not in required, (
        "a2a-maintain now depends on in-cluster gateway reachability; making it a "
        "required check would block every merge on the gateway being up"
    )
