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
      # 8472/udp = Flannel VXLAN overlay (bootstrap default; live runtime rule scoped
      # to known node IPs). 51820/udp = Flannel WireGuard-native overlay — required
      # since the cluster switched to wireguard-native backend (FuzeInfra#318, 2026-07-22).
      # kubelet (10250) is NOT opened: k3s tunnels agent kubelets over the outbound
      # 6443 connection, so no inbound 10250 is needed and exposing it is an
      # unnecessary risk.
      - ufw allow 22/tcp
      - ufw allow 6443/tcp
      - ufw allow 8472/udp
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
