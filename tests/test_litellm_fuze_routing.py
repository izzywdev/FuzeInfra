"""Static invariants for the @fuze handler routed through the LiteLLM gateway.

Mirrors test_litellm_ci_routing.py, but targets fuze.yml and
scripts/mint-litellm-fuze-key.sh instead of a2a-maintain.yml and
mint-litellm-ci-key.sh.

Offline: reads local files only, no cluster, no network.
"""

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/fuze.yml"
GATEWAY_VALUES = ROOT / "helm/litellm/values.yaml"
GATEWAY_PROD_VALUES = ROOT / "helm/litellm/values-contabo.yaml"

MODEL_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)


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


# ── Workflow holds no provider key ──────────────────────────────────────────

def test_workflow_holds_no_provider_key():
    """The fuze handler must hold only a gateway credential, never a provider key."""
    raw = WORKFLOW.read_text()
    assert "secrets.LITELLM_FUZE_KEY" in raw
    assert "secrets.ANTHROPIC_API_KEY" not in raw, (
        "fuze.yml is back to holding a provider key directly, which defeats the "
        "gateway's key custody and re-couples CI to a single provider's billing"
    )
    assert "api.anthropic.com" not in raw, (
        "fuze.yml must reach providers only via the LiteLLM gateway"
    )


# ── Gateway addressing ───────────────────────────────────────────────────────

def test_base_url_points_at_the_in_cluster_gateway():
    env = _workflow()["jobs"]["fuze"]["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://litellm.fuzeinfra.svc.cluster.local:4000"


def test_job_runs_on_the_in_cluster_runner():
    """Hosted runners cannot reach a ClusterIP gateway."""
    job = _workflow()["jobs"]["fuze"]
    assert job["runs-on"] == "staging", (
        "the LiteLLM gateway is ClusterIP-only — from ubuntu-latest every request "
        "times out; `staging` is the in-cluster ARC runner"
    )


# ── Model pinning ────────────────────────────────────────────────────────────

def test_every_pinned_model_is_served_by_the_gateway():
    env = _workflow()["jobs"]["fuze"]["env"]
    served = _model_names()
    for var in MODEL_VARS:
        assert var in env, f"{var} must be pinned in fuze.yml env"
        assert env[var] in served, (
            f"{var}={env[var]!r} is not in the gateway's model list {sorted(served)}. "
            f"Either add it to helm/litellm/values.yaml or pin to a served name."
        )


def test_no_pinned_model_carries_the_extended_context_suffix():
    env = _workflow()["jobs"]["fuze"]["env"]
    for var in MODEL_VARS:
        assert not re.search(r"\[\d+m\]$", str(env[var])), (
            f"{var}={env[var]!r} carries an extended-context suffix; the gateway "
            f"serves no such name. CLAUDE_CODE_DISABLE_1M_CONTEXT must stay set."
        )
    assert env.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"


def test_experimental_betas_disabled():
    """Non-Anthropic upstreams reject Anthropic-only beta capability fields."""
    env = _workflow()["jobs"]["fuze"]["env"]
    assert env.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS") == "1"
    assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"


# ── Key allowlist ────────────────────────────────────────────────────────────

def test_key_allowlist_covers_every_model_the_workflow_pins():
    env = _workflow()["jobs"]["fuze"]["env"]
    allow = _key_allowlist()
    for var in MODEL_VARS:
        assert env[var] in allow, (
            f"{var}={env[var]!r} is not on the fuze-handler virtual key's model "
            f"allowlist, so the key would be refused for it. Add it to MODELS in "
            f"scripts/mint-litellm-fuze-key.sh."
        )


def test_key_allowlist_covers_every_fallback_hop():
    """A key allowed only the Claude names breaks failover — the subtle invariant."""
    env = _workflow()["jobs"]["fuze"]["env"]
    allow = _key_allowlist()
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    pinned = {env[v] for v in MODEL_VARS}
    for entry in fallbacks:
        for primary, alts in entry.items():
            if primary not in pinned:
                continue
            for alt in alts:
                assert alt in allow, (
                    f"{primary!r} falls back to {alt!r} but the fuze-handler key "
                    f"does not allow {alt!r} — failover would be rejected by the key's ACL"
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
