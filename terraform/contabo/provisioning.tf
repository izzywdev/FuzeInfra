# ---------------------------------------------------------------------------
# k3s + ArgoCD provisioning via SSH remote-exec
#
# Runs in order:
#   1. Install k3s (single-node, TLS SAN for external IP)
#   2. Wait for node to be Ready
#   3. Install ArgoCD via manifest
#   4. Wait for ArgoCD to be available
#   5. Apply AppProject + prod Application
#
# Re-running terraform apply is safe — k3s install is idempotent, kubectl
# apply is idempotent.  Triggers on server IP change (i.e. VPS replaced).
# ---------------------------------------------------------------------------

locals {
  argocd_project_path = "${path.root}/../../argocd/projects/fuzeinfra.yaml"
  argocd_app_path     = "${path.root}/../../argocd/applications/fuzeinfra-prod.yaml"
  values_path         = "${path.root}/../../helm/fuzeinfra/values-contabo.yaml"
}

resource "null_resource" "provision" {
  triggers = {
    server_ip  = local.server_ip
    app_sha    = filesha256(local.argocd_app_path)
    project_sha = filesha256(local.argocd_project_path)
  }

  connection {
    type        = "ssh"
    host        = local.server_ip
    user        = var.server_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "5m"
  }

  # Upload ArgoCD manifests to the server
  provisioner "file" {
    source      = local.argocd_project_path
    destination = "/tmp/argocd-project.yaml"
  }

  provisioner "file" {
    source      = local.argocd_app_path
    destination = "/tmp/argocd-app.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      # --- firewall ---
      "ufw allow 22/tcp && ufw allow 6443/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable 2>/dev/null || true",

      # --- k3s ---
      "if ! command -v k3s &>/dev/null; then",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip}' sh -",
      "else",
      "  echo 'k3s already installed, running upgrade check'",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip}' sh - || true",
      "fi",
      "sleep 15",
      "kubectl wait --for=condition=ready node --all --timeout=120s",

      # --- ArgoCD ---
      "kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -",
      "kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml",
      "kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=300s",

      # --- ArgoCD AppProject + Application ---
      "kubectl apply -f /tmp/argocd-project.yaml",
      "kubectl apply -f /tmp/argocd-app.yaml",

      # --- ArgoCD Ingress via Traefik (insecure mode: Traefik handles TLS) ---
      "kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{\"data\":{\"server.insecure\":\"true\"}}'",
      "kubectl -n argocd rollout restart deployment/argocd-server",
      "kubectl apply -f - <<'EOF'\napiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: argocd\n  namespace: argocd\nspec:\n  ingressClassName: traefik\n  rules:\n    - host: argocd.${replace(local.server_ip, \".\", \"-\")}.nip.io\n      http:\n        paths:\n          - path: /\n            pathType: Prefix\n            backend:\n              service:\n                name: argocd-server\n                port:\n                  number: 80\nEOF",

      "echo 'Provisioning complete'",
    ]
  }
}

# ---------------------------------------------------------------------------
# Extract kubeconfig from k3s, rewrite 127.0.0.1 → server IP, store locally
# Used by the github secret resource below
# ---------------------------------------------------------------------------
resource "null_resource" "extract_kubeconfig" {
  depends_on = [null_resource.provision]

  triggers = {
    server_ip = local.server_ip
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      ssh -o StrictHostKeyChecking=no \
          -i "${var.ssh_private_key_path}" \
          ${var.server_user}@${local.server_ip} \
          "cat /etc/rancher/k3s/k3s.yaml" \
        | sed 's/127\.0\.0\.1/${local.server_ip}/g' \
        > "${path.root}/k3s-kubeconfig.yaml"
      chmod 600 "${path.root}/k3s-kubeconfig.yaml"
    EOT
  }
}

