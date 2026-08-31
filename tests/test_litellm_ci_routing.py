"""Static invariants for CI agents routed through the LiteLLM gateway.

CI agents reach providers through the gateway, which owns routing, key custody and
cross-provider failover. That buys provider independence, and it introduces one coupling
worth guarding: **the model names CI can ask for must be names the gateway actually
serves.**

That coupling is not theoretical. The original outage ran with no pinning at all, and
Claude Code asked for `claude-opus-5[1m]` — the extended-context marker appended to the
model ID, which no gateway model list contains. Against the gateway that is a 400 on the
first call. A model rename in `helm/litellm/values.yaml` breaks CI the same way, and
neither side's tests would otherwise notice: the chart lints fine, the workflow lints
fine, and the failure only appears at runtime.

WHERE THE PINNED SET NOW COMES FROM, AND WHY IT MOVED. It used to be read out of
`a2a-maintain.yml`'s own `env:` block — `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_*` — because
this repo ran a FORK of that workflow which hardcoded them alongside an in-cluster
`ANTHROPIC_BASE_URL` and `runs-on: staging`. That fork is gone. The workflow is now the
stamped FuzeSDLC canonical (`# fuze:managed`), which resolves its endpoint through
`./.github/actions/llm-endpoint` and therefore holds no model pins, no gateway URL and no
runner class of its own. All three were FuzeInfra-only — this repo hosts the gateway — and
did not belong in a template every repo installs.

So the source of truth for "which models can CI ask the gateway for" is now the VIRTUAL
KEY's own allowlist: the `MODELS` array in `scripts/mint-litellm-ci-key.sh`. That is a
strictly better anchor than a workflow env block ever was, because the key's ACL is the
thing that actually rejects a dispatch — a name present in the workflow but absent from the
ACL was always the real defect, and reading the ACL directly means this suite cannot be
satisfied by a pin the gateway will refuse.

The workflow assertions that remain are about the merged contract itself: that the
canonical's gating has not been re-forked back into skip-green paths, and that the
FuzeInfra-only hardcoding has not crept back in.

Offline: reads a few files, no cluster, no network.
"""

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/a2a-maintain.yml"
GATEWAY_VALUES = ROOT / "helm/litellm/values.yaml"
GATEWAY_PROD_VALUES = ROOT / "helm/litellm/values-contabo.yaml"

# The FuzeInfra-only hardcoding the merged canonical must never reacquire. Each of these
# was in this repo's fork and is now the gateway host's business, not a consumer template's.
FORK_ONLY = (
    ("ANTHROPIC_BASE_URL", "the gateway URL — ./.github/actions/llm-endpoint probes it "
                           "generically and falls back when it is unreachable"),
    ("litellm.fuzeinfra.svc.cluster.local", "in-cluster service DNS, true only in this repo"),
    ("runs-on: staging", "a pinned runner class — render.py rewrites runs-on: from the "
                         "repo's declared ci.runner and this job is not RUNNER_EXEMPT"),
    ("helm/litellm", "chart paths that exist only in the gateway host"),
)

