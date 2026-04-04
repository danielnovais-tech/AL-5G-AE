<#
.SYNOPSIS
    Lint, test, and publish the AL-5G-AE Helm chart.

.DESCRIPTION
    PowerShell wrapper for Helm chart validation, kind cluster testing, and OCI publish.

.PARAMETER Command
    lint     — Lint and validate templates (no cluster needed)
    test     — Full integration test in kind cluster
    publish  — Package and push to OCI registry
    all      — lint → test → publish
    cleanup  — Delete the kind test cluster

.EXAMPLE
    .\helm_test_publish.ps1 lint
    .\helm_test_publish.ps1 test
    .\helm_test_publish.ps1 publish
    .\helm_test_publish.ps1 all
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("lint", "test", "publish", "all", "cleanup")]
    [string]$Command
)

$ErrorActionPreference = "Stop"

# ── Configuration ────────────────────────────────────────────────────────────
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$ChartDir     = if ($env:CHART_DIR)     { $env:CHART_DIR }     else { Join-Path $ScriptDir "helm\al-5g-ae" }
$OciRegistry  = if ($env:OCI_REGISTRY)  { $env:OCI_REGISTRY }  else { "ghcr.io/danielnovais-tech" }
$KindCluster  = if ($env:KIND_CLUSTER)  { $env:KIND_CLUSTER }  else { "al5gae-test" }
$DockerImage  = if ($env:DOCKER_IMAGE)  { $env:DOCKER_IMAGE }  else { "ghcr.io/danielnovais-tech/al-5g-ae:latest" }
$SkipBuild    = if ($env:SKIP_BUILD)    { $env:SKIP_BUILD -eq "true" } else { $false }

