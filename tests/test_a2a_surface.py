"""Static invariants for this repo's A2A (agent-to-agent) surface.

FuzeInfra hosts FIVE tenants: the four executive roles (`ceo`, `cto`, `cfo`, `ciso`),
projected by the frozen contract as one card each with tenant `Exec-<role>` (FuzeAgent
`agent-templates/contracts/a2a/v1/card-projection.md` section 5 names this repo's role
files as the discriminator) -- and, since platform decision 2026-09-04, a genuine PRODUCT
tenant, `infra-platform`, offering read-only cluster query today and a
described-but-not-yet-wired data-tier provisioning capability
(`.claude/skills/fuzeinfra-platform-expert`). This is a deliberate reversal of the prior
exec-only policy, not drift back toward it.

The projection's default source set is *every* role under `agent-templates/roles/` -- so
with `servingRoles` absent or wrong this repo could publish either NOTHING (if it fell back
to empty) or an unintended wide surface (if a role landed here that nobody meant to expose).
The guard this file enforces changed shape accordingly: not "servingRoles must be exactly
the four exec roles" (that policy is retired), but "servingRoles must be exactly the four
exec roles plus the one deliberate product role, and nothing else can arrive silently."

These tests exist because the drift is silent — nothing fails, a card simply appears or
disappears — and because the `a2a-maintain` agent that would otherwise catch it depends on
a funded Anthropic account. They need no cluster and no network.
"""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]

# The roles the A2A card projection treats as executive, via metadata.tier == "executive".
EXEC_ROLES = {"ceo", "cto", "cfo", "ciso"}

# The one deliberate non-exec role FuzeInfra serves -- its presence is what makes the
# infra-platform PRODUCT card project at all (an exec-only servingRoles set, by
# construction, projects zero product skills and therefore no product card).
PRODUCT_ROLES = {"infra-platform"}


def _manifest() -> dict:
    return json.loads((ROOT / ".fuze/manifest.json").read_text())


def _role(key: str) -> dict:
    return json.loads((ROOT / "agent-templates/roles" / key / "role.json").read_text())


def test_manifest_declares_an_a2a_block():
    a2a = _manifest().get("a2a")
    assert a2a is not None, "the a2a block is the A2A surface; absent means unconfigured"
    assert isinstance(a2a.get("enabled"), bool), (
        "a2a.enabled must be an explicit true/false, not absent or truthy-by-accident"
    )
    # Deliberately NOT asserting a specific value here. Whether the infra-platform
    # tenant is actually LIVE (enabled: true) depends on a Helm a2a: block existing
    # for it (gate-a2a I0 enforces that separately, with cluster/deploy context this
    # test suite does not have) -- staying false while that lands is correct, not drift.
    # card-projection.md section 5.4 — exec agents are in-cluster only, never tunnel-published.
    # The product tenant follows the same rule: this repo's A2A surface is never the
    # public tunnel-facing kind regardless of which tenant is asking.
    assert a2a.get("external") is False


def test_serving_roles_are_exactly_the_exec_roles_plus_the_product_role():
    """The load-bearing line, updated for the 2026-09-04 policy.

    Before: this asserted servingRoles == EXEC_ROLES exactly, because ANY non-exec role
    here was necessarily an accident (FuzeInfra served no product of its own). Now one
    non-exec role is deliberate. The invariant that matters is unchanged in spirit: an
    UNPLANNED role landing in servingRoles must still fail loudly, exactly as it would
    have before -- the allowed set just has one more deliberate member than it used to.
    """
    serving = _manifest()["a2a"].get("servingRoles")
    assert serving, "absent/empty servingRoles falls back to ALL roles -> a wide-open card"
    expected = EXEC_ROLES | PRODUCT_ROLES
    assert set(serving) == expected, (
        f"servingRoles must be exactly the exec roles plus {sorted(PRODUCT_ROLES)}, got "
        f"{sorted(serving)}. Any OTHER addition here publishes an unplanned product-card "
        f"skill to every allowlisted caller."
    )


def test_every_serving_role_exists_and_is_correctly_tiered():
    """A serving role that is absent, undescribed, or wrongly tiered breaks the projection.

    EXEC_ROLES must be metadata.tier == "executive" -- that is what routes them to
    Exec-<role> cards instead of the product card. PRODUCT_ROLES must be the OPPOSITE:
    NOT tier == "executive", because that is what makes infra-platform project into the
    product card in the first place, which is the entire point of adding it.
    """
    for key in _manifest()["a2a"]["servingRoles"]:
        path = ROOT / "agent-templates/roles" / key / "role.json"
        assert path.is_file(), f"servingRoles names {key!r} but {path} does not exist"
        role = _role(key)
        tier = role.get("metadata", {}).get("tier")
        if key in EXEC_ROLES:
            assert tier == "executive", (
                f"{key!r} is in servingRoles as an exec role but not metadata.tier=executive, "
                f"so it would project into the product card instead of an Exec-{key} card"
            )
        elif key in PRODUCT_ROLES:
            assert tier != "executive", (
                f"{key!r} is FuzeInfra's deliberate product role but is tiered executive, "
                f"so it would project into an Exec-{key} card instead of the product card "
                f"-- the opposite of what adding it here was for"
            )
        else:
            raise AssertionError(
                f"{key!r} is in servingRoles but is neither an exec role nor a declared "
                f"product role (PRODUCT_ROLES) -- classify it explicitly, do not let it "
                f"project by default"
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
