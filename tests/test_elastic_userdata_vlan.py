"""Guards the elastic-node cloud-init: template<->deployed-blob parity + VLAN wiring.

WHY THIS FILE EXISTS
--------------------
`clusterAutoscaler.provider.userDataTemplateB64` in values-contabo.yaml is a
base64 blob. `cluster-autoscaler/contabo-externalgrpc/deploy/elastic-userdata.template`
is the file `ca-cutover.yml` regenerates that blob FROM, and the file
`ca-salvage-enroll.yml` reinstalls instances FROM directly.

PR #830 edited only the blob. Nothing failed, and two regressions were armed:

  * the next `ca-cutover` run would have regenerated the blob from the untouched
    template and silently reverted the whole private-VLAN fix, and
  * `ca-salvage-enroll` kept minting off-VLAN nodes from the stale template.

Neither is visible in a diff review, because a base64 blob is not reviewable.
These tests make that drift a red build.

They are offline: no cluster, no Contabo API, no secrets.
"""

from __future__ import annotations

import base64
import io
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "cluster-autoscaler" / "contabo-externalgrpc" / "deploy" / "elastic-userdata.template"
VALUES = REPO / "helm" / "fuzeinfra" / "values-contabo.yaml"
MODULE_TFTPL = REPO / "modules" / "contabo-k3s-node" / "cloud-init.tftpl"

# Contabo's createInstance `userData` is sent as plain text. We have no
# authoritative published ceiling, so this guard is deliberately conservative:
# 16 KiB is the tightest limit any comparable provider imposes (EC2), and every
# comment in the template is shipped over the wire on every scale-up. If a change
# pushes past this, move the prose into docs/ rather than raising the number
# without evidence.
MAX_USERDATA_BYTES = 16 * 1024


def _read(p: pathlib.Path) -> str:
    # newline="" then normalise: a Windows checkout has CRLF in the working tree
    # while git stores LF, and the base64 in values-contabo.yaml is of the LF form.
    return io.open(p, encoding="utf-8", newline="").read().replace("\r\n", "\n")


def _deployed_userdata() -> str:
    raw = _read(VALUES)
    m = re.search(r'^\s*userDataTemplateB64:\s*"([A-Za-z0-9+/=]+)"\s*$', raw, re.M)
    assert m, "clusterAutoscaler.provider.userDataTemplateB64 not found in values-contabo.yaml"
    return base64.b64decode(m.group(1)).decode("utf-8")


def _ssh_key_of(text: str) -> str:
    m = re.search(r"ssh_authorized_keys:\s*\n\s*-\s*(ssh-\S+ \S+(?: \S+)?)\s*\n", text)
    assert m, "no ssh_authorized_keys entry found"
    return m.group(1).strip()


def _render_go_template(text: str) -> str:
    """Substitute the three Go text/template vars renderUserData supplies."""
    return (
        text.replace("{{.NodeName}}", "fuzeinfra-prod-elastic-v2-testnode")
        .replace("{{.K3SServerURL}}", "https://10.0.0.6:6443")
        .replace("{{.K3SNodeToken}}", "K10testtoken::server:testsecret")
    )


# ---------------------------------------------------------------------------
# 1. The blob and the template must be the same file.
# ---------------------------------------------------------------------------


def test_deployed_blob_matches_source_template():
    """The base64 in values-contabo.yaml is exactly the template with the SSH key filled in.

    This is the check that would have caught PR #830's half-fix.
    """
    deployed = _deployed_userdata()
    template = _read(TEMPLATE)
    assert "__SSH_PUBLIC_KEY__" in template, (
        "the template lost its __SSH_PUBLIC_KEY__ placeholder; ca-cutover.yml "
        "hard-fails without it"
    )
    expected = template.replace("__SSH_PUBLIC_KEY__", _ssh_key_of(deployed))
    assert deployed == expected, (
        "userDataTemplateB64 has drifted from deploy/elastic-userdata.template. "
        "Edit the TEMPLATE and regenerate the blob - never hand-edit the base64. "
        "The next ca-cutover run regenerates the blob from the template and would "
        "silently revert whatever the blob alone contains."
    )


