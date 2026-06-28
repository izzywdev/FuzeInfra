<#
.SYNOPSIS
  Delete the local FuzeInfra kind cluster (Windows-native).
.DESCRIPTION
  PowerShell mirror of teardown-kind.sh.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ClusterName = "fuzeinfra"

Write-Host "==> Deleting kind cluster '$ClusterName'" -ForegroundColor Cyan
& kind delete cluster --name $ClusterName
Write-Host "==> Done." -ForegroundColor Green
