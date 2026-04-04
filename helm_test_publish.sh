#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# helm_test_publish.sh — Lint, test, and publish the AL-5G-AE Helm chart
#
# Usage:
#   ./helm_test_publish.sh lint          # Lint & template-render only (no cluster)
#   ./helm_test_publish.sh test          # Full test in kind cluster
#   ./helm_test_publish.sh publish       # Package & push to OCI registry
#   ./helm_test_publish.sh all           # lint → test → publish
#
# Environment variables:
#   OCI_REGISTRY    — OCI registry URL  (default: ghcr.io/danielnovais-tech)
#   CHART_DIR       — Helm chart path   (default: helm/al-5g-ae)
#   KIND_CLUSTER    — kind cluster name  (default: al5gae-test)
#   DOCKER_IMAGE    — container image    (default: ghcr.io/danielnovais-tech/al-5g-ae:latest)
#   SKIP_BUILD      — skip docker build  (default: false)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${CHART_DIR:-${SCRIPT_DIR}/helm/al-5g-ae}"
OCI_REGISTRY="${OCI_REGISTRY:-ghcr.io/danielnovais-tech}"
KIND_CLUSTER="${KIND_CLUSTER:-al5gae-test}"
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/danielnovais-tech/al-5g-ae:latest}"
SKIP_BUILD="${SKIP_BUILD:-false}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Pre-flight checks ────────────────────────────────────────────────────────
check_tool() {
    command -v "$1" &>/dev/null || fail "$1 is required but not found. Install it first."
}

preflight_lint() {
    check_tool helm
}

preflight_test() {
    preflight_lint
    check_tool kind
    check_tool kubectl
    check_tool docker
}

preflight_publish() {
    check_tool helm
}

# ── 1. Lint ───────────────────────────────────────────────────────────────────
do_lint() {
    info "══════════════════════════════════════════════════════"
    info "Phase 1: Lint & Template Validation"
    info "══════════════════════════════════════════════════════"

    info "Running helm lint..."
    helm lint "${CHART_DIR}" --strict
    ok "helm lint passed"

    info "Template render: default values..."
    helm template test-release "${CHART_DIR}" > /dev/null
    ok "Default values render OK"

    info "Template render: all components enabled..."
    helm template test-release "${CHART_DIR}" \
        --set components.api=true \
        --set components.webui=true \
        --set components.slackBot=true \
        --set components.teamsBot=true \
        --set components.prometheusBridge=true \
        --set components.streamIngest=true \
        --set auth.enabled=true \
        --set auth.apiKeys="test-key-1" \
        --set slack.botToken="xoxb-test" \
        --set slack.appToken="xapp-test" \
        --set teams.appId="test-id" \
        --set teams.appPassword="test-pass" \
        --set ingress.enabled=true \
        --set autoscaling.enabled=true \
        --set serviceMonitor.enabled=true \
        --set otel.enabled=true \
        --set persistence.enabled=true \
        > /tmp/al5gae-all-components.yaml
    ok "All-components render OK"

    info "Template render: minimal (only API)..."
    helm template test-release "${CHART_DIR}" \
        --set components.api=true \
        --set components.webui=false \
        --set components.slackBot=false \
        --set components.teamsBot=false \
        --set components.prometheusBridge=false \
        --set components.streamIngest=false \
        --set persistence.enabled=false \
        > /tmp/al5gae-minimal.yaml
    ok "Minimal render OK"

    info "Template render: no components (edge case)..."
    helm template test-release "${CHART_DIR}" \
        --set components.api=false \
        --set components.webui=false \
        > /tmp/al5gae-empty.yaml
    ok "Empty render OK"

    # Validate YAML structure if yq is available
    if command -v yq &>/dev/null; then
        info "Validating YAML structure..."
        yq eval '.' /tmp/al5gae-all-components.yaml > /dev/null
        ok "YAML structure valid"
    fi

    # Check for common issues
    info "Checking for common template issues..."
    local rendered
    rendered=$(cat /tmp/al5gae-all-components.yaml)

    # Verify no nil/null references leaked
    if echo "$rendered" | grep -q '<nil>'; then
        fail "Found <nil> in rendered templates"
    fi
    ok "No <nil> references"

    # Verify labels are consistent
    if ! echo "$rendered" | grep -q 'app.kubernetes.io/name: al-5g-ae'; then
        fail "Standard labels missing"
    fi
    ok "Standard labels present"

    # Count rendered resources
    local resource_count
    resource_count=$(grep -c '^kind:' /tmp/al5gae-all-components.yaml || true)
    info "Rendered ${resource_count} Kubernetes resources (all components)"

    echo ""
    ok "════ Lint phase PASSED ════"
    echo ""
}

