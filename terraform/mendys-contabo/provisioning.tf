# ---------------------------------------------------------------------------
# Kubeconfig extraction — local-exec after the VPS is up and k3s has started.
#
# The full k3s + ArgoCD bootstrap happens in cloud-init (vps.tf user_data) so
# no SSH remote-exec runs from CI — the CI runner needs only the private key
# for this single kubeconfig pull, which happens after cloud-init finishes.
#
# This resource triggers only when the VPS is replaced (server_ip changes).
# It polls until /etc/rancher/k3s/k3s.yaml appears on the node (cloud-init
# complete), then pulls and rewrites the kubeconfig.
#
# The resulting mendys-kubeconfig.yaml is base64-encoded and stored as the
# MENDYS_KUBECONFIG GitHub Actions secret by the deploy-mendys-contabo.yml
# workflow step that follows terraform apply.
# ---------------------------------------------------------------------------

resource "null_resource" "extract_kubeconfig" {
  triggers = {
    server_ip = local.server_ip
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      KEY="${var.ssh_private_key_path}"
      HOST="${var.server_user}@${local.server_ip}"

      echo "Waiting for cloud-init bootstrap to complete on ${local.server_ip}..."

      # Poll until k3s kubeconfig exists (bootstrap wrote it) — max 20 min
      for i in $(seq 1 120); do
        if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \
               -i "$KEY" "$HOST" \
               "test -f /etc/rancher/k3s/k3s.yaml" 2>/dev/null; then
          echo "k3s kubeconfig found (attempt $i)"
          break
        fi
        echo "  attempt $i/120 — waiting 10s..."
        sleep 10
      done

      # Extract kubeconfig, rewrite 127.0.0.1 → public IP
      ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -i "$KEY" "$HOST" \
          "cat /etc/rancher/k3s/k3s.yaml" \
        | sed 's/127\.0\.0\.1/${local.server_ip}/g' \
        > "${path.root}/mendys-kubeconfig.yaml"
      chmod 600 "${path.root}/mendys-kubeconfig.yaml"
      echo "Kubeconfig written to ${path.root}/mendys-kubeconfig.yaml"
    EOT
  }
}
