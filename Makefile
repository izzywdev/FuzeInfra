# FuzeInfra - Kubernetes & Helm convenience targets.
# (Docker Compose workflow remains via ./infra-up.sh / ./infra-down.sh)

CHART      := helm/fuzeinfra
NAMESPACE  := fuzeinfra
RELEASE    := fuzeinfra
PROFILE    ?=

# Cross-platform: native Windows `make` shells to the PowerShell scripts; macOS/
# Linux (and Git Bash/WSL) use the bash scripts. The Python helpers run the same
# everywhere (python on Windows, python3 elsewhere).
ifeq ($(OS),Windows_NT)
  KIND_UP      := pwsh -NoProfile -ExecutionPolicy Bypass -File k8s/kind/setup-kind.ps1
  KIND_DOWN    := pwsh -NoProfile -ExecutionPolicy Bypass -File k8s/kind/teardown-kind.ps1
  PROFILE_FLAG := -Profile
  PY           := python
else
  KIND_UP      := ./k8s/kind/setup-kind.sh
  KIND_DOWN    := ./k8s/kind/teardown-kind.sh
  PROFILE_FLAG := --profile
  PY           := python3
endif

.PHONY: help
help:
	@echo "FuzeInfra make targets:"
	@echo "  Local Kubernetes (kind):"
	@echo "    make kind-up                     Create kind cluster + ingress + deploy full stack"
	@echo "    make kind-profile PROFILE=<name> Deploy a subset (minimal, data-stores, full)"
	@echo "    make kind-validate               Assert every enabled service is ready + reachable"
	@echo "    make kind-test                   Run the pytest suite via kubectl port-forward"
	@echo "    make kind-down                   Delete the kind cluster"
	@echo "  Docker Desktop Kubernetes (no kind needed):"
	@echo "    make dd-up                       Deploy the chart to Docker Desktop k8s"
	@echo "    make dd-down                     Uninstall the chart from Docker Desktop k8s"
	@echo "    (then: make kind-validate / kind-test run against the docker-desktop context)"
	@echo "    make k8s-deploy      Deploy/upgrade chart to current kube-context (local values)"
	@echo "    make k8s-status      Show pods in the $(NAMESPACE) namespace"
	@echo "  Chart validation (no cluster needed):"
	@echo "    make helm-lint       helm lint the chart (all overlays)"
	@echo "    make helm-template   Render the chart to stdout"
	@echo "    make kubeconform     Render + validate against k8s schemas"
	@echo "  AWS (EKS):"
	@echo "    make eks-init        terraform init (terraform/eks)"
	@echo "    make eks-plan        terraform plan"
	@echo "    make eks-apply       terraform apply"

# ----------------------------------------------------------------------------
# Local kind
# ----------------------------------------------------------------------------
.PHONY: kind-up
kind-up:
	$(KIND_UP)

.PHONY: kind-profile
kind-profile:
	@if [ -z "$(PROFILE)" ]; then echo "Usage: make kind-profile PROFILE=<minimal|data-stores|full>"; exit 1; fi
	$(KIND_UP) $(PROFILE_FLAG) $(PROFILE)

.PHONY: kind-validate
kind-validate:
	$(PY) scripts-tools/validate_kind_deployment.py --reuse

.PHONY: kind-test
kind-test:
	$(PY) scripts-tools/kind_port_forward.py -- pytest tests/ -v

.PHONY: kind-down
kind-down:
	$(KIND_DOWN)

# ----------------------------------------------------------------------------
# Docker Desktop's built-in Kubernetes (alternative to kind — needs no kind and
# works where kind can't, e.g. a Docker Desktop on cgroup v1). Enable it in
# Docker Desktop → Settings → Kubernetes. Deploys the same chart/values-local;
# kind-validate / kind-test then run against the docker-desktop context.
# ----------------------------------------------------------------------------
.PHONY: dd-up
dd-up:
	kubectl config use-context docker-desktop
	helm upgrade --install $(RELEASE) $(CHART) --kube-context docker-desktop \
	  -n $(NAMESPACE) --create-namespace \
	  -f $(CHART)/values-local.yaml

.PHONY: dd-down
dd-down:
	-helm --kube-context docker-desktop uninstall $(RELEASE) -n $(NAMESPACE)

.PHONY: k8s-deploy
k8s-deploy:
	helm upgrade --install $(RELEASE) $(CHART) \
	  -n $(NAMESPACE) --create-namespace \
	  -f $(CHART)/values-local.yaml

.PHONY: k8s-status
k8s-status:
	kubectl -n $(NAMESPACE) get pods,svc,ingress

# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
.PHONY: helm-lint
helm-lint:
	helm lint $(CHART)
	helm lint $(CHART) -f $(CHART)/values-local.yaml
	helm lint $(CHART) -f $(CHART)/values-aws.yaml

.PHONY: helm-template
helm-template:
	helm template $(RELEASE) $(CHART) -f $(CHART)/values-local.yaml

.PHONY: kubeconform
kubeconform:
	helm template $(RELEASE) $(CHART) -f $(CHART)/values-local.yaml | \
	  kubeconform -strict -summary -kubernetes-version 1.29.0

# ----------------------------------------------------------------------------
# AWS EKS
# ----------------------------------------------------------------------------
.PHONY: eks-init
eks-init:
	cd terraform/eks && terraform init

.PHONY: eks-plan
eks-plan:
	cd terraform/eks && terraform plan

.PHONY: eks-apply
eks-apply:
	cd terraform/eks && terraform apply

# ----------------------------------------------------------------------------
# Argo CD (GitOps) - see docs/gitops.md
# ----------------------------------------------------------------------------
# ENV selects which Application to bootstrap: local (kind) or aws (EKS).
ENV ?= local

.PHONY: argocd-install
argocd-install:
	./argocd/install/setup-argocd.sh $(ENV)

.PHONY: argocd-password
argocd-password:
	@kubectl -n argocd get secret argocd-initial-admin-secret \
	  -o jsonpath="{.data.password}" | base64 -d; echo

.PHONY: argocd-ui
argocd-ui:
	@echo "Argo CD UI at https://localhost:8080 (user: admin)"
	kubectl -n argocd port-forward svc/argocd-server 8080:443

.PHONY: argocd-status
argocd-status:
	kubectl -n argocd get applications

