# ---------------------------------------------------------------------------
# k3s + ArgoCD provisioning via SSH remote-exec
#
# Two resources, two scopes:
#
#   null_resource.provision   — full k3s + ArgoCD bootstrap.
#     Triggers ONLY on server_ip change (VPS replaced / first apply).
#     k3s install is idempotent; ArgoCD install is idempotent.
#     Does NOT trigger on ArgoCD manifest changes — ArgoCD auto-syncs from git.
#
#   null_resource.argocd_sync — lightweight manifest re-apply.
#     Triggers when ArgoCD project/app YAML changes.
#     Only pushes the updated YAML to the running cluster; never re-installs k3s.
#
# Separation prevents a routine commit to argocd/ from wiping cluster state
# by triggering a full re-provision cascade (which also re-runs secret patches).
# ---------------------------------------------------------------------------

locals {
  argocd_project_path        = "${path.root}/../../argocd/projects/fuzeinfra.yaml"
  argocd_app_path            = "${path.root}/../../argocd/applications/fuzeinfra-prod.yaml"
  argocd_sealed_secrets_path = "${path.root}/../../argocd/applications/sealed-secrets.yaml"
  argocd_ingress_prod_path   = "${path.root}/../../argocd/argocd-ingress-prod.yaml"
  values_path                = "${path.root}/../../helm/fuzeinfra/values-contabo.yaml"
}

resource "null_resource" "provision" {
  # Only re-run when the VPS is replaced (IP changes) or on first apply.
  # ArgoCD manifest changes are handled by null_resource.argocd_sync below.
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

  # Upload ArgoCD manifests to the server
  provisioner "file" {
    source      = local.argocd_project_path
    destination = "/tmp/argocd-project.yaml"
  }

  provisioner "file" {
    source      = local.argocd_app_path
    destination = "/tmp/argocd-app.yaml"
  }

  provisioner "file" {
    source      = local.argocd_sealed_secrets_path
    destination = "/tmp/argocd-sealed-secrets.yaml"
  }

  provisioner "file" {
    source      = local.argocd_ingress_prod_path
    destination = "/tmp/argocd-ingress-prod.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      # --- firewall ---
      # Ports 80/443 are intentionally CLOSED from the public internet.
      # All HTTP(S) traffic enters exclusively through the Cloudflare Named Tunnel,
      # which cloudflared initiates as outbound-only connections — no inbound port needed.
      # Blocking 80/443 externally prevents direct VPS access bypassing WAF + Access.
      #
      # 8472/udp = Flannel VXLAN overlay. REQUIRED once worker nodes join: without it
      # the control-plane DROPs inbound VXLAN from agents, so pods on worker nodes
      # can't reach control-plane services (CoreDNS/Postgres/Traefik) — symptom is
      # cross-node DNS timeouts / EAI_AGAIN. (The worker module already opens 8472;
      # the server never did, which broke the first worker that joined.)
      # 10250/tcp = kubelet (parity with the worker module). NOTE: 8472 is opened
      # Anywhere here, matching the worker cloud-init + the public-IP VXLAN design;
      # the overlay is unauthenticated, so moving to flannel wireguard-native or a
      # Contabo private VLAN is tracked as a hardening follow-up.
      "ufw allow 22/tcp && ufw allow 6443/tcp && ufw allow 8472/udp && ufw allow 10250/tcp && ufw --force enable 2>/dev/null || true",
      "ufw delete allow 80/tcp 2>/dev/null || true",
      "ufw delete allow 443/tcp 2>/dev/null || true",

      # --- k3s ---
      "if ! command -v k3s &>/dev/null; then",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip}' sh -",
      "else",
      "  echo 'k3s already installed, running upgrade check'",
      "  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--tls-san ${local.server_ip}' sh - || true",
      "fi",
      "sleep 15",
      "kubectl wait --for=condition=ready node --all --timeout=120s",

      # --- Lock Traefik to ClusterIP (no external LoadBalancer binding) ---
      # All HTTP(S) must come through the Cloudflare tunnel; direct VPS access is blocked.
      # HelmChartConfig overrides k3s's bundled Traefik before ArgoCD even syncs.
      "kubectl apply -f - <<'HELMCFG'",
      "apiVersion: helm.cattle.io/v1",
      "kind: HelmChartConfig",
      "metadata:",
      "  name: traefik",
      "  namespace: kube-system",
      "spec:",
      "  valuesContent: |",
      "    service:",
      "      type: ClusterIP",
      "HELMCFG",
      # Wait for k3s to reconcile Traefik to ClusterIP (it polls ~every 15s)
      "for i in $(seq 1 20); do TYPE=$(kubectl get svc traefik -n kube-system -o jsonpath='{.spec.type}' 2>/dev/null); [ \"$TYPE\" = 'ClusterIP' ] && break; sleep 5; done",

      # --- ArgoCD ---
      "kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -",
      "kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml",
      "kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=300s",

      # --- ArgoCD AppProject + Applications ---
      "kubectl apply -f /tmp/argocd-project.yaml",
      "kubectl apply -f /tmp/argocd-app.yaml",
      # Sealed Secrets controller + published public-cert Ingress.
      "kubectl apply -f /tmp/argocd-sealed-secrets.yaml",

      # --- ArgoCD Ingress via Traefik (insecure mode: Cloudflare handles TLS) ---
      "kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{\"data\":{\"server.insecure\":\"true\"}}'",
      # External URL used by ArgoCD for CORS headers and OAuth callbacks.
      # Uses prod.fuzefront.com — the Cloudflare tunnel domain.
      "kubectl -n argocd patch configmap argocd-cm --type merge -p '{\"data\":{\"url\":\"https://argocd.prod.fuzefront.com\"}}'",
      # Traefik runs as ClusterIP so Ingress never gets a LB address; without this
      # custom health check ArgoCD marks every Ingress as Progressing forever.
      "kubectl -n argocd patch configmap argocd-cm --type merge -p '{\"data\":{\"resource.customizations.health.networking.k8s.io_Ingress\":\"hs = {}\\nhs.status = \\\"Healthy\\\"\\nhs.message = \\\"Ingress is healthy (ClusterIP mode)\\\"\\nreturn hs\\n\"}}'",
      "kubectl -n argocd rollout restart deployment/argocd-server",
      "kubectl -n argocd rollout status deployment/argocd-server --timeout=60s",
      # Ingress for argocd.prod.fuzefront.com (Cloudflare tunnel → Traefik → ArgoCD)
      "kubectl apply -f /tmp/argocd-ingress-prod.yaml",

      "echo 'Provisioning complete'",
    ]
  }
}

