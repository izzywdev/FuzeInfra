# ---------------------------------------------------------------------------
# Control-plane k3s config — canonical, codified, drift-checkable
#
# WHY THIS EXISTS
# ---------------
# provisioning.tf provisions only the PRIMARY server. The other two control
# planes were joined by hand during the HA (embedded-etcd) work, and their
# configs drifted apart with nobody watching:
#
#   vmi3383846       config.yaml, had tls-san
#   mendys-worker-1  NO config.yaml keys at all — server/token/flannel-backend/
#                    node-name lived as systemd CLI ARGS, which OVERRIDE
#                    config.yaml. Editing config.yaml there was a silent no-op.
#   vmi3396106       config.yaml, no tls-san
#
# None of them set `advertise-address`, so the kubernetes Service published the
# PUBLIC IPs while 6443 is firewalled on the public interface of two nodes —
# every pod on an elastic node lost API access (0/5 reachable). See
# provisioning.tf for the full story.
#
# WHAT THIS MANAGES
# -----------------
# The full config.yaml on EVERY control plane, so a future divergence is a diff
# rather than a multi-day outage.
#
# THE TOKEN IS DELIBERATELY NOT TEMPLATED. The script reads the node's existing
# token and writes it back, so the cluster join secret never enters Terraform
# state, a plan output, or this repo. That also removes any chance of writing a
# mismatched token and orphaning a control plane.
#
# GATED OFF BY DEFAULT (var.manage_control_plane_config). Applying this REWRITES
# config.yaml and RESTARTS k3s on each control plane, one at a time. That is a
# live HA control-plane operation: never let a routine apply do it. Flip the gate
# only for a deliberate, supervised run, and verify between nodes.
#
# RUNS FROM A WORKSTATION, NOT CD. Like null_resource.provision, this needs the
# SSH private key (var.ssh_private_key_path). CI is given only
# NODE_SSH_PUBLIC_KEY, so CD cannot execute it — by design, given what it does.
# ---------------------------------------------------------------------------

locals {
  # node_name must match the Kubernetes node name, which is NOT always the
  # hostname (194.163.136.242 is host vmi3410214 but node mendys-worker-1).
  control_planes = {
    vmi3383846 = {
      public_ip  = "161.97.118.134"
      private_ip = "10.0.0.6"
      node_name  = "vmi3383846"
      # The primary; joins the cluster via another member.
      server_url = "https://194.163.136.242:6443"
    }
    mendys-worker-1 = {
      public_ip  = "194.163.136.242"
      private_ip = "10.0.0.3"
      node_name  = "mendys-worker-1"
      server_url = "https://161.97.118.134:6443"
    }
    vmi3396106 = {
      public_ip  = "95.111.238.66"
      private_ip = "10.0.0.2"
      node_name  = "vmi3396106"
      server_url = "https://161.97.118.134:6443"
    }
  }

  # Rendered per node. Kept byte-identical in shape to what is live now, so the
  # first gated apply is a no-op beyond formatting.
  cp_config = {
    for k, v in local.control_planes : k => join("\n", [
      "server: ${v.server_url}",
      "token: __TOKEN__",
      "flannel-backend: wireguard-native",
      "node-name: ${v.node_name}",
      "tls-san:",
      "  - ${v.public_ip}",
      "  - ${v.private_ip}",
      "node-taint:",
      "  - \"node-role.kubernetes.io/control-plane=:PreferNoSchedule\"",
      "flannel-iface: ${var.private_iface}",
      "node-external-ip: ${v.public_ip}",
      # The line whose absence broke API access for every elastic node.
      "advertise-address: ${v.private_ip}",
    ])
  }
}

resource "null_resource" "control_plane_config" {
  for_each = var.manage_control_plane_config ? local.control_planes : {}

  triggers = {
    config = sha256(local.cp_config[each.key])
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
      "C=/etc/rancher/k3s/config.yaml",
      "U=/etc/systemd/system/k3s.service",
      "cp -f \"$C\" \"$C.bak-$(date +%s)\"",
      # Reuse the token already on the node — never templated from Terraform.
      "TOK=$(grep -oE 'K10[A-Za-z0-9:]+' \"$C\" \"$U\" 2>/dev/null | head -1 | sed 's/^[^:]*://')",
      "[ -n \"$TOK\" ] || { echo 'ERROR: no existing k3s token found on node'; exit 1; }",
      "cat > \"$C\" <<'CFG'\n${local.cp_config[each.key]}\nCFG",
      "sed -i \"s|__TOKEN__|$TOK|\" \"$C\"",
      "chmod 600 \"$C\"",
      # config.yaml must be authoritative: strip any quoted CLI args from the
      # unit, or those keys silently win over the file we just wrote.
      "grep -vE \"^[[:space:]]*'\" \"$U\" > \"$U.new\" && mv \"$U.new\" \"$U\"",
      "systemctl daemon-reload",
      "systemctl restart k3s",
      "sleep 30",
      "systemctl is-active k3s",
      # Fail loudly rather than move on to the next control plane.
      "k3s kubectl get --raw /healthz",
    ]
  }
}
