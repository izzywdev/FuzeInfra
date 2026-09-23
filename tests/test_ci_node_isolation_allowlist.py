"""Guards against a repeat of PR #1193: a workload that explicitly tolerates
the `fuzeinfra.io/ci` taint from outside the ARC runner namespaces.

Background. `fuzeinfra.io/pool=ci` nodes carry a NoSchedule taint
(`fuzeinfra.io/ci=true:NoSchedule`, applied/auto-repaired by
runners/arc/gitops/controller-selfheal.yaml's check 1) specifically so only
CI runner workloads land on them -- they are the most churn-prone, most
resource-saturated nodes in the cluster. PR #1193, written to fix CoreDNS's
single point of failure, replaced the inert coredns-ha HelmChartConfig with
a real Deployment that explicitly tolerated this taint (plus required
nodeAffinity banning the control-plane), so BOTH extra CoreDNS replicas
landed on the two CI nodes instead of the durable fuze-core-* nodes
(ci-runner-1 measured at 96% CPU as a result). This passed review because
nothing checked for the pattern.

This is the repo-local half of a two-layer defense; the cluster-level half
is the ValidatingAdmissionPolicy in
helm/fuzeinfra/templates/ci-node-isolation.yaml (gated by
ciNodeIsolation.enabled), which rejects the same pattern at admission time
regardless of which repo/chart/raw-apply produced it -- this test catches it
earlier, at PR time, cheaply, with no cluster needed.

Offline: parses committed YAML directly and renders the Helm chart via the
`helm` CLI (the same "render the chart and assert on the result" pattern the
repo's other chart-invariant tests already use -- see the comment above the
offline-tests step in .github/workflows/infrastructure-tests.yml). No
network, no live cluster call.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]

# Must stay in lockstep with ciNodeIsolation.allowedNamespaces in
# helm/fuzeinfra/values.yaml (test_allowlists_are_in_sync_with_the_chart below
# enforces that) -- these are the ONLY namespaces observed live (2026-09-23)
# to legitimately carry an explicit fuzeinfra.io/ci toleration.
ALLOWED_NAMESPACES = {"arc-runners", "arc-systems"}

# Manifest directories applied straight to the cluster (not Helm templates).
RAW_MANIFEST_DIRS = [
    ROOT / "argocd",
    ROOT / "runners" / "arc" / "gitops",
]

# Resource kinds that carry a pod template somewhere in their spec.
POD_TEMPLATE_KINDS = {
    "Pod",
    "Deployment",
    "DaemonSet",
    "StatefulSet",
    "ReplicaSet",
    "Job",
    "CronJob",
}


def _pod_spec_of(doc):
    kind = doc.get("kind")
    if kind == "Pod":
        return doc.get("spec")
    if kind == "CronJob":
        try:
            return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        except (KeyError, TypeError):
            return None
    try:
        return doc["spec"]["template"]["spec"]
    except (KeyError, TypeError):
        return None


def _explicit_ci_toleration(pod_spec):
    """Mirrors the CEL predicate in ci-node-isolation.yaml: an entry with a
    'key' field literally equal to 'fuzeinfra.io/ci'. A keyless blanket
    `{operator: Exists}` toleration (used by fuzeinfra-node-exporter,
    fuzeinfra-promtail, fuzeinfra-node-configurator to run on every node) is
    deliberately NOT flagged -- see ci-node-isolation.yaml's SCOPE comment."""
    if not pod_spec:
        return False
    return any(t.get("key") == "fuzeinfra.io/ci" for t in (pod_spec.get("tolerations") or []))


def _offenders(docs_with_source):
    offenders = []
    for source, doc in docs_with_source:
        if not isinstance(doc, dict) or doc.get("kind") not in POD_TEMPLATE_KINDS:
            continue
        namespace = doc.get("metadata", {}).get("namespace")
        if _explicit_ci_toleration(_pod_spec_of(doc)) and namespace not in ALLOWED_NAMESPACES:
            name = doc.get("metadata", {}).get("name")
            offenders.append(f"{source}: {doc['kind']} '{name}' (namespace={namespace})")
    return offenders


def _raw_manifest_docs():
    for d in RAW_MANIFEST_DIRS:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.yaml")) + sorted(d.rglob("*.yml")):
            try:
                for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                    if doc:
                        yield str(path.relative_to(ROOT)), doc
            except yaml.YAMLError:
                continue


