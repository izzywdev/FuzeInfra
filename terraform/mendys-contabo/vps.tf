# ---------------------------------------------------------------------------
# Contabo VPS — MendysRobotics production server (separate from FuzeInfra's).
#
# To adopt an already-running server without recreating it:
#   terraform import contabo_instance.mendys <instanceId>
# ---------------------------------------------------------------------------
resource "contabo_instance" "mendys" {
  display_name = var.instance_display_name
  image_id     = var.image_id
  product_id   = var.product_id

  # Inject the SSH key + open the firewall via cloud-init (first boot only).
  # Unlike FuzeInfra (tunnel-only), Mendys uses DIRECT ingress, so 80/443 are
  # OPEN — Let's Encrypt HTTP-01 needs inbound 80, and users reach ingress-nginx
  # directly over 443.
  user_data = <<-EOT
    #cloud-config
    users:
      - name: root
        ssh_authorized_keys:
          - ${var.ssh_public_key}
    runcmd:
      - ufw allow 22/tcp
      - ufw allow 80/tcp
      - ufw allow 443/tcp
      - ufw allow 6443/tcp
      - ufw allow 8472/udp
      - ufw --force enable
  EOT

  lifecycle {
    # cloud-init runs once; Contabo also ignores requested display_name. Ignoring
    # both keeps plans clean and prevents spurious re-provision cascades.
    ignore_changes = [user_data, display_name]
  }
}

locals {
  server_ip = contabo_instance.mendys.ip_config[0].v4[0].ip
}