def test_deployed_blob_is_valid_cloud_config_yaml():
    rendered = _render_go_template(_deployed_userdata())
    assert rendered.startswith("#cloud-config"), "cloud-init requires the #cloud-config header"
    doc = yaml.safe_load(rendered)
    assert isinstance(doc, dict), "userdata must parse as a cloud-config mapping"
    for key in ("users", "write_files", "runcmd"):
        assert key in doc, f"cloud-config is missing {key!r}"


def test_userdata_stays_within_a_conservative_size_budget():
    size = len(_deployed_userdata().encode("utf-8"))
    assert size <= MAX_USERDATA_BYTES, (
        f"rendered userData is {size} bytes (> {MAX_USERDATA_BYTES}). Every byte "
        "is shipped to Contabo on each scale-up; move prose into docs/."
    )


# ---------------------------------------------------------------------------
# 2. The VLAN wiring the blob must actually contain.
# ---------------------------------------------------------------------------


def test_private_nic_is_configured_not_merely_waited_for():
    """The bug behind every off-VLAN elastic node.

    PR #830 added a 300s wait for an address on eth1 but never CONFIGURED eth1.
    cloud-init's fallback network config only brings up the primary NIC, so the
    wait could not succeed and every node fell through to the fail-open branch.
    A wait without a netplan is not a fix.
    """
    doc = yaml.safe_load(_render_go_template(_deployed_userdata()))
    paths = [f["path"] for f in doc["write_files"]]
    assert "/etc/netplan/60-eth1-private.yaml" in paths, (
        "no netplan drop-in for eth1 - the private NIC will never get a DHCP "
        "lease, so any wait for its address is guaranteed to time out"
    )
    netplan = next(f for f in doc["write_files"] if f["path"] == "/etc/netplan/60-eth1-private.yaml")
    eth1 = yaml.safe_load(netplan["content"])["network"]["ethernets"]["eth1"]
    assert eth1["dhcp4"] is True
    assert eth1["optional"] is True, "optional:true keeps boot from hanging when the NIC is absent"


def test_join_binds_k3s_to_the_private_nic():
    script = _join_script()
    assert "IFACE=eth1" in script, "the private NIC name must be pinned"
    assert "--node-ip $PRIV" in script, "k3s must register on the private address"
    assert "--flannel-iface $IFACE" in script, "the pod overlay must ride the private VLAN"


def test_join_is_reboot_proof_via_systemd_not_runcmd():
    """The assign-before-boot race.

    The Contabo private-network assign happens after createInstance returns and
    may REBOOT the node. cloud-init `runcmd` is once-per-instance, so a reboot
    mid-runcmd can leave the join half-done and never retried. The join must
    therefore live in a unit that re-runs on every boot until it succeeds.
    """
    doc = yaml.safe_load(_render_go_template(_deployed_userdata()))
    paths = [f["path"] for f in doc["write_files"]]
    assert "/etc/systemd/system/fuzeinfra-vlan-join.service" in paths

    unit = next(f for f in doc["write_files"] if f["path"].endswith("fuzeinfra-vlan-join.service"))["content"]
    assert "ConditionPathExists=!/etc/fuzeinfra-vlan-joined" in unit, (
        "the unit must re-run until the VLAN join has succeeded"
    )
    assert "WantedBy=multi-user.target" in unit, "the unit must be enabled at boot, not run once"

    runcmd_text = "\n".join(str(c) for c in doc["runcmd"])
    assert "get.k3s.io" not in runcmd_text, (
        "the k3s join must NOT be in runcmd - a post-assign reboot would destroy it"
    )
    assert "systemctl enable --now fuzeinfra-vlan-join.service" in runcmd_text


def test_wait_loop_reapplies_netplan_so_a_late_assign_still_lands():
    script = _join_script()
    loop = script[script.index("while :") : script.index("PUB=")]
    assert "netplan apply" in loop, (
        "the wait loop must re-apply netplan: the vNIC can materialise only once "
        "the post-create assign lands, which is after the first apply ran"
    )


