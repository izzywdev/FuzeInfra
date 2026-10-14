"""Offline guards for .github/workflows/argo-terminate-op.yml (the deployment-watchdog's one write).

2026-10-14: Step 3 used `kubectl patch --subresource=status`, but Argo CD's Application
CRD has no status subresource (`subresources: {}`), so it always failed with NotFound and the
watchdog could never clear a stale `operationState.phase: Running` whose `.operation` was empty.
That wedge held a prod sync (and a Kafka fix) for hours.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = (REPO / ".github" / "workflows" / "argo-terminate-op.yml").read_text()


def code_lines() -> list[str]:
    return [l for l in WF.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_never_patches_a_status_subresource_that_does_not_exist():
    assert not any("--subresource" in l for l in code_lines())


def test_every_kubectl_call_names_the_argocd_namespace():
    for l in code_lines():
        if re.search(r"\bkubectl\s+(get|patch|annotate|delete|apply)\b", l) and "config set-context" not in l:
            assert "-n argocd" in l or "--namespace" in l, l


def test_step3_only_touches_operation_state_and_is_guarded_by_running():
    assert 'if [ "$phase" != "Running" ]' in WF  # does nothing unless an op is actually stuck
    patches = re.findall(r"-p '(\{\"status\":.*?\})'", WF)
    assert patches, "step 3 status patch missing"
    for p in patches:
        assert p.startswith('{"status":{"operationState":{"phase":"Failed"')
        assert p.count("{") == 3  # status -> operationState -> {phase,message}, nothing else
