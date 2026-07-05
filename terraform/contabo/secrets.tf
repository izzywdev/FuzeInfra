# ---------------------------------------------------------------------------
# Set KUBE_CONFIG GitHub Actions secret using the local gh CLI.
# Runs after kubeconfig is extracted from k3s.
# ---------------------------------------------------------------------------
resource "null_resource" "set_github_secret" {
  depends_on = [null_resource.extract_kubeconfig]

  triggers = {
    server_ip = local.server_ip
  }

  provisioner "local-exec" {
    command     = <<-EOT
      gh secret set KUBE_CONFIG \
        --body "$(cat "${path.root}/k3s-kubeconfig.yaml" | base64 -w0)" \
        --repo "${var.github_owner}/${var.github_repo}"
    EOT
    interpreter = ["bash", "-c"]
  }
}