def test_off_vlan_failure_quarantines_rather_than_joining_silently():
    """The documented failure policy, asserted.

    Fail-soft (join off-VLAN, warn to a log) produced an undebuggable node whose
    kubelet is unreachable. Fail-loud (refuse to join) costs a full billing month
    per CA retry. The chosen policy is to register but be unschedulable.
    """
    script = _join_script()
    assert "--node-label fuzeinfra.io/vlan=absent" in script
    assert "--node-taint fuzeinfra.io/off-vlan=true:NoSchedule" in script, (
        "an off-VLAN node must be unschedulable, not silently used as capacity"
    )
    assert "--node-label fuzeinfra.io/vlan=present" in script, (
        "the healthy path must be labelled too, so 'no label' is itself detectable"
    )


def test_provider_id_and_pool_identity_survive_the_rewrite():
    """Regression guard: dropping these breaks CA scale-down and workload placement."""
    script = _join_script()
    assert "provider-id=contabo://fuzeinfra-prod-elastic-v2-testnode" in script
    assert "fuzeinfra.io/pool=elastic" in script
    assert "node-role=workload" in script
    assert "fuzeinfra.io/elastic=true:PreferNoSchedule" in script


def test_vlan_firewall_rule_covers_the_whole_slash22():
    """The network is 10.0.0.0/22; a /24 rule blackholes peers on 10.0.1-3.x."""
    doc = yaml.safe_load(_render_go_template(_deployed_userdata()))
    runcmd_text = "\n".join(str(c) for c in doc["runcmd"])
    assert "10.0.0.0/22" in runcmd_text
    assert "ufw allow from 10.0.0.0/24" not in runcmd_text


def _join_script() -> str:
    doc = yaml.safe_load(_render_go_template(_deployed_userdata()))
    return next(
        f for f in doc["write_files"] if f["path"] == "/usr/local/sbin/fuzeinfra-vlan-join.sh"
    )["content"]


# ---------------------------------------------------------------------------
# 3. The Terraform node module (CI runners + consumer node-requests) must carry
#    the same contract. This is the path that produced fuzeinfra-ci-runner-2.
# ---------------------------------------------------------------------------


def test_terraform_node_module_has_the_same_vlan_contract():
    tftpl = _read(MODULE_TFTPL)
    for needle in (
        "/etc/netplan/60-eth1-private.yaml",
        "--node-ip $PRIV",
        "--flannel-iface ${private_iface}",
        "fuzeinfra.io/vlan=absent",
        "fuzeinfra.io/off-vlan=true:NoSchedule",
        "fuzeinfra.io/vlan=present",
        "ConditionPathExists=!/etc/fuzeinfra-node-joined",
        "10.0.0.0/22",
    ):
        assert needle in tftpl, f"modules/contabo-k3s-node/cloud-init.tftpl is missing {needle!r}"


def test_ci_workers_are_placed_on_the_private_network():
    """fuzeinfra-ci-runner-2 was born off-VLAN because this call said nothing."""
    ci = _read(REPO / "terraform" / "contabo" / "ci-workers.tf")
    assert "private_network_id" in ci, (
        "terraform/contabo/ci-workers.tf must place CI runners on the VLAN - "
        "omitting it is exactly what produced fuzeinfra-ci-runner-2 off-VLAN"
    )
    # Comments legitimately mention name-mode to explain why it is not used, so
    # only assert on actual HCL arguments.
    hcl = [ln for ln in ci.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("private_network_name" in ln for ln in hcl), (
        "name-mode makes Terraform CREATE and reconcile the network, which against "
        "live net 60932 would try to detach the control planes and every elastic node"
    )


def test_node_module_defaults_to_the_prod_vlan():
    """Secure-by-default: a caller that says nothing must not get an off-VLAN node."""
    variables = _read(REPO / "modules" / "contabo-k3s-node" / "variables.tf")
    block = variables[variables.index('variable "private_network_id"') :]
    block = block[: block.index("\n}\n") + 3] if "\n}\n" in block else block
    assert re.search(r"default\s*=\s*60932", block), (
        "modules/contabo-k3s-node private_network_id must default to the live prod "
        "VLAN, so a provisioning path nobody has audited yet still lands on it"
    )