def _rendered_chart_docs(values_file):
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm binary not on PATH -- this test needs it to render the chart")
    result = subprocess.run(
        [
            helm,
            "template",
            "fuzeinfra",
            str(ROOT / "helm" / "fuzeinfra"),
            "-f",
            str(ROOT / "helm" / "fuzeinfra" / values_file),
            "--namespace",
            "fuzeinfra",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"helm template ({values_file}) failed:\n{result.stderr}"
    for doc in yaml.safe_load_all(result.stdout):
        if doc:
            yield f"helm/fuzeinfra ({values_file})", doc


# --- the guard itself ----------------------------------------------------


def test_raw_bootstrap_manifests_do_not_tolerate_ci_taint_outside_arc_namespaces():
    offenders = _offenders(_raw_manifest_docs())
    assert not offenders, (
        "found workload(s) tolerating fuzeinfra.io/ci outside the ARC runner "
        f"namespaces {sorted(ALLOWED_NAMESPACES)} -- this is exactly PR #1193's "
        "mistake: an explicit toleration lets a non-CI workload schedule onto the "
        "fuzeinfra.io/pool=ci nodes. If this is a genuine new ARC runner "
        "component, add its namespace to ALLOWED_NAMESPACES here AND to "
        "ciNodeIsolation.allowedNamespaces in helm/fuzeinfra/values.yaml (kept in "
        "sync by test_allowlists_are_in_sync_with_the_chart below). Otherwise, "
        "drop the toleration -- the node-configurator DaemonSet approach in "
        "helm/fuzeinfra/templates/node-configurator.yaml is the right pattern for "
        "anything that needs a durable/control-plane node instead. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_rendered_prod_chart_does_not_tolerate_ci_taint_outside_arc_namespaces():
    offenders = _offenders(_rendered_chart_docs("values-contabo.yaml"))
    assert not offenders, (
        "the rendered chart (values-contabo.yaml, what actually reaches prod) "
        "contains a workload tolerating fuzeinfra.io/ci outside "
        f"{sorted(ALLOWED_NAMESPACES)}. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_allowlists_are_in_sync_with_the_chart():
    """The cluster-level ValidatingAdmissionPolicy binding
    (ci-node-isolation.yaml) reads ciNodeIsolation.allowedNamespaces from
    values.yaml. If that drifts from this test's ALLOWED_NAMESPACES, one
    layer of the two-layer defense silently disagrees with the other."""
    values = yaml.safe_load((ROOT / "helm" / "fuzeinfra" / "values.yaml").read_text(encoding="utf-8"))
    chart_allowlist = set(values.get("ciNodeIsolation", {}).get("allowedNamespaces", []))
    assert chart_allowlist == ALLOWED_NAMESPACES, (
        f"helm/fuzeinfra/values.yaml ciNodeIsolation.allowedNamespaces "
        f"({sorted(chart_allowlist)}) does not match this test's ALLOWED_NAMESPACES "
        f"({sorted(ALLOWED_NAMESPACES)}) -- update both together."
    )


# --- proves the guard actually catches the #1193 shape --------------------


def test_the_predicate_flags_the_exact_pr_1193_toleration_shape():
    """PR #1193's Deployment carried this toleration list verbatim. This is a
    logic-level proof the detection predicate works, independent of whatever
    is currently committed in the repo (which should always be clean --
    that's the test above)."""
    pr_1193_tolerations = [
        {"key": "CriticalAddonsOnly", "operator": "Exists"},
        {"key": "fuzeinfra.io/ci", "operator": "Exists"},
        {"key": "fuzeinfra.io/elastic", "operator": "Exists"},
    ]
    assert _explicit_ci_toleration({"tolerations": pr_1193_tolerations})


def test_the_predicate_does_not_flag_legitimate_host_daemonsets():
    """fuzeinfra-node-exporter / promtail / node-configurator's shape: a
    keyless blanket toleration that must stay exempt or fleet-wide
    observability breaks the day this policy is enabled."""
    blanket = [{"operator": "Exists"}]
    assert not _explicit_ci_toleration({"tolerations": blanket})


def test_the_predicate_does_not_flag_an_untainted_workload():
    assert not _explicit_ci_toleration({"tolerations": []})
    assert not _explicit_ci_toleration({})


def test_the_predicate_respects_the_namespace_allowlist():
    doc = {
        "kind": "Deployment",
        "metadata": {"name": "fuze-runner", "namespace": "arc-runners"},
        "spec": {
            "template": {
                "spec": {"tolerations": [{"key": "fuzeinfra.io/ci", "operator": "Exists"}]}
            }
        },
    }
    assert not _offenders([("fixture", doc)]), "arc-runners is allowlisted -- must not be flagged"

    doc["metadata"]["namespace"] = "kube-system"
    assert _offenders([("fixture", doc)]), "kube-system is NOT allowlisted -- must be flagged"
