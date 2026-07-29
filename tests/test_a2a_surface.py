"""Static invariants for this repo's A2A (agent-to-agent) surface.

FuzeInfra is deliberately NOT an A2A agent in its own right — the shared platform is
consumed as a submodule / network + namespace, not asked to do work (FuzeSDLC
`governance/a2a-dependency-graph.md`). What it DOES host is the four executive roles
(`ceo`, `cto`, `cfo`, `ciso`), which the frozen contract projects as one card each with
tenant `Exec-<role>` (FuzeAgent `agent-templates/contracts/a2a/v1/card-projection.md` §5
names this repo's role files as the discriminator).

Those two facts are in tension, and the tension is entirely carried by
`.fuze/manifest.json` `a2a.servingRoles`. The projection's default source set is *every*
role under `agent-templates/roles/` — so with `servingRoles` absent this repo publishes a
13-skill "FuzeInfra agent" card offering backend/frontend/qa/devops/marketing/… to any
allowlisted caller. Naming exactly the four exec roles is what suppresses that card: they
are all exec, exec roles never project into the product card, and an empty product-skill
set means no product card is emitted at all.

These tests exist because the drift is silent — nothing fails, a card simply appears — and
because the `a2a-maintain` agent that would otherwise catch it depends on a funded
Anthropic account. They need no cluster and no network.
"""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]

# The roles the A2A card projection treats as executive, via metadata.tier == "executive".
EXEC_ROLES = {"ceo", "cto", "cfo", "ciso"}


def _manifest() -> dict:
    return json.loads((ROOT / ".fuze/manifest.json").read_text())


def _role(key: str) -> dict:
    return json.loads((ROOT / "agent-templates/roles" / key / "role.json").read_text())


def test_manifest_declares_an_a2a_block():
    a2a = _manifest().get("a2a")
    assert a2a is not None, "the a2a block is the A2A surface; absent means unconfigured"
    assert a2a.get("enabled") is False, (
        "FuzeInfra publishes no agent surface of its own; the exec tenants are enabled "
        "per-tenant in FuzeAgent's a2a-shared values, not here"
    )
    # card-projection.md §5.4 — exec agents are in-cluster only, never tunnel-published.
    assert a2a.get("external") is False


def test_serving_roles_are_exactly_the_exec_roles():
    """The load-bearing line: this is what suppresses the FuzeInfra product card."""
    serving = _manifest()["a2a"].get("servingRoles")
    assert serving, "absent/empty servingRoles falls back to ALL roles -> a product card"
    assert set(serving) == EXEC_ROLES, (
        f"servingRoles must be exactly the exec roles, got {sorted(serving)}. Adding a "
        f"non-exec role here publishes a FuzeInfra product card offering that role's "
        f"capability to callers."
    )


def test_every_serving_role_exists_and_is_executive():
    """A serving role that is absent, undescribed, or non-exec breaks the projection."""
    for key in _manifest()["a2a"]["servingRoles"]:
        path = ROOT / "agent-templates/roles" / key / "role.json"
        assert path.is_file(), f"servingRoles names {key!r} but {path} does not exist"
        role = _role(key)
        assert role.get("metadata", {}).get("tier") == "executive", (
            f"{key!r} is in servingRoles but not metadata.tier=executive, so it would "
            f"project into a FuzeInfra product card instead of an Exec-{key} card"
        )
        # An undescribed skill is unroutable; the generator refuses to emit a placeholder.
        assert role.get("name"), f"{key!r} has no name; cannot project a skill"
        assert role.get("description"), f"{key!r} has no description; projection would fail"


def test_no_manifest_entry_role_overrides_the_exec_derivation():
    """`entryRole` here would break every exec tenant.

    The adapter derives an exec tenant's entry role from the tenant name
    (`Exec-cto` -> `cto`) only when no skill id and no `entryRole` are set. A manifest
    `entryRole` takes precedence — and the repo's coordinators live in
    `agent-templates/coordinator/`, which the projection loader never reads, so naming
    one resolves to no role at all and the dispatch is rejected.
    """
    assert "entryRole" not in _manifest()["a2a"]


def test_provides_to_is_declared_and_fail_closed():
    """`providesTo` is the authoritative allowlist and lives in the CALLEE's manifest.

    Absent and empty both DENY, but they are not the same statement: absent is
    unconfigured, empty is "accept no agent callers" (authz.md §3). Declaring it empty
    is the deliberate staged state — the exec tier's grants land in the same PR that
    flips `a2a.enabled`, per the rollout order in the dependency graph.
    """
    manifest = _manifest()
    assert "providesTo" in manifest, "absent providesTo is unconfigured, not a decision"
    assert manifest["providesTo"] == [], (
        "widening providesTo grants real callers access to the exec agents — it belongs "
        "in the exec-tier rollout PR alongside a2a.enabled and the FuzeAgent tenant entries"
    )


def test_role_manifests_carry_no_a2a_block_yet():
    """The role-level `a2a` block is blocked upstream, not forgotten.

    `agent-templates/schema/role-manifest.schema.json` is `additionalProperties: false`
    and has no `a2a` property; it is canonical in FuzeSDLC and reconciled by
    governance-sync, so adding the block here would be both schema-invalid and reverted.
    Every field in it has a derived default, so the cards project fine without it — the
    only loss is skill `examples`/`scopes` for discoverability. When FuzeSDLC lands the
    schema property, this test is the reminder to backfill them.
    """
    schema = json.loads((ROOT / "agent-templates/schema/role-manifest.schema.json").read_text())
    if "a2a" in schema.get("properties", {}):
        for key in sorted(EXEC_ROLES):
            assert "a2a" in _role(key), (
                f"role-manifest.schema.json now defines `a2a` — backfill {key}'s "
                f"a2a.examples/scopes for card discoverability"
            )
    else:
        for key in sorted(EXEC_ROLES):
            assert "a2a" not in _role(key), (
                f"{key}/role.json has an `a2a` block but the role schema still forbids it"
            )