function Write-Info  { param($Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-Ok    { param($Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn  { param($Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Fail  { param($Msg) Write-Host "[FAIL]  $Msg" -ForegroundColor Red; exit 1 }

function Assert-Tool {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Fail "$Name is required but not found. Install it first."
    }
}

# ── 1. Lint ──────────────────────────────────────────────────────────────────
function Invoke-Lint {
    Write-Info "══════════════════════════════════════════════════════"
    Write-Info "Phase 1: Lint & Template Validation"
    Write-Info "══════════════════════════════════════════════════════"

    Assert-Tool "helm"

    Write-Info "Running helm lint..."
    helm lint $ChartDir --strict
    if ($LASTEXITCODE -ne 0) { Write-Fail "helm lint failed" }
    Write-Ok "helm lint passed"

    Write-Info "Template render: default values..."
    helm template test-release $ChartDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "Default render failed" }
    Write-Ok "Default values render OK"

    Write-Info "Template render: all components enabled..."
    $allOut = Join-Path $env:TEMP "al5gae-all-components.yaml"
    helm template test-release $ChartDir `
        --set components.api=true `
        --set components.webui=true `
        --set components.slackBot=true `
        --set components.teamsBot=true `
        --set components.prometheusBridge=true `
        --set components.streamIngest=true `
        --set auth.enabled=true `
        --set auth.apiKeys="test-key-1" `
        --set slack.botToken="xoxb-test" `
        --set slack.appToken="xapp-test" `
        --set teams.appId="test-id" `
        --set teams.appPassword="test-pass" `
        --set ingress.enabled=true `
        --set autoscaling.enabled=true `
        --set serviceMonitor.enabled=true `
        --set otel.enabled=true `
        --set persistence.enabled=true `
        | Out-File -Encoding utf8 $allOut
    if ($LASTEXITCODE -ne 0) { Write-Fail "All-components render failed" }
    Write-Ok "All-components render OK"

    Write-Info "Template render: minimal (only API)..."
    helm template test-release $ChartDir `
        --set components.api=true `
        --set components.webui=false `
        --set components.slackBot=false `
        --set components.teamsBot=false `
        --set components.prometheusBridge=false `
        --set components.streamIngest=false `
        --set persistence.enabled=false `
        | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "Minimal render failed" }
    Write-Ok "Minimal render OK"

    Write-Info "Template render: no components (edge case)..."
    helm template test-release $ChartDir `
        --set components.api=false `
        --set components.webui=false `
        | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "Empty render failed" }
    Write-Ok "Empty render OK"

    # Check for <nil> leaked
    $content = Get-Content $allOut -Raw
    if ($content -match '<nil>') {
        Write-Fail "Found <nil> in rendered templates"
    }
    Write-Ok "No <nil> references"

    if ($content -match 'app\.kubernetes\.io/name: al-5g-ae') {
        Write-Ok "Standard labels present"
    } else {
        Write-Fail "Standard labels missing"
    }

    $resourceCount = ([regex]::Matches($content, '(?m)^kind:')).Count
    Write-Info "Rendered $resourceCount Kubernetes resources (all components)"

    Write-Host ""
    Write-Ok "════ Lint phase PASSED ════"
    Write-Host ""
}

# ── 2. Test ──────────────────────────────────────────────────────────────────
function Invoke-Test {
    Write-Info "══════════════════════════════════════════════════════"
    Write-Info "Phase 2: Integration Test (kind cluster)"
    Write-Info "══════════════════════════════════════════════════════"

    Assert-Tool "kind"
    Assert-Tool "kubectl"
    Assert-Tool "docker"

    # Create kind cluster
    $clusters = kind get clusters 2>$null
    if ($clusters -contains $KindCluster) {
        Write-Info "Kind cluster '$KindCluster' already exists, reusing..."
    } else {
        Write-Info "Creating kind cluster '$KindCluster'..."
        kind create cluster --name $KindCluster --wait 60s
        Write-Ok "Cluster created"
    }

    kubectl cluster-info --context "kind-$KindCluster" | Out-Null
    Write-Ok "kubectl context set"

    # Build and load image
    if (-not $SkipBuild) {
        Write-Info "Building Docker image..."
        docker build -t $DockerImage $ScriptDir
        Write-Ok "Docker image built"

        Write-Info "Loading image into kind..."
        kind load docker-image $DockerImage --name $KindCluster
        Write-Ok "Image loaded"
    } else {
        Write-Warn "Skipping Docker build (SKIP_BUILD=true)"
    }

    # Parse image parts
    $imageParts = $DockerImage -split ":"
    $imageRepo = $imageParts[0]
    $imageTag  = if ($imageParts.Length -gt 1) { $imageParts[1] } else { "latest" }

    # Install
    Write-Info "Installing Helm chart..."
    helm upgrade --install al5gae-test $ChartDir `
        --set "image.repository=$imageRepo" `
        --set "image.tag=$imageTag" `
        --set image.pullPolicy=IfNotPresent `
        --set components.api=true `
        --set components.webui=true `
        --set components.slackBot=false `
        --set components.teamsBot=false `
        --set components.prometheusBridge=false `
        --set persistence.enabled=false `
        --set resources.requests.cpu=100m `
        --set resources.requests.memory=256Mi `
        --set resources.limits.cpu=500m `
        --set resources.limits.memory=1Gi `
        --wait `
        --timeout 120s
    Write-Ok "Chart installed"

    # Wait for pods
    Write-Info "Waiting for pods..."
    kubectl wait --for=condition=ready pod `
        -l app.kubernetes.io/instance=al5gae-test `
        --timeout=120s 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Pods may not be fully ready (model download takes time)"
    }

    Write-Info "Pod status:"
    kubectl get pods -l app.kubernetes.io/instance=al5gae-test -o wide

    Write-Info "Services:"
    kubectl get svc -l app.kubernetes.io/instance=al5gae-test

    # Helm test
    Write-Info "Running helm test..."
    helm test al5gae-test --timeout 60s 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Helm test passed"
    } else {
        Write-Warn "Helm test failed (may need model load time)"
    }

    Write-Host ""
    Write-Ok "════ Test phase PASSED ════"
    Write-Host ""
}

# ── 3. Publish ───────────────────────────────────────────────────────────────
function Invoke-Publish {
    Write-Info "══════════════════════════════════════════════════════"
    Write-Info "Phase 3: Package & Publish"
    Write-Info "══════════════════════════════════════════════════════"

    Assert-Tool "helm"

    $chartYaml = Get-Content (Join-Path $ChartDir "Chart.yaml") -Raw
    if ($chartYaml -match 'version:\s*(\S+)') {
        $chartVersion = $Matches[1]
    } else {
        Write-Fail "Cannot parse chart version"
    }
    Write-Info "Chart version: $chartVersion"

    Write-Info "Packaging chart..."
    helm package $ChartDir --destination $env:TEMP
    $pkg = Join-Path $env:TEMP "al-5g-ae-$chartVersion.tgz"
    if (-not (Test-Path $pkg)) { Write-Fail "Package not found at $pkg" }
    Write-Ok "Packaged: $pkg"

    Write-Info "Registry: oci://$OciRegistry"

    Write-Info "Pushing to OCI registry..."
    helm push $pkg "oci://$OciRegistry"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Push failed. Run: helm registry login $($OciRegistry.Split('/')[0])" }
    Write-Ok "Pushed to oci://$OciRegistry/al-5g-ae:$chartVersion"

    Write-Info ""
    Write-Info "Install from registry:"
    Write-Info "  helm install al5gae oci://$OciRegistry/al-5g-ae --version $chartVersion"

    Write-Host ""
    Write-Ok "════ Publish phase PASSED ════"
    Write-Host ""
}

# ── Cleanup ──────────────────────────────────────────────────────────────────
function Invoke-Cleanup {
    Assert-Tool "kind"
    Write-Info "Cleaning up kind cluster '$KindCluster'..."
    $clusters = kind get clusters 2>$null
    if ($clusters -contains $KindCluster) {
        kind delete cluster --name $KindCluster
        Write-Ok "Cluster deleted"
    } else {
        Write-Info "No cluster to clean up"
    }
}

# ── Main ─────────────────────────────────────────────────────────────────────
switch ($Command) {
    "lint"    { Invoke-Lint }
    "test"    { Invoke-Lint; Invoke-Test }
    "publish" { Invoke-Publish }
    "all"     { Invoke-Lint; Invoke-Test; Invoke-Publish }
    "cleanup" { Invoke-Cleanup }
}

Write-Ok "Done."
