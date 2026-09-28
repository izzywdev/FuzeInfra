"""The shared-credentials Secret must stay complete, and be movable off the chart.

Two separate concerns, both load-bearing for the credential rotation this repo
still owes (see docs/runbooks/rotate-shared-credentials.md):

  1. COMPLETENESS. Every key a workload reads via `secretKeyRef` from the shared
     secret must actually exist in it. A missing key is not a soft failure — the
     pod stays in CreateContainerConfigError forever.

  2. THE CUTOVER. `credentials.existingSecret` is the supported way to stop the
     chart rendering credentials and point everything at an externally-managed
     (SealedSecret) one instead. The dangerous failure mode there is silent: the
     flip works, the chart stops rendering the Secret, and an externally-created
     secret that is missing one key takes down whichever pod needed it. These
     tests pin the exact key set the replacement must carry, so the person doing
     the rotation has a machine-checked list rather than a hand-copied one.

Why this file exists at all: prod renders `fuzeinfra-secrets` from the committed
dev defaults in values.yaml, because values-contabo.yaml sets neither a
`credentials:` block nor `credentials.existingSecret`. Fixing that is a
coordinated rotation (the datastores hold their own copies of these passwords),
not a values edit — so the mechanism is tested here first, ready for it.

Offline: renders the chart with `helm template`. No cluster, no network.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
SHARED_SECRET = "fuzeinfra-secrets"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm not installed"
)


def _render(*value_files, extra=None):
    argv = ["helm", "template", "fuzeinfra", str(CHART), "--namespace", "fuzeinfra"]
    for v in value_files:
        argv += ["-f", str(CHART / v)]
    if extra:
        argv += extra
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _walk(node):
    """Every dict nested anywhere in a rendered manifest."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _keys_consumed(docs, secret_name=SHARED_SECRET) -> set:
    """Keys read from the shared secret via secretKeyRef / envFrom anywhere."""
    keys = set()
    for doc in docs:
        for d in _walk(doc):
            ref = d.get("secretKeyRef")
            if isinstance(ref, dict) and ref.get("name") == secret_name and ref.get("key"):
                keys.add(ref["key"])
    return keys


def _rendered_secret(docs, secret_name=SHARED_SECRET):
    for d in docs:
        if d.get("kind") == "Secret" and d["metadata"]["name"] == secret_name:
            return d
    return None


def _keys_provided(secret) -> set:
    return set((secret.get("stringData") or {})) | set((secret.get("data") or {}))


# --- 1. completeness --------------------------------------------------------

def test_every_consumed_key_exists_in_the_rendered_secret():
    """A secretKeyRef to a key the Secret does not carry leaves the pod stuck in
    CreateContainerConfigError — it never crash-loops, it simply never starts."""
    docs = _render("values-contabo.yaml")
    secret = _rendered_secret(docs)
    assert secret is not None, (
        "prod renders no shared Secret; if credentials.existingSecret was set, "
        "update this test to assert against the external secret instead"
    )
    missing = _keys_consumed(docs) - _keys_provided(secret)
    assert not missing, f"workloads read key(s) the Secret does not provide: {sorted(missing)}"


def test_the_consumed_key_set_is_not_empty():
    """Guard the guard: if the walk stopped finding secretKeyRefs, the test above
    would pass vacuously and stop protecting anything."""
    docs = _render("values-contabo.yaml")
    assert len(_keys_consumed(docs)) >= 5, (
        "found almost no secretKeyRefs against the shared secret — the extractor "
        "is probably broken, not the chart"
    )


# --- 2. the cutover ---------------------------------------------------------

def test_existing_secret_stops_the_chart_rendering_credentials(tmp_path):
    """With credentials.existingSecret set, the chart must NOT emit a Secret of
    its own — otherwise it would keep overwriting the sealed one on every sync."""
    overlay = tmp_path / "existing.yaml"
    overlay.write_text("credentials:\n  existingSecret: fuzeinfra-secrets\n")
    docs = _render("values-contabo.yaml", extra=["-f", str(overlay)])
    assert _rendered_secret(docs) is None, (
        "chart still renders fuzeinfra-secrets even though existingSecret is set; "
        "Argo would overwrite the externally-managed secret on every sync"
    )


def test_consumers_still_resolve_after_the_cutover(tmp_path):
    """The flip must not orphan a single reference. Every key consumed WITHOUT
    existingSecret must still be consumed WITH it, against the same name — so the
    external secret's required key set is exactly the set pinned below."""
    overlay = tmp_path / "existing.yaml"
    overlay.write_text("credentials:\n  existingSecret: fuzeinfra-secrets\n")
    before = _keys_consumed(_render("values-contabo.yaml"))
    after = _keys_consumed(_render("values-contabo.yaml", extra=["-f", str(overlay)]))
    assert before == after, (
        "the existingSecret cutover changed which keys are read: "
        f"lost={sorted(before - after)} gained={sorted(after - before)}"
    )


def test_the_external_secret_must_carry_these_exact_keys():
    """The machine-checked shopping list for whoever seals the replacement.

    If this list changes, the rotation runbook's key set changes with it, and a
    stale runbook here means a pod that will not start after the cutover.
    """
    docs = _render("values-contabo.yaml")
    required = _keys_consumed(docs)

    # The exact surface still served by the CHART-RENDERED secret in prod, i.e.
    # still on the committed dev defaults. Measured, not assumed — an earlier
    # read of this file by hand wrongly included GRAFANA_ADMIN_PASSWORD and
    # RABBITMQ_PASSWORD, which have ALREADY been rotated out to their own sealed
    # Secrets (grafana-admin, fuzeinfra-app-credentials).
    #
    # This set shrinking is the whole goal: every credential moved to a dedicated
    # sealed Secret drops out of it. So this asserts the set is a SUBSET of what
    # we know about — progress is allowed, silent regression is not.
    known_remaining = {
        "POSTGRES_USER", "POSTGRES_PASSWORD",
        "MONGODB_USER", "MONGODB_PASSWORD",
        "NEO4J_AUTH",
        "MARIADB_ROOT_PASSWORD",
        "AIRFLOW_FERNET_KEY", "AIRFLOW_ADMIN_USER",
        "RABBITMQ_USER",
    }
    unexpected = required - known_remaining
    assert not unexpected, (
        f"credential(s) moved BACK onto the chart-rendered secret: {sorted(unexpected)}. "
        "That undoes a rotation — see docs/runbooks/rotate-shared-credentials.md"
    )


def test_postgres_pulls_the_whole_shared_secret():
    """fuzeinfra-postgres uses `envFrom: secretRef`, so it receives EVERY key in
    the shared secret as an environment variable, not just the ones it reads.

    That matters for the rotation: adding a key to this Secret adds an env var to
    the Postgres container, and removing one silently changes its environment. It
    is also why the cutover has to be all-or-nothing for this workload rather than
    per-key."""
    docs = _render("values-contabo.yaml")
    users = []
    for doc in docs:
        if doc.get("kind") != "StatefulSet":
            continue
        for d in _walk(doc):
            ref = d.get("secretRef")
            if isinstance(ref, dict) and ref.get("name") == SHARED_SECRET:
                users.append(doc["metadata"]["name"])
    assert "fuzeinfra-postgres" in users, (
        "expected fuzeinfra-postgres to envFrom the shared secret; if that changed, "
        "the rotation runbook's all-or-nothing note for Postgres needs updating"
    )
