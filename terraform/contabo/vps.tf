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
      - ufw allow 22/tcp
      - ufw allow 6443/tcp
      - ufw --force enable
  EOT

  lifecycle {
    # user_data is cloud-init — it only runs on first boot.
    # Changing comments or whitespace here would otherwise trigger a Contabo API
    # update (no VPS effect) AND cause null_resource.provision to re-run because
    # it depends on the instance. Ignore it to prevent spurious re-provisions.
    ignore_changes = [user_data]
  }
}

locals {
  server_ip = contabo_instance.prod.ip_config[0].v4[0].ip
  domain    = var.domain != "" ? var.domain : "${replace(local.server_ip, ".", "-")}.nip.io"
}