# Verdict paths from the fork that went GREEN while a declared A2A surface went
# unmaintained. Deleting them is the entire point of the merge; asserted on the strings
# the fork actually shipped rather than on a paraphrase.
SKIP_GREEN_PATHS = (
    ("LITELLM_CI_KEY not set", "gating on ONE secret's name, so a repo on the fallback "
                               "vendor read as uncredentialed"),
    ("Skip if gateway key absent", "the guard step itself"),
    ("A2A drift is NOT being checked on this PR", "the warn-and-pass verdict"),
    ("provider billing/budget error detected", "billing errors treated as skippable"),
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


def _workflow_live() -> str:
    """The workflow with comment lines stripped.

    Its header DOCUMENTS the removed skip paths and the un-promoted FuzeInfra-only bits by
    name, on purpose — that history is why they are banned. The ban is on the BEHAVIOUR, so
    every assertion below reads the executable half. A suite that forbade the explanation
    too would be one you "fix" by deleting the explanation.
    """
    return "\n".join(
        ln for ln in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_the_workflow_is_the_stamped_canonical_not_a_local_fork():
    """A fork of this file is what produced two jobs with the SAME id `a2a-maintain`, so
    branch protection saw one indistinguishable status-check context while both copies
    raced to push `[skip a2a]` commits to the same PR branch. The `fuze:managed` marker is
    what keeps governance-sync reconciling it instead of it drifting again."""
    first = WORKFLOW.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# fuze:managed template=a2a-maintain.yml"), (
        f"a2a-maintain.yml is no longer stamped from the FuzeSDLC canonical: {first!r}. "
        "Editing it here re-forks it; change workflow-templates/a2a-maintain.yml upstream."
    )


def test_no_skip_green_path_came_back():
    """The merged contract: `a2a.enabled` undeclared -> skip (no surface exists, so
    skipping is the truth); declared with NO usable credential -> FAIL; declared with a
    usable credential (LiteLLM OR fallback) -> run. Nothing else may pass by not running.

    Note in particular that an unreachable gateway is NOT a skip. It is a FALLBACK TRIGGER:
    llm-endpoint degrades to a direct vendor, because recovering the cluster may itself
    require an agent run, so the agent path must not depend on the cluster being up."""
    live = _workflow_live()
    for fragment, why in SKIP_GREEN_PATHS:
        assert fragment not in live, f"skip-green path is back ({why}): {fragment!r}"


def test_the_credential_gate_is_on_resolution_not_on_one_secrets_name():
    live = _workflow_live()
    assert "uses: ./.github/actions/llm-endpoint" in live, (
        "the gate must be whether llm-endpoint resolved ANY usable credential"
    )
    assert "secrets.LITELLM_CI_KEY" not in live, (
        "gating on LITELLM_CI_KEY by name is what made a repo running on a configured "
        "fallback vendor read as uncredentialed and skip green"
    )


def test_no_provider_key_or_gateway_url_is_hardcoded_in_the_workflow():
    """Key custody stays with llm-endpoint/the gateway. The workflow names secrets to PASS
    to that action; what it must not do is address a provider directly."""
    live = _workflow_live()
    assert "api.anthropic.com" not in live, (
        "the workflow reaches a provider directly instead of through llm-endpoint"
    )
    assert "secrets.LITELLM_MASTER_KEY" not in live, (
        "LITELLM_MASTER_KEY is the gateway ADMIN key — it can mint keys, read the proxy "
        "config and see every consumer's spend. CI needs none of that."
    )


def test_no_fuzeinfra_only_hardcoding_was_promoted_into_the_shared_template():
    live = _workflow_live()
    for fragment, why in FORK_ONLY:
        assert fragment not in live, (
            f"{fragment!r} is FuzeInfra-only ({why}) and this file is installed verbatim "
            f"in every repo that declares an a2a surface"
        )


def test_fork_prs_get_no_credential_and_never_start():
    """The old guard was `head.repo.full_name == github.repository`, needed because the
    fork ran an agent with Bash on an IN-CLUSTER runner. The canonical closes the same case
    structurally instead: the trigger is `pull_request` (not `pull_request_target`), so a
    fork PR is handed no secrets at all, llm-endpoint resolves nothing, and the fail-closed
    preflight stops the job before the maintainer runs."""
    wf = _workflow()
    triggers = wf[True] if True in wf else wf["on"]  # PyYAML parses bare `on:` as True
    assert "pull_request_target" not in triggers, (
        "pull_request_target hands fork PRs this repo's secrets AND write scope; the "
        "fail-closed preflight stops being a boundary"
    )
    assert "pull_request" in triggers


def test_every_model_ci_may_request_is_served_by_the_gateway():
    """The virtual key's ACL is what the gateway enforces at dispatch, so it is the set
    that matters — see the module docstring for why this no longer reads a workflow env."""
    served = _model_names()
    allow = _key_allowlist()
    assert allow, "the mint script's MODELS array is empty; CI could request nothing"
    for name in sorted(allow):
        assert name in served, (
            f"{name!r} is on the CI virtual key's allowlist but not in the gateway's model "
            f"list {sorted(served)}. Either add it to helm/litellm/values.yaml or drop it "
            f"from MODELS in scripts/mint-litellm-ci-key.sh."
        )


def test_no_allowed_model_carries_the_extended_context_suffix():
    """`claude-opus-5[1m]` is what the original failure requested. It is not a model."""
    for name in sorted(_key_allowlist()):
        assert not re.search(r"\[\d+m\]$", name), (
            f"{name!r} carries an extended-context suffix; the gateway serves no such name."
        )


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


def test_every_anthropic_model_ci_may_request_has_a_cross_provider_fallback_chain():
    """Provider independence is the whole objective — assert it for every model CI can
    actually dispatch, not just one. A key allowed three Claude names of which only one has
    a chain is single-provider on the other two, and nothing would say so."""
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    by_model = {m["name"]: m["provider"].split("/", 1)[0] for m in _gateway_models()}
    anthropic_allowed = [n for n in sorted(_key_allowlist()) if by_model[n] == "anthropic"]
    assert anthropic_allowed, "the CI key allows no Anthropic model; check MODELS"
    for name in anthropic_allowed:
        chain = next(
            (alts for entry in fallbacks for prim, alts in entry.items() if prim == name),
            None,
        )
        assert chain, f"{name} has no fallbacks; CI is still single-provider on it"
        providers = {by_model[alt] for alt in chain}
        assert len(providers) >= 2, (
            f"fallbacks for {name} only reach {providers}; a single alternate provider is "
            f"one outage away from the same total failure"
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


def test_key_allowlist_covers_every_fallback_hop():
    """The subtle one: a key allowed only the Claude names breaks failover.

    `models` is enforced on the model actually dispatched, so such a key passes in
    normal operation and is rejected at the precise moment the router fails over —
    converting the cross-provider fallback into an outage on the one day it matters.
    """
    allow = _key_allowlist()
    fallbacks = yaml.safe_load(GATEWAY_VALUES.read_text())["routerSettings"]["fallbacks"]
    for entry in fallbacks:
        for primary, alts in entry.items():
            if primary not in allow:
                continue  # CI cannot ask for it, so its hops are irrelevant here
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
    """a2a-maintain must stay off requiredChecks — for a different reason than it used to.

    The old reason was cluster reachability: the fork could only reach a ClusterIP gateway
    from an in-cluster runner, so requiring it blocked every merge on the gateway being up.
    The canonical removed that dependency (llm-endpoint falls back to a direct vendor), but
    the check is still ACTOR-GATED: claude-code-action refuses a Bot actor, and
    governance-sync's commit-back flips the triggering actor User -> Bot on most PRs here.
    Red then means "an agent opened this PR", not "this PR is bad"
    (governance/required-checks.json says the same, measured on 1 of 4 PR heads).
    """
    manifest = json.loads((ROOT / ".fuze/manifest.json").read_text())
    required = manifest["hardening"]["requiredChecks"]
    assert "a2a-maintain" not in required, (
        "a2a-maintain is actor-gated — it goes red merely because the PR was opened by a "
        "bot — so requiring it would block agent PRs on something the PR cannot fix"
    )
