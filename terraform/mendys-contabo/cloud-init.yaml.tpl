#cloud-config
# ---------------------------------------------------------------------------
# MendysRobotics — full cluster bootstrap (runs once on first VPS boot).
#
# Installs: k3s (Traefik disabled) → ingress-nginx → cert-manager →
#           Let's Encrypt HTTP-01 ClusterIssuer → ArgoCD →
#           mendys-robotics-bootstrap Application (app-of-apps).
#
# Ports 80/443 are OPEN (direct ingress; HTTP-01 challenge needs inbound 80).
# ---------------------------------------------------------------------------
users:
  - name: root
    ssh_authorized_keys:
      - ${ssh_public_key}

write_files:
  # Bootstrap script — called from runcmd so the log goes to /var/log/bootstrap.log
  - path: /root/bootstrap.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -euo pipefail
      exec > /var/log/bootstrap.log 2>&1

      echo "=== Mendys bootstrap started ==="

      # Firewall — 80/443 OPEN for direct ingress + HTTP-01 ACME
      ufw allow 22/tcp
      ufw allow 80/tcp
      ufw allow 443/tcp
      ufw allow 6443/tcp
      ufw allow 8472/udp
      ufw --force enable || true

      # k3s — Traefik disabled; ingress-nginx replaces it
      if ! command -v k3s >/dev/null 2>&1; then
        SERVER_IP=$(curl -s https://api.ipify.org)
        curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--tls-san $SERVER_IP --disable traefik" sh -
      fi
      sleep 20
      kubectl wait --for=condition=ready node --all --timeout=180s

      # ingress-nginx (cloud provider manifest — exposes the LB on host 80/443)
      kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/cloud/deploy.yaml
      kubectl -n ingress-nginx wait --for=condition=available deployment/ingress-nginx-controller --timeout=300s

      # cert-manager
      kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.3/cert-manager.yaml
      kubectl -n cert-manager wait --for=condition=available deployment/cert-manager --timeout=300s
      kubectl -n cert-manager wait --for=condition=available deployment/cert-manager-webhook --timeout=120s
      sleep 15

      # Let's Encrypt HTTP-01 ClusterIssuer
      cat <<'ISSUER' | kubectl apply -f -
      apiVersion: cert-manager.io/v1
      kind: ClusterIssuer
      metadata:
        name: letsencrypt-prod
      spec:
        acme:
          server: https://acme-v02.api.letsencrypt.org/directory
          email: ${letsencrypt_email}
          privateKeySecretRef:
            name: letsencrypt-prod
          solvers:
            - http01:
                ingress:
                  ingressClassName: nginx
      ISSUER

      # ArgoCD
      kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
      kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
      kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=300s

      # ArgoCD config — insecure mode (TLS terminated at ingress-nginx/cert-manager)
      kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge \
        -p '{"data":{"server.insecure":"true"}}'
      kubectl -n argocd patch configmap argocd-cm --type merge \
        -p '{"data":{"url":"https://argocd.${domain}"}}'
      kubectl -n argocd rollout restart deployment/argocd-server
      kubectl -n argocd rollout status deployment/argocd-server --timeout=120s

      # Bootstrap app-of-apps — points ArgoCD at izzywdev/MendysRobotics
      kubectl apply -f /tmp/mendys-robotics-bootstrap.yaml

      echo "=== Mendys bootstrap complete — ArgoCD is converging from git ==="

  # The app-of-apps bootstrap Application (managed by FuzeInfra, declared here
  # so cloud-init can apply it without needing a file upload from the runner).
  - path: /tmp/mendys-robotics-bootstrap.yaml
    content: |
      ${indent(6, bootstrap_yaml)}

runcmd:
  - bash /root/bootstrap.sh
