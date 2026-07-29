"""Regression guard: the local overlay must be installable on a bare kind cluster.

Why this file exists
--------------------
`helm/fuzeinfra/templates/autoscaler/sealed-secret-ca-provider.yaml` rendered a
`SealedSecret` with no `enabled` gate, so it appeared in EVERY overlay including
values-local. A fresh kind cluster has no Sealed Secrets CRD, so the very first
real `kind-validate` run died at install:

    Error: unable to build kubernetes objects from release manifest:
    resource mapping not found for name: "fuzeinfra-ca-provider" ...
    no matches for kind "SealedSecret" in version "bitnami.com/v1alpha1"
    ensure CRDs are installed first

The dangerous part is that nothing caught it for months. `kubeconform
-ignore-missing-schemas` — which is what helm-validate.yml runs — SKIPS resources
whose CRD schema it cannot find, so an uninstallable chart validated clean. The
skip was even visible in CI output as "Skipped: 1" and read as noise.

What is enforced
----------------
Every apiVersion rendered by values-local.yaml must belong to a group that a
`k8s/kind/setup-kind.sh` cluster actually has: core Kubernetes, or one of the
CRDs that script installs (cert-manager). Anything else means `helm install`
would fail on a developer's fresh cluster, and must be gated off for local.

Add to CRD_GROUPS_INSTALLED_LOCALLY only when setup-kind.sh genuinely installs
that CRD — the point of the list is to mirror reality, not to silence the test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
LOCAL_VALUES = CHART / "values-local.yaml"
SETUP_KIND = REPO_ROOT / "k8s" / "kind" / "setup-kind.sh"

#: Built-in Kubernetes API groups — always present, no CRD needed. "" is core/v1.
CORE_GROUPS = {
    "", "apps", "batch", "networking.k8s.io", "rbac.authorization.k8s.io",
    "policy", "autoscaling", "storage.k8s.io", "scheduling.k8s.io",
    "apiextensions.k8s.io", "coordination.k8s.io", "discovery.k8s.io",
    "admissionregistration.k8s.io", "apiregistration.k8s.io", "node.k8s.io",
    "flowcontrol.apiserver.k8s.io", "certificates.k8s.io", "events.k8s.io",
    "authentication.k8s.io", "authorization.k8s.io",
}

#: CRD groups setup-kind.sh installs before deploying the chart.
CRD_GROUPS_INSTALLED_LOCALLY = {
    "cert-manager.io",        # cert-manager + the fuzeinfra-local-ca ClusterIssuer
    "acme.cert-manager.io",
}


def _group(api_version: str) -> str:
    """`apps/v1` -> `apps`; `v1` -> `` (core)."""
    return api_version.split("/")[0] if "/" in api_version else ""


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_local_overlay_needs_no_uninstalled_crd():
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout

    offenders: dict[str, set[str]] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or "apiVersion" not in doc:
            continue
        group = _group(str(doc["apiVersion"]))
        if group in CORE_GROUPS or group in CRD_GROUPS_INSTALLED_LOCALLY:
            continue
        offenders.setdefault(
            f"{doc['apiVersion']} {doc.get('kind', '?')}", set()
        ).add((doc.get("metadata") or {}).get("name", "?"))

    assert not offenders, (
        "values-local.yaml renders resources whose CRDs a fresh kind cluster does "
        "not have, so `helm install` fails for any developer running `make dev`:\n"
        + "\n".join(f"  {k}: {sorted(v)}" for k, v in sorted(offenders.items()))
        + "\n\nGate these off for local (an `enabled` flag that values-local sets "
          "false), or install the CRD in k8s/kind/setup-kind.sh and add its group "
          "to CRD_GROUPS_INSTALLED_LOCALLY.\n"
          "NOTE: `kubeconform -ignore-missing-schemas` will NOT catch this — it "
          "silently skips resources with unknown schemas."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_sealed_secrets_are_prod_only():
    """Specific guard for the resource that actually broke it.

    Sealed Secrets are a prod/GitOps mechanism — the controller lives in the real
    cluster and holds the decryption key. A SealedSecret in the local overlay can
    never decrypt into anything useful even if the CRD were installed, so its
    presence there is always a mistake.
    """
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout
    names = [
        (doc.get("metadata") or {}).get("name")
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "SealedSecret"
    ]
    assert not names, (
        f"values-local.yaml renders SealedSecret(s) {names}. kind has no Sealed "
        "Secrets controller or CRD, so the install fails outright — and even with "
        "the CRD there is no key to decrypt them. Gate them to prod."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_every_referenced_namespace_is_created_or_is_the_release_namespace():
    """Second way the local overlay turned out to be uninstallable on a bare cluster.

    The CRD guard above passed, and the chart still could not install. The
    custom-hostname-api route profile puts a Role + RoleBinding in the CONSUMER's
    namespace (`fuzefront`), which exists in prod because FuzeFront deploys
    itself there — and does not exist on a fresh kind cluster. Helm applies the
    rest of the chart, hits those two objects, and rejects the whole install:

        Error: 2 errors occurred:
            * namespaces "fuzefront" not found
            * namespaces "fuzefront" not found

    Note the shape of the failure: 25+ pods are already created when it fires, so
    the cluster *looks* like it is coming up. Only the helm exit code says
    otherwise.

    A namespace is a precondition, not a schema, so neither `helm lint` nor
    `kubeconform` can see this. The rule: if the local overlay puts a resource in
    some namespace, the local overlay must also create that namespace.
    """
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout

    docs = [d for d in yaml.safe_load_all(rendered) if d and "apiVersion" in d]
    created = {
        (d.get("metadata") or {}).get("name")
        for d in docs if d.get("kind") == "Namespace"
    }
    missing: dict[str, set[str]] = {}
    for doc in docs:
        ns = (doc.get("metadata") or {}).get("namespace")
        # No namespace => cluster-scoped, or defaulted to the release namespace.
        if not ns or ns == "fuzeinfra" or ns in created:
            continue
        missing.setdefault(ns, set()).add(
            f"{doc.get('kind', '?')}/{(doc.get('metadata') or {}).get('name', '?')}"
        )

    assert not missing, (
        "values-local.yaml puts resources in namespaces that nothing creates, so "
        "`helm install` is rejected on a fresh cluster with "
        '`namespaces \"<name>\" not found`:\n'
        + "\n".join(f"  {ns}: {sorted(v)}" for ns, v in sorted(missing.items()))
        + "\n\nEither render the Namespace for local (e.g. a route profile's "
          "`createNamespace: true`, which must stay false in real overlays where "
          "the consumer owns its namespace), or gate the resources off locally.\n"
          "NOTE: helm lint and kubeconform CANNOT catch this — a namespace is an "
          "install-time precondition, not a schema property."
    )


def test_prod_overlay_does_not_create_consumer_namespaces():
    """The inverse guard: `createNamespace` must never be on in a real overlay.

    Two releases claiming one Namespace object is an Argo ownership fight, and a
    `helm uninstall` would take the consumer's running workloads with it. The
    escape hatch is for standalone clusters only.
    """
    import re
    for overlay in ("values.yaml", "values-contabo.yaml", "values-aws.yaml"):
        path = CHART / overlay
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        enabled = [
            line for line in text.splitlines()
            if re.match(r"\s*createNamespace:\s*true\b", line)
        ]
        assert not enabled, (
            f"{overlay} sets createNamespace: true ({enabled}). Only "
            "values-local.yaml may do this — on a real cluster the consumer owns "
            "its own namespace."
        )


def test_setup_kind_installs_the_crds_this_test_assumes():
    """Keep the allowlist honest: it must describe what setup-kind.sh really does.

    If someone drops the cert-manager install from setup-kind.sh, this list would
    silently keep permitting cert-manager resources that no longer have a CRD.
    """
    script = SETUP_KIND.read_text(encoding="utf-8")
    assert "cert-manager" in script, (
        "CRD_GROUPS_INSTALLED_LOCALLY allows cert-manager.io, but setup-kind.sh no "
        "longer installs cert-manager — the allowlist is now lying. Update both."
    )


# ---------------------------------------------------------------------------
# Third and fourth ways the local overlay turned out to be uninstallable.
#
# The namespace guard above passed, the CRD guard passed, `helm install` itself
# finally succeeded — and the stack still did not come up. 25 pods reached
# Running; two workloads never did:
#
#   fuzeinfra-chromadb-0   Init:CreateContainerConfigError
#     Error: secret "chromadb-admin-credentials" not found
#
#   fuzeinfra-airflow-{webserver,scheduler,worker,flower}   CrashLoopBackOff
#     FATAL:  database "airflow" does not exist
#
# Same family as the namespace bug in every case: the local overlay assumed a
# precondition that only production supplies. Neither is visible to `helm lint`
# or `kubeconform` — one is a runtime object, the other is an ordering property.
# ---------------------------------------------------------------------------


def _render_local() -> list[dict]:
    rendered = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(LOCAL_VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(rendered) if d and "apiVersion" in d]


#: Kinds that carry a pod template we need to inspect, and where it lives.
_POD_TEMPLATE_PATHS = {
    "Deployment": ("spec", "template"),
    "StatefulSet": ("spec", "template"),
    "DaemonSet": ("spec", "template"),
    "ReplicaSet": ("spec", "template"),
    "Job": ("spec", "template"),
}


def _pod_specs(docs: list[dict]):
    """Yield (owner, podSpec) for everything in the render that runs a pod."""
    for doc in docs:
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name", "?")
        if kind == "Pod":
            yield f"Pod/{name}", doc.get("spec") or {}
        elif kind == "CronJob":
            spec = (((doc.get("spec") or {}).get("jobTemplate") or {})
                    .get("spec") or {}).get("template") or {}
            yield f"CronJob/{name}", spec.get("spec") or {}
        elif kind in _POD_TEMPLATE_PATHS:
            a, b = _POD_TEMPLATE_PATHS[kind]
            template = (doc.get(a) or {}).get(b) or {}
            yield f"{kind}/{name}", template.get("spec") or {}


def _required_secret_refs(pod_spec: dict) -> set[str]:
    """Secret names this pod cannot start without (`optional: true` excluded)."""
    names: set[str] = set()
    containers = (pod_spec.get("containers") or []) + \
                 (pod_spec.get("initContainers") or [])
    for container in containers:
        for env in container.get("env") or []:
            ref = ((env.get("valueFrom") or {}).get("secretKeyRef")) or {}
            if ref.get("name") and not ref.get("optional"):
                names.add(ref["name"])
        for envfrom in container.get("envFrom") or []:
            ref = envfrom.get("secretRef") or {}
            if ref.get("name") and not ref.get("optional"):
                names.add(ref["name"])
    for volume in pod_spec.get("volumes") or []:
        secret = volume.get("secret") or {}
        if secret.get("secretName") and not secret.get("optional"):
            names.add(secret["secretName"])
    return names


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_every_secret_a_local_pod_needs_is_created_by_the_same_render():
    """A referenced-but-absent Secret is a permanent, silent wedge.

    `helm install` SUCCEEDS — the manifest is valid and every object is
    accepted. The pod then sits in `CreateContainerConfigError` forever, because
    a missing Secret is not a retryable pull failure that eventually resolves;
    nothing is ever going to create it. Locally that Secret only ever came from
    `deploy/sealed-secrets/`, which needs the Sealed Secrets controller and the
    production private key.

    Same rule as the namespace guard: if the local overlay mounts it, the local
    overlay must render it.
    """
    docs = _render_local()
    created = {
        (d.get("metadata") or {}).get("name")
        for d in docs if d.get("kind") == "Secret"
    }

    missing: dict[str, set[str]] = {}
    for owner, pod_spec in _pod_specs(docs):
        for secret in _required_secret_refs(pod_spec) - created:
            missing.setdefault(secret, set()).add(owner)

    assert not missing, (
        "values-local.yaml mounts Secrets that nothing in the local render "
        "creates. `helm install` will succeed and the pods will then wedge in "
        "CreateContainerConfigError indefinitely:\n"
        + "\n".join(f"  {s}: needed by {sorted(v)}" for s, v in sorted(missing.items()))
        + "\n\nRender a throwaway value for local (the `devSecret.enabled` "
          "pattern used by customHostnameApi and chromadb, which must stay false "
          "in every real overlay), or gate the workload off locally.\n"
          "NOTE: helm lint and kubeconform CANNOT catch this — the manifest is "
          "perfectly valid; the Secret is a runtime object."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_airflow_init_is_not_a_hook_in_the_local_overlay():
    """The cold-start deadlock, which is an ordering bug rather than a missing object.

    `fuzeinfra-airflow-init` creates the `airflow` database, migrates it and
    makes the admin user. As a `post-install` hook that can never run on a fresh
    cluster: Helm runs post-install hooks only AFTER `--wait` reports the release
    ready, and the four Airflow pods cannot become ready until the database the
    hook creates exists. So `--wait` burns its full timeout, the hook never
    fires, and the pods crashloop on `FATAL: database "airflow" does not exist`.
    Argo has the identical ordering — PostSync runs after the sync goes healthy.

    It only ever worked because every cluster it had run on already had the
    database. `airflow.init.mode: inline` moves the Job into the main wave.
    """
    docs = _render_local()
    jobs = [
        d for d in docs
        if d.get("kind") == "Job"
        and str((d.get("metadata") or {}).get("name", "")).startswith("fuzeinfra-airflow-init")
    ]
    assert jobs, (
        "the local render has no fuzeinfra-airflow-init Job at all — Airflow "
        "cannot have a metadata database."
    )
    hooked = [
        (j.get("metadata") or {}).get("name")
        for j in jobs
        if "helm.sh/hook" in ((j.get("metadata") or {}).get("annotations") or {})
    ]
    assert not hooked, (
        f"fuzeinfra-airflow-init is still a Helm hook in the local overlay ({hooked}). "
        "A post-install hook cannot cold-start the database it is responsible for "
        "creating — see this test's docstring. Set airflow.init.mode: inline in "
        "values-local.yaml."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_every_local_airflow_pod_waits_for_the_metadata_database():
    """The init Job alone is not enough — the pods must not race it.

    Without a gate the Airflow pods start alongside the Job, die on the missing
    database, and CrashLoopBackOff backs the retry off to ~5 minutes. A pod can
    then still be down long after the database is ready, which blows the same
    `--wait` budget by a different route. Blocking in an init container makes the
    wait deterministic.
    """
    docs = _render_local()
    offenders = []
    for owner, pod_spec in _pod_specs(docs):
        if not owner.startswith("Deployment/fuzeinfra-airflow-"):
            continue
        init_names = {c.get("name") for c in pod_spec.get("initContainers") or []}
        if "wait-for-airflow-db" not in init_names:
            offenders.append(owner)

    assert not offenders, (
        f"these Airflow workloads have no wait-for-airflow-db init container: "
        f"{sorted(offenders)}. They will crashloop through the whole cold start."
    )


def test_prod_overlays_do_not_enable_local_only_bootstrap_escapes():
    """Inverse guards for both escape hatches added here.

    `chromadb.auth.adminSecret.devSecret` writes a plaintext admin token into an
    ordinary Secret under the SAME name the SealedSecret uses — in prod that
    would overwrite the real credential with a public one. `airflow.init.mode:
    inline` strips the hook annotations, changing how Argo orders the Job; prod's
    metadata database already exists and does not need it.

    Parsed as YAML rather than regexed, so nesting and comments cannot fool it.
    """
    def walk(node, path=()):
        """Yield (dotted-path, dict) for every mapping in the tree."""
        if isinstance(node, dict):
            yield ".".join(path), node
            for key, value in node.items():
                yield from walk(value, path + (str(key),))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, path + (f"[{index}]",))

    for overlay in ("values.yaml", "values-contabo.yaml", "values-aws.yaml"):
        path = CHART / overlay
        if not path.exists():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        enabled_dev_secrets = [
            where for where, node in walk(doc)
            if where.endswith("devSecret") and node.get("enabled") is True
        ]
        assert not enabled_dev_secrets, (
            f"{overlay} enables a devSecret at {enabled_dev_secrets}. Those render "
            "a plaintext token into a Secret whose name the real SealedSecret also "
            "uses — enabling it in a real overlay replaces the production "
            "credential with a public one. kind only."
        )

        # NOTE: the inverse of what this used to assert.
        #
        # `init.mode: inline` was originally treated as a local-only escape
        # hatch, on the belief that only local cold-starts. That was wrong:
        # `hook` cannot cold-start on any cluster, prod included — local was just
        # the only place that ever exercised it. `inline` is now the default and
        # a real overlay pinning itself back to `hook` re-introduces the
        # deadlock, silently, and only discovers it during a rebuild.
        hooked = [
            where for where, node in walk(doc)
            if where.endswith("init") and node.get("mode") == "hook"
        ]
        assert not hooked, (
            f"{overlay} pins init.mode back to 'hook' at {hooked}. That mode "
            "cannot create the database it is responsible for: Helm and Argo both "
            "run it only after the release reports healthy, which cannot happen "
            "while the database is missing. It works only on a cluster that "
            "already has one — so the failure is invisible until a rebuild."
        )

        external_listeners = [
            where for where, node in walk(doc)
            if where.endswith("externalListener") and node.get("enabled") is True
        ]
        assert not external_listeners, (
            f"{overlay} enables an externalListener at {external_listeners}. It "
            "advertises `localhost`, and Kafka hands the advertised address back "
            "to clients — on a real cluster every client would be redirected to "
            "its own pod. kind only, for port-forwarded test clients."
        )
