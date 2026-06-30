# ---------------------------------------------------------------------------
# k3s + ingress-nginx + cert-manager + ArgoCD provisioning via SSH remote-exec.
#
# Mirrors MendysRobotics/deploy/contabo/bootstrap.sh, but driven by Terraform so
# FuzeInfra owns the execution. Idempotent — safe to re-apply.
#
# null_resource.provision triggers ONLY on server_ip change (first apply / VPS
# rebuild). Ongoing reconciliation is ArgoCD's job; routine manifest changes in
# the MendysRobotics repo are synced by ArgoCD selfHeal, not by re-provisioning.
# ---------------------------------------------------------------------------
locals {
  argocd_bootstrap_path = "${path.root}/../../argocd/applications/mendys-robotics-bootstrap.yaml"
}

resource "null_resource" "provision" {
  triggers = {
    server_ip = local.server_ip
  }

  connection {
    type        = "ssh"
    host        = local.server_ip
    user        = var.server_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "5m"
  }

  # Upload the FuzeInfra-owned ArgoCD bootstrap Application (app-of-apps root for
  # Mendys). It points ArgoCD at izzywdev/MendysRobotics deploy/argocd/applications.
  provisioner "file" {
    source      = local.argocd_bootstrap_path
    destination = "/tmp/mendys-robotics-bootstrap.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      # --- firewall: direct ingress (80/443 OPEN) ---
      "ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 6443/tcp && ufw allow 8472/udp && ufw --force enable 2>/dev/null || true",

      # --- k3s (Traefik disabled; ingress-nginx replaces it) ---
      "if ! command -v k3s >/dev/null 2>&1; then",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip} --disable traefik' sh -",
      "else",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip} --disable traefik' sh - || true",
      "fi",
      "sleep 20",
      "kubectl wait --for=condition=ready node --all --timeout=180s",

      # --- ingress-nginx ---
      "kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/cloud/deploy.yaml",
      "kubectl -n ingress-nginx wait --for=condition=available deployment/ingress-nginx-controller --timeout=180s",

      # --- cert-manager ---
      "kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.3/cert-manager.yaml",
      "kubectl -n cert-manager wait --for=condition=available deployment/cert-manager --timeout=180s",
      "kubectl -n cert-manager wait --for=condition=available deployment/cert-manager-webhook --timeout=120s",
      "sleep 10",

      # --- Let's Encrypt ClusterIssuer (HTTP-01 via ingress-nginx) ---
      # Email comes from var.letsencrypt_email, which is validated non-empty.
      "cat <<'EOF' | kubectl apply -f -",
      "apiVersion: cert-manager.io/v1",
      "kind: ClusterIssuer",
      "metadata:",
      "  name: letsencrypt-prod",
      "spec:",
      "  acme:",
      "    server: https://acme-v02.api.letsencrypt.org/directory",
      "    email: ${var.letsencrypt_email}",
      "    privateKeySecretRef:",
      "      name: letsencrypt-prod",
      "    solvers:",
      "      - http01:",
      "          ingress:",
      "            ingressClassName: nginx",
      "EOF",

      # --- ArgoCD ---
      "kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -",
      "kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml",
      "kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=300s",

      # ArgoCD runs insecure — TLS is terminated by cert-manager/ingress-nginx.
      "kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{\"data\":{\"server.insecure\":\"true\"}}'",
      "kubectl -n argocd patch configmap argocd-cm --type merge -p '{\"data\":{\"url\":\"https://argocd.${var.domain}\"}}'",
      "kubectl -n argocd rollout restart deployment/argocd-server",
      "kubectl -n argocd rollout status deployment/argocd-server --timeout=120s",

      # --- Bootstrap GitOps: app-of-apps root → izzywdev/MendysRobotics ---
      "kubectl apply -f /tmp/mendys-robotics-bootstrap.yaml",

      "echo 'Mendys provisioning complete — ArgoCD is converging from git.'",
    ]
  }
}

# ---------------------------------------------------------------------------
# Extract kubeconfig (rewrite 127.0.0.1 → server IP) for CI / the MENDYS_KUBECONFIG
# secret. base64 -w0 the file and store it as the MENDYS_KUBECONFIG GH secret.
# ---------------------------------------------------------------------------
resource "null_resource" "extract_kubeconfig" {
  depends_on = [null_resource.provision]

  triggers = {
    server_ip    = local.server_ip
    provision_id = null_resource.provision.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      ssh -o StrictHostKeyChecking=no \
          -i "${var.ssh_private_key_path}" \
          ${var.server_user}@${local.server_ip} \
          "cat /etc/rancher/k3s/k3s.yaml" \
        | sed 's/127\.0\.0\.1/${local.server_ip}/g' \
        > "${path.root}/mendys-kubeconfig.yaml"
      chmod 600 "${path.root}/mendys-kubeconfig.yaml"
    EOT
  }
}