# ---------------------------------------------------------------------------
# Lightweight ArgoCD manifest re-apply
#
# Re-pushes project/app/ingress YAML when those files change. The cluster is
# already running; this never touches k3s. Depends on provision so it waits
# for a fresh cluster on first apply or after VPS replacement.
# ---------------------------------------------------------------------------
resource "null_resource" "argocd_sync" {
  # OFF by default — see var.enable_argocd_provisioner. It SSHes with a local key
  # file that doesn't exist on CI runners, so it must not run under the CD. With
  # count = 0 the connection block (and file(var.ssh_private_key_path)) is never
  # evaluated. Argo selfHeal + argocd-register.yml cover the work it did.
  count = var.enable_argocd_provisioner ? 1 : 0

  depends_on = [null_resource.provision]

  triggers = {
    server_ip          = local.server_ip
    app_sha            = filesha256(local.argocd_app_path)
    project_sha        = filesha256(local.argocd_project_path)
    sealed_secrets_sha = filesha256(local.argocd_sealed_secrets_path)
  }

  connection {
    type        = "ssh"
    host        = local.server_ip
    user        = var.server_user
    private_key = file(var.ssh_private_key_path)
    timeout     = "2m"
  }

  provisioner "file" {
    source      = local.argocd_project_path
    destination = "/tmp/argocd-project.yaml"
  }

  provisioner "file" {
    source      = local.argocd_app_path
    destination = "/tmp/argocd-app.yaml"
  }

  provisioner "file" {
    source      = local.argocd_sealed_secrets_path
    destination = "/tmp/argocd-sealed-secrets.yaml"
  }

  provisioner "file" {
    source      = local.argocd_ingress_prod_path
    destination = "/tmp/argocd-ingress-prod.yaml"
  }

  provisioner "remote-exec" {
    inline = [
      "kubectl apply -f /tmp/argocd-project.yaml",
      "kubectl apply -f /tmp/argocd-app.yaml",
      "kubectl apply -f /tmp/argocd-sealed-secrets.yaml",
      "kubectl apply -f /tmp/argocd-ingress-prod.yaml",
      "echo 'ArgoCD manifests synced'",
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
        > "${path.root}/k3s-kubeconfig.yaml"
      chmod 600 "${path.root}/k3s-kubeconfig.yaml"
    EOT
  }
}

