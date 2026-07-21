# ---------------------------------------------------------------------------
# Contabo VPS — production server
#
# To IMPORT the existing server (one-time, adopts it without recreating):
#   terraform import contabo_instance.prod 203383846
#
# To create from scratch on a clean state: terraform apply
# To destroy: terraform destroy (permanently deletes the VPS)
# ---------------------------------------------------------------------------
resource "contabo_instance" "prod" {
  display_name = var.instance_display_name
  image_id     = var.image_id
  product_id   = var.product_id
  # ssh_keys takes numeric Contabo secret IDs. We inject the key via user_data
  # instead so new servers are provisioned correctly without pre-registering keys.
  user_data = <<-EOT
    #cloud-config
    users:
      - name: root
        ssh_authorized_keys:
          - ${var.ssh_public_key}
    runcmd:
      # Ports 80/443 are intentionally omitted — all HTTP(S) traffic flows through
      # the Cloudflare Named Tunnel (outbound-only from cloudflared).
      # 8472/udp = Flannel VXLAN overlay — required for cross-node pod networking
      # once worker nodes join (the server must accept inbound VXLAN from agents, or
      # worker pods can't reach control-plane services).
      # 10250/tcp = kubelet read-only/authenticated API. It IS needed inbound:
      # metrics-server runs OFF the control-plane (on a worker) and dials each
      # kubelet directly at <node-ip>:10250 — it does NOT ride k3s's 6443 tunnel
      # (that tunnel only backs apiserver→kubelet proxying for logs/exec, not the
      # metrics scrape). Without this rule, `kubectl top node vmi3383846` returns
      # <unknown> and HPA/scheduling signals for this node go dark (issue #318).
      # 8472 and 10250 Anywhere here are rebuild bootstrap defaults; the live runtime
      # rules are scoped to node IPs (durable fix: wireguard-native overlay / private
      # VLAN). See ufw allows below.
      # 51820/udp = Flannel WireGuard-native overlay (see provisioning.tf for the
      # --flannel-backend=wireguard-native install flag). Opened ALONGSIDE 8472/udp
      # during the transition: the flag is inert on the already-running prod
      # server (flannel backend is fixed at k3s install time), so the live server
      # still speaks VXLAN on 8472 until a deliberate, human-scheduled reprovision
      # cuts it over to WireGuard on 51820. Once that cutover happens and all
      # nodes are confirmed on WireGuard, 8472 can be closed.
      - ufw allow 22/tcp
      - ufw allow 6443/tcp
      - ufw allow 8472/udp
      - ufw allow 10250/tcp
      - ufw allow 51820/udp
      - ufw --force enable
  EOT

  lifecycle {
    # user_data is cloud-init — it only runs on first boot.
    # Changing comments or whitespace here would otherwise trigger a Contabo API
    # update (no VPS effect) AND cause null_resource.provision to re-run because
    # it depends on the instance. Ignore it to prevent spurious re-provisions.
    #
    # display_name: Contabo ignores the requested name and keeps its auto-assigned
    # value (e.g. "vmi3383846"), so terraform would otherwise show a perpetual
    # (no-op) diff trying to set it. Ignore to keep plans clean.
    ignore_changes = [user_data, display_name]
  }
}

locals {
  server_ip = contabo_instance.prod.ip_config[0].v4[0].ip
  domain    = var.domain != "" ? var.domain : "${replace(local.server_ip, ".", "-")}.nip.io"
}
