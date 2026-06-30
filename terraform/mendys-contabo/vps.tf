# ---------------------------------------------------------------------------
# Contabo VPS — MendysRobotics production server (separate from FuzeInfra's).
#
# To adopt an already-running server without recreating it:
#   terraform import contabo_instance.mendys <instanceId>
#
# Unlike FuzeInfra's own VPS, Mendys uses DIRECT ingress (ports 80/443 open)
# + Let's Encrypt HTTP-01, not a Cloudflare Tunnel.
#
# The ENTIRE cluster bootstrap (k3s + ingress-nginx + cert-manager + ArgoCD +
# app-of-apps root) is embedded here as cloud-init. No SSH remote-exec runs
# from CI — the cloud-init script runs on first boot with no external key
# requirement. This matches FuzeInfra's own CI-safe provisioning pattern.
# ---------------------------------------------------------------------------

locals {
  argocd_bootstrap_path = "${path.root}/../../argocd/applications/mendys-robotics-bootstrap.yaml"
  bootstrap_yaml        = file(local.argocd_bootstrap_path)
}

resource "contabo_instance" "mendys" {
  display_name = var.instance_display_name
  image_id     = var.image_id
  product_id   = var.product_id

  # Full bootstrap happens in cloud-init on first boot — no SSH provisioner needed.
  # Changing this field forces a VPS rebuild (intentional: new bootstrap → clean slate).
  user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tpl", {
    ssh_public_key    = var.ssh_public_key
    letsencrypt_email = var.letsencrypt_email
    domain            = var.domain
    bootstrap_yaml    = local.bootstrap_yaml
  }))

  lifecycle {
    # display_name: Contabo ignores requested display_name changes in-place.
    # user_data is NOT in ignore_changes — a change here rebuilds the VPS with
    # the new bootstrap (which is the correct behaviour for a bootstrap change).
    ignore_changes = [display_name]
  }
}

locals {
  server_ip = contabo_instance.mendys.ip_config[0].v4[0].ip
}
