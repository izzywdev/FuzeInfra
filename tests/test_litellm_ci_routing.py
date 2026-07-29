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

# Anthropic-only body fields a non-Anthropic upstream rejects with
# "Extra inputs are not permitted" when the router falls through to it.
REQUIRED_DROPS = {"thinking", "context_management", "output_config"}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _gateway_models() -> list[dict]:
    return yaml.safe_load(GATEWAY_VALUES.read_text())["models"]


def _model_names() -> set[str]:
    return {m["name"] for m in _gateway_models()}


def test_workflow_holds_no_provider_key():
    """The point of the gateway: CI holds a gateway credential, never a provider key."""
    raw = WORKFLOW.read_text()
    assert "secrets.LITELLM_MASTER_KEY" in raw
    assert "secrets.ANTHROPIC_API_KEY" not in raw, (
        "the workflow is back to holding a provider key directly, which defeats the "
        "gateway's key custody and re-couples CI to a single provider's billing"
    )
    assert "api.anthropic.com" not in raw, "CI must reach providers only via the gateway"


def test_base_url_points_at_the_in_cluster_gateway():
    env = _workflow()["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://litellm.fuzeinfra.svc.cluster.local:4000"


def test_job_runs_on_the_in_cluster_runner():
    """A hosted runner cannot reach a ClusterIP gateway; the job must run in-cluster."""
    job = _workflow()["jobs"]["a2a-maintain"]
    assert job["runs-on"] == "staging", (
        "the gateway is ClusterIP-only with a NetworkPolicy — from ubuntu-latest every "
        "request times out. `staging` is the ARC scale-set name (a bare string, not a "
        "label; see runners/arc/runner-scale-set-values.yaml)."
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


def test_every_fallback_target_is_a_real_model():
    """LiteLLM silently never routes to a fallback name absent from model_list."""
    served = _model_names()
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    for entry in fallbacks:
        for primary, alts in entry.items():
            assert primary in served, f"fallback primary {primary!r} is not in model_list"
            for alt in alts:
                assert alt in served, f"fallback target {alt!r} is not in model_list"


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


def test_manifest_and_workflow_agree_the_check_is_non_blocking():
    """a2a-maintain must stay off requiredChecks while it depends on cluster reachability."""
    manifest = json.loads((ROOT / ".fuze/manifest.json").read_text())
    required = manifest["hardening"]["requiredChecks"]
    assert "a2a-maintain" not in required, (
        "a2a-maintain now depends on in-cluster gateway reachability; making it a "
        "required check would block every merge on the gateway being up"
    )
