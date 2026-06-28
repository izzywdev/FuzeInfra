<#
.SYNOPSIS
  Bring up a local FuzeInfra Kubernetes cluster on kind (Windows-native).

.DESCRIPTION
  PowerShell port of setup-kind.sh. Creates a kind cluster, installs the
  ingress-nginx controller, cert-manager + the FuzeInfra local-CA ClusterIssuer,
  and deploys the FuzeInfra Helm chart with values-local.yaml (plus an optional
  profile overlay).

  Prereqs: docker, kind, kubectl, helm.

.PARAMETER Profile
  Optional profile overlay name from helm/fuzeinfra/profiles/<name>.yaml
  (e.g. minimal, data-stores, full). Layered on top of values-local.yaml.

.PARAMETER Reuse
  Reuse an existing cluster instead of failing if one is present (default
  behaviour already reuses; this switch is accepted for parity with CI flags).

.EXAMPLE
  .\k8s\kind\setup-kind.ps1
  .\k8s\kind\setup-kind.ps1 -Profile minimal
#>
[CmdletBinding()]
param(
  [string]$Profile = "",
  [switch]$Reuse
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$ClusterName = "fuzeinfra"
$Namespace   = "fuzeinfra"
$CertManagerVersion = "v1.16.2"   # bump deliberately

foreach ($tool in @("kind", "kubectl", "helm")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    throw "$tool not found in PATH. Install it before running this script (see docs/LOCAL_KUBERNETES.md)."
  }
}

Write-Host "==> Creating kind cluster '$ClusterName' (if missing)" -ForegroundColor Cyan
$existing = (& kind get clusters) 2>$null
if ($existing -notcontains $ClusterName) {
  & kind create cluster --config (Join-Path $ScriptDir "kind-cluster.yaml")
} else {
  & kubectl cluster-info --context "kind-$ClusterName" *> $null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "    cluster already exists and is reachable, reusing"
  } else {
    # A leftover-but-dead cluster (e.g. after a Docker restart) would wedge every
    # kubectl/helm call below — recreate it instead of reusing.
    Write-Host "    existing cluster is unreachable - recreating" -ForegroundColor Yellow
    & kind delete cluster --name $ClusterName 2>$null
    & kind create cluster --config (Join-Path $ScriptDir "kind-cluster.yaml")
  }
}
& kubectl cluster-info --context "kind-$ClusterName"

Write-Host "==> Installing ingress-nginx (kind provider)" -ForegroundColor Cyan
& kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

Write-Host "==> Waiting for ingress-nginx controller to be ready" -ForegroundColor Cyan
& kubectl wait --namespace ingress-nginx `
  --for=condition=ready pod `
  --selector=app.kubernetes.io/component=controller `
  --timeout=180s

Write-Host "==> Installing cert-manager ($CertManagerVersion)" -ForegroundColor Cyan
& kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/$CertManagerVersion/cert-manager.yaml"
Write-Host "==> Waiting for cert-manager to be ready" -ForegroundColor Cyan
& kubectl wait --namespace cert-manager --for=condition=Available --timeout=180s deployment --all

Write-Host "==> Installing the FuzeInfra local CA ClusterIssuer (shared local TLS)" -ForegroundColor Cyan
# Retry: the CRDs/webhook may take a moment to register after the deployments are Available.
$issuerApplied = $false
for ($i = 1; $i -le 5; $i++) {
  try {
    & kubectl apply -f (Join-Path $RepoRoot "k8s\local-tls\cluster-issuer.yaml")
    $issuerApplied = $true
    break
  } catch {
    Write-Host "    cert-manager webhook not ready yet, retrying ($i)..."
    Start-Sleep -Seconds 6
  }
}
if ($issuerApplied) {
  try {
    & kubectl wait --for=condition=Ready --timeout=120s clusterissuer/fuzeinfra-local-ca 2>$null
  } catch {
    Write-Host "    (CA ClusterIssuer still initializing - it'll be Ready shortly)"
  }
}

Write-Host "==> Deploying FuzeInfra Helm chart" -ForegroundColor Cyan
$helmArgs = @(
  "upgrade", "--install", "fuzeinfra", (Join-Path $RepoRoot "helm\fuzeinfra"),
  "--namespace", $Namespace, "--create-namespace",
  "-f", (Join-Path $RepoRoot "helm\fuzeinfra\values-local.yaml")
)
if ($Profile) {
  $profilePath = Join-Path $RepoRoot "helm\fuzeinfra\profiles\$Profile.yaml"
  if (-not (Test-Path $profilePath)) {
    throw "Profile '$Profile' not found at $profilePath. Available: $(Get-ChildItem (Join-Path $RepoRoot 'helm\fuzeinfra\profiles') -Filter *.yaml | ForEach-Object { $_.BaseName } | Join-String -Separator ', ')"
  }
  Write-Host "    applying profile overlay: $Profile" -ForegroundColor Yellow
  $helmArgs += @("-f", $profilePath)
}
$helmArgs += @("--wait", "--timeout", "10m")
& helm @helmArgs
if ($LASTEXITCODE -ne 0) {
  Write-Host "Helm install reported a timeout; some heavy images (elasticsearch, kafka)" -ForegroundColor Yellow
  Write-Host "may still be pulling. Check: kubectl -n $Namespace get pods" -ForegroundColor Yellow
}

Write-Host @"

==> Done.
Add these hostnames to your hosts file (all -> 127.0.0.1) or use the dnsmasq
wildcard for *.dev.local:

  127.0.0.1 grafana.dev.local prometheus.dev.local airflow.dev.local
            flower.dev.local kafka-ui.dev.local mongo-express.dev.local
            rabbitmq.dev.local neo4j.dev.local alertmanager.dev.local

Then open:  http://grafana.dev.local  (admin / admin)
Check pods: kubectl -n $Namespace get pods
Validate:   make kind-validate   (or python scripts-tools/validate_kind_deployment.py --reuse)

See docs/LOCAL_KUBERNETES.md for the full guide.
"@ -ForegroundColor Green
