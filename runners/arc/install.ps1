#Requires -Version 5.1
<#
.SYNOPSIS
    Bootstrap the ARC controller and staging runner scale set on Windows.

.DESCRIPTION
    Equivalent of install.sh for Windows PowerShell / PowerShell Core.
    Requires kubectl and helm to be installed and in PATH.

.PARAMETER Upgrade
    Upgrade existing Helm releases in place.

.PARAMETER Uninstall
    Remove the runner scale set and ARC controller.

.EXAMPLE
    .\runners\arc\install.ps1
    .\runners\arc\install.ps1 -Upgrade
    .\runners\arc\install.ps1 -Uninstall

.NOTES
    Prerequisites:
      - kubectl configured against the target cluster
      - helm >= 3.10  (https://helm.sh/docs/intro/install/)
      - GitHub App / PAT Secret created FIRST (see github-secret.yaml)
      - Org runner group "staging-runners" created in GitHub UI
#>

[CmdletBinding()]
param(
    [switch]$Upgrade,
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ArcControllerChart = 'oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller'
$ArcRunnerChart     = 'oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set'

$ControllerNs      = 'arc-systems'
$RunnerNs          = 'arc-runners'
$ControllerRelease = 'arc-controller'
$RunnerRelease     = 'staging'   # becomes the runner label → runs-on: [self-hosted, staging]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ControllerValuesFile = Join-Path $ScriptDir 'controller-values.yaml'
$RunnerValuesFile     = Join-Path $ScriptDir 'runner-scale-set-values.yaml'

function Invoke-Cmd {
    param([string]$Description, [string[]]$Cmd)
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Cmd[0] $Cmd[1..($Cmd.Length-1)]
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $($Cmd -join ' ')" }
}

# ---- Check prerequisites ----------------------------------------------------
foreach ($tool in @('kubectl','helm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "'$tool' not found in PATH. Install it before running this script."
    }
}

# ---- Uninstall --------------------------------------------------------------
if ($Uninstall) {
    Invoke-Cmd "Removing runner scale set ($RunnerRelease)" @(
        'helm','uninstall',$RunnerRelease,'--namespace',$RunnerNs,'--ignore-not-found'
    )
    Invoke-Cmd "Removing ARC controller ($ControllerRelease)" @(
        'helm','uninstall',$ControllerRelease,'--namespace',$ControllerNs,'--ignore-not-found'
    )
    Write-Host "`nDone. Namespaces preserved; remove manually if desired." -ForegroundColor Green
    exit 0
}

# ---- Namespaces -------------------------------------------------------------
foreach ($ns in @($ControllerNs, $RunnerNs)) {
    Write-Host "==> Ensuring namespace: $ns" -ForegroundColor Cyan
    $exists = kubectl get namespace $ns --ignore-not-found 2>$null
    if (-not $exists) {
        kubectl create namespace $ns
        if ($LASTEXITCODE -ne 0) { throw "Failed to create namespace $ns" }
    } else {
        Write-Host "    (already exists)" -ForegroundColor DarkGray
    }
}

# ---- Runner ServiceAccount --------------------------------------------------
Write-Host "==> Applying runner ServiceAccount (arc-runner-sa in $RunnerNs)" -ForegroundColor Cyan
$saYaml = @"
apiVersion: v1
kind: ServiceAccount
metadata:
  name: arc-runner-sa
  namespace: $RunnerNs
  labels:
    app.kubernetes.io/component: runner
    app.kubernetes.io/part-of: arc-staging
"@
$tmpFile = [System.IO.Path]::GetTempFileName() + '.yaml'
$saYaml | Set-Content -Path $tmpFile -Encoding UTF8
kubectl apply -f $tmpFile
Remove-Item $tmpFile -Force

# ---- ARC controller ---------------------------------------------------------
if ($Upgrade) {
    Invoke-Cmd "Upgrading ARC controller" @(
        'helm','upgrade',$ControllerRelease,$ArcControllerChart,
        '--namespace',$ControllerNs,
        '--values',$ControllerValuesFile,
        '--wait'
    )
} else {
    Invoke-Cmd "Installing ARC controller" @(
        'helm','install',$ControllerRelease,$ArcControllerChart,
        '--namespace',$ControllerNs,
        '--values',$ControllerValuesFile,
        '--wait'
    )
}

# ---- Runner scale set -------------------------------------------------------
if ($Upgrade) {
    Invoke-Cmd "Upgrading runner scale set ($RunnerRelease)" @(
        'helm','upgrade',$RunnerRelease,$ArcRunnerChart,
        '--namespace',$RunnerNs,
        '--values',$RunnerValuesFile,
        '--wait'
    )
} else {
    Invoke-Cmd "Installing runner scale set ($RunnerRelease)" @(
        'helm','install',$RunnerRelease,$ArcRunnerChart,
        '--namespace',$RunnerNs,
        '--values',$RunnerValuesFile,
        '--wait'
    )
}

Write-Host ""
Write-Host "ARC controller running in namespace : $ControllerNs" -ForegroundColor Green
Write-Host "Runner scale set '$RunnerRelease' registered in : $RunnerNs" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Confirm the runner appears in GitHub:"
Write-Host "     https://github.com/organizations/izzywdev/settings/actions/runners"
Write-Host "  2. Assign the runner to the 'staging-runners' group with your repo allowlist."
Write-Host "  3. For each consumer app: apply the deployer RBAC (see runners\rbac\)."
