# Hybrid proxy startup: Docker (production) or Host (fallback)
# Ensures API key is set and available to both paths
# Usage: .\startup-proxy.ps1 [docker|host]

param(
    [string]$Mode = "docker"  # docker (default) or host
)

$ErrorActionPreference = "Stop"

Write-Host "=== FuzeInfra Proxy Startup ==="
Write-Host "Mode: $Mode"

# Step 1: Verify real API key is in user environment
Write-Host ""
Write-Host "Checking ANTHROPIC_API_KEY in user environment..."
$realKey = [System.Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")

if (-not $realKey) {
    Write-Error "ANTHROPIC_API_KEY not set in user environment. Set it first with:`n  setx ANTHROPIC_API_KEY sk-ant-..."
    exit 1
}

if ($realKey -like "sk-litellm*") {
    Write-Error "ANTHROPIC_API_KEY is set to LiteLLM key, not real Anthropic key"
    exit 1
}

Write-Host "✓ Found real API key: $($realKey.Substring(0, 16))..."

# Step 2: Based on mode, start either Docker or Host proxy
if ($Mode -eq "docker") {
    Write-Host ""
    Write-Host "=== Starting Docker Proxy Stack ==="

    Set-Location "D:\source\FuzeKeys-runtime\pii-tokenizer"

    # Ensure .env has the real key
    Write-Host "Syncing API key to Docker .env..."
    $envLines = Get-Content .env | Where-Object { $_ -notmatch "^ANTHROPIC_API_KEY=" }
    $envLines += "ANTHROPIC_API_KEY=$realKey"
    $envLines | Set-Content .env
    Write-Host "✓ .env updated"

    # Start Docker stack
    Write-Host "Starting containers..."
    $env:ANTHROPIC_API_KEY = $realKey
    docker-compose up -d 2>&1 | Select-String -Pattern "Recreate|Running|Started|Stopping" | Select-Object -First 15

    Start-Sleep -Seconds 3

    # Verify
    Write-Host ""
    Write-Host "Verifying proxy..."
    $headers = @{ Authorization = "Bearer sk-litellm-master-local-dev"; "Content-Type" = "application/json" }
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/chat/completions" -Method POST `
            -Headers $headers `
            -Body '{"model":"claude-haiku","messages":[{"role":"user","content":"ok"}],"max_tokens":3}' `
            -TimeoutSec 20
        Write-Host "✓ Docker proxy LIVE: $($r.choices[0].message.content)"
    } catch {
        Write-Host "✗ Docker proxy not responding yet. Logs:"
        docker logs pii-litellm --tail 10
        exit 1
    }

} elseif ($Mode -eq "host") {
    Write-Host ""
    Write-Host "=== Starting Host Proxy ==="

    # Stop any running host proxy
    Write-Host "Stopping existing proxy..."
    Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    # Ensure port 4000 is free
    $conn = Get-NetTCPConnection -LocalPort 4000 -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "LISTEN" }
    if ($conn) {
        Write-Host "Killing process on port 4000..."
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    # Start via scheduled task (has the real key in env)
    Write-Host "Starting via scheduled task..."
    schtasks /run /tn "LiteLLM-Proxy" 2>&1 | Select-String SUCCESS

    Start-Sleep -Seconds 5

    # Verify
    Write-Host ""
    Write-Host "Verifying proxy..."
    $headers = @{ Authorization = "Bearer sk-litellm-master-local-dev"; "Content-Type" = "application/json" }
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/chat/completions" -Method POST `
            -Headers $headers `
            -Body '{"model":"claude-haiku","messages":[{"role":"user","content":"ok"}],"max_tokens":3}' `
            -TimeoutSec 20
        Write-Host "✓ Host proxy LIVE: $($r.choices[0].message.content)"
    } catch {
        Write-Host "✗ Host proxy not responding"
        exit 1
    }

} else {
    Write-Error "Invalid mode: $Mode. Use 'docker' or 'host'"
    exit 1
}

Write-Host ""
Write-Host "=== Proxy Ready ==="
Write-Host "Endpoint: http://localhost:4000"
Write-Host "Master Key: sk-litellm-master-local-dev"
Write-Host ""
Write-Host "Configure Claude Code:"
Write-Host "  ANTHROPIC_BASE_URL: http://localhost:4000"
Write-Host "  ANTHROPIC_API_KEY: sk-litellm-master-local-dev"