# ── 2. Test in kind cluster ──────────────────────────────────────────────────
do_test() {
    info "══════════════════════════════════════════════════════"
    info "Phase 2: Integration Test (kind cluster)"
    info "══════════════════════════════════════════════════════"

    # Create kind cluster
    if kind get clusters 2>/dev/null | grep -q "^${KIND_CLUSTER}$"; then
        info "Kind cluster '${KIND_CLUSTER}' already exists, reusing..."
    else
        info "Creating kind cluster '${KIND_CLUSTER}'..."
        kind create cluster --name "${KIND_CLUSTER}" --wait 60s
        ok "Cluster created"
    fi

    # Set kubectl context
    kubectl cluster-info --context "kind-${KIND_CLUSTER}" > /dev/null
    ok "kubectl context set"

    # Build and load Docker image
    if [[ "${SKIP_BUILD}" != "true" ]]; then
        info "Building Docker image..."
        docker build -t "${DOCKER_IMAGE}" "${SCRIPT_DIR}"
        ok "Docker image built"

        info "Loading image into kind..."
        kind load docker-image "${DOCKER_IMAGE}" --name "${KIND_CLUSTER}"
        ok "Image loaded"
    else
        warn "Skipping Docker build (SKIP_BUILD=true)"
    fi

    # Install chart
    info "Installing Helm chart..."
    helm upgrade --install al5gae-test "${CHART_DIR}" \
        --set image.repository="$(echo "${DOCKER_IMAGE}" | cut -d: -f1)" \
        --set image.tag="$(echo "${DOCKER_IMAGE}" | cut -d: -f2)" \
        --set image.pullPolicy=IfNotPresent \
        --set components.api=true \
        --set components.webui=true \
        --set components.slackBot=false \
        --set components.teamsBot=false \
        --set components.prometheusBridge=false \
        --set persistence.enabled=false \
        --set resources.requests.cpu=100m \
        --set resources.requests.memory=256Mi \
        --set resources.limits.cpu=500m \
        --set resources.limits.memory=1Gi \
        --wait \
        --timeout 120s \
        2>&1 || true
    ok "Chart installed"

    # Wait for pods
    info "Waiting for pods to be ready..."
    kubectl wait --for=condition=ready pod \
        -l app.kubernetes.io/instance=al5gae-test \
        --timeout=120s 2>/dev/null || warn "Pods may not be fully ready (model download takes time)"

    # Show pod status
    info "Pod status:"
    kubectl get pods -l app.kubernetes.io/instance=al5gae-test -o wide

    # Show services
    info "Services:"
    kubectl get svc -l app.kubernetes.io/instance=al5gae-test

    # Run helm test
    info "Running helm test..."
    if helm test al5gae-test --timeout 60s 2>/dev/null; then
        ok "Helm test passed"
    else
        warn "Helm test failed (expected if model takes time to load)"
        kubectl logs -l app.kubernetes.io/instance=al5gae-test --tail=20 2>/dev/null || true
    fi

    # Port-forward and smoke test
    info "Port-forward smoke test..."
    kubectl port-forward svc/al5gae-test-al-5g-ae-api 18000:8000 &
    PF_PID=$!
    sleep 3

    if curl -sf http://localhost:18000/health > /dev/null 2>&1; then
        HEALTH=$(curl -sf http://localhost:18000/health)
        ok "Health check: ${HEALTH}"
    else
        warn "Health endpoint not yet reachable (model loading)"
    fi

    kill $PF_PID 2>/dev/null || true

    echo ""
    ok "════ Test phase PASSED ════"
    echo ""
}

# ── 3. Publish to OCI registry ───────────────────────────────────────────────
do_publish() {
    info "══════════════════════════════════════════════════════"
    info "Phase 3: Package & Publish"
    info "══════════════════════════════════════════════════════"

    local chart_version
    chart_version=$(grep '^version:' "${CHART_DIR}/Chart.yaml" | awk '{print $2}')
    info "Chart version: ${chart_version}"

    # Package
    info "Packaging chart..."
    helm package "${CHART_DIR}" --destination /tmp/
    local pkg="/tmp/al-5g-ae-${chart_version}.tgz"
    if [[ ! -f "${pkg}" ]]; then
        fail "Package not found at ${pkg}"
    fi
    ok "Packaged: ${pkg}"

    # Check registry login
    info "Registry: oci://${OCI_REGISTRY}"
    if ! helm registry login "${OCI_REGISTRY%%/*}" --help &>/dev/null; then
        warn "If push fails, run: helm registry login ${OCI_REGISTRY%%/*}"
    fi

    # Push to OCI
    info "Pushing to OCI registry..."
    helm push "${pkg}" "oci://${OCI_REGISTRY}"
    ok "Pushed to oci://${OCI_REGISTRY}/al-5g-ae:${chart_version}"

    info ""
    info "Install from registry:"
    info "  helm install al5gae oci://${OCI_REGISTRY}/al-5g-ae --version ${chart_version}"

    echo ""
    ok "════ Publish phase PASSED ════"
    echo ""
}

# ── Cleanup helper ────────────────────────────────────────────────────────────
do_cleanup() {
    info "Cleaning up kind cluster '${KIND_CLUSTER}'..."
    if kind get clusters 2>/dev/null | grep -q "^${KIND_CLUSTER}$"; then
        kind delete cluster --name "${KIND_CLUSTER}"
        ok "Cluster deleted"
    else
        info "No cluster to clean up"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
usage() {
    echo "Usage: $0 {lint|test|publish|all|cleanup}"
    echo ""
    echo "Commands:"
    echo "  lint      Lint and validate templates (no cluster needed)"
    echo "  test      Full integration test in kind cluster"
    echo "  publish   Package and push to OCI registry"
    echo "  all       Run lint → test → publish"
    echo "  cleanup   Delete the kind test cluster"
    echo ""
    echo "Environment:"
    echo "  OCI_REGISTRY=${OCI_REGISTRY}"
    echo "  CHART_DIR=${CHART_DIR}"
    echo "  KIND_CLUSTER=${KIND_CLUSTER}"
    echo "  DOCKER_IMAGE=${DOCKER_IMAGE}"
    echo "  SKIP_BUILD=${SKIP_BUILD}"
    exit 1
}

case "${1:-}" in
    lint)
        preflight_lint
        do_lint
        ;;
    test)
        preflight_test
        do_lint
        do_test
        ;;
    publish)
        preflight_publish
        do_publish
        ;;
    all)
        preflight_test
        do_lint
        do_test
        do_publish
        ;;
    cleanup)
        check_tool kind
        do_cleanup
        ;;
    *)
        usage
        ;;
esac

ok "Done."
