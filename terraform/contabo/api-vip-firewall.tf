# ---------------------------------------------------------------------------
# Open 6443 on the PUBLIC interface of every durable control plane, so the
# floating API VIP can serve kubectl from whichever node currently holds it.
#
# Today only the PRIMARY control plane has 6443 open publicly (provisioning.tf:
# `ufw allow 6443/tcp` runs against local.server_ip only); the other two have it
# firewalled — which is the external SPOF the VIP design closes. `ufw allow` is
# idempotent, so this runs against all three (re-allowing on the primary is a
# no-op) rather than special-casing it.
#
# DOUBLE-GATED, and deliberately NOT a CD path:
#   * var.api_vip_enabled — opening a public apiserver port WIDENS the attack
#     surface (apiserver reachable on all 3 CPs, not one). That is the deliberate
#     trade of the VIP design and must not precede its adoption, so it is tied to
#     the VIP gate, not applied speculatively.
#   * var.manage_control_plane_config — the same supervised, workstation-only gate
#     control-planes.tf uses. This needs the SSH PRIVATE key (var.ssh_private_key_path),
#     which CI is never given (only NODE_SSH_PUBLIC_KEY), so CD cannot run it. A
#     public apiserver port is not something a routine merge should open on its own.
#
# Unlike control-planes.tf's remote-exec this does NOT restart k3s — it only adds a
# ufw rule, so it is non-disruptive even when applied to a live control plane.
# Reproducible-from-scratch note: a rebuilt CP gets 6443 from its cloud-init
# firewall; this resource is what opens it on the existing, never-wiped nodes.
# ---------------------------------------------------------------------------
resource "null_resource" "api_vip_open_6443" {
  for_each = (var.api_vip_enabled && var.manage_control_plane_config) ? local.control_planes : {}

  triggers = {
    # Re-run if the VIP gate flips on. (ufw allow is idempotent, so a spurious
    # re-run is harmless.)
    enabled = tostring(var.api_vip_enabled)
    node    = each.value.node_name
  }

  connection {
    type        = "ssh"
    host        = each.value.public_ip
    user        = var.server_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "5m"
  }

  provisioner "remote-exec" {
    inline = [
      "set -euo pipefail",
      "ufw allow 6443/tcp",
      "ufw status | grep -E '6443' || true",
    ]
  }
}
