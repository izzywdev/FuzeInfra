# ---------------------------------------------------------------------------
# Kubeconfig extraction + GitHub secret storage.
#
# The full k3s + ArgoCD bootstrap runs in cloud-init (vps.tf user_data) so
# no SSH remote-exec is needed from CI runners for the initial provision.
#
# null_resource.extract_kubeconfig polls until cloud-init finishes
# (/etc/rancher/k3s/k3s.yaml appears), then pulls and rewrites the kubeconfig
# via local-exec SSH — matching FuzeInfra's own extract_kubeconfig pattern in
# terraform/contabo/provisioning.tf.
#
# null_resource.set_github_secret stores the kubeconfig as MENDYS_KUBECONFIG in
# GitHub Actions secrets via the gh CLI — matching FuzeInfra's secrets.tf
# null_resource.set_github_secret pattern exactly.
#
# Both resources trigger only on server_ip change (VPS replaced / first apply).
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

      # Poll until k3s kubeconfig exists — max 20 min (cloud-init installs k3s + ArgoCD)
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

# ---------------------------------------------------------------------------
# Store the kubeconfig as the MENDYS_KUBECONFIG GitHub Actions secret.
# Matches FuzeInfra's own null_resource.set_github_secret in
# terraform/contabo/secrets.tf — uses gh CLI local-exec with GH_TOKEN env.
# ---------------------------------------------------------------------------
resource "null_resource" "set_github_secret" {
  depends_on = [null_resource.extract_kubeconfig]

  triggers = {
    server_ip = local.server_ip
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      gh secret set MENDYS_KUBECONFIG \
        --body "$(base64 -w0 < "${path.root}/mendys-kubeconfig.yaml")" \
        --repo "${var.github_owner}/${var.github_repo}"
    EOT
  }
}
