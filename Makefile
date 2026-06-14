# FuzeInfra - Kubernetes & Helm convenience targets.
# (Docker Compose workflow remains via ./infra-up.sh / ./infra-down.sh)

CHART      := helm/fuzeinfra
NAMESPACE  := fuzeinfra
RELEASE    := fuzeinfra

.PHONY: help
help:
	@echo "FuzeInfra make targets:"
	@echo "  Local Kubernetes (kind):"
	@echo "    make kind-up         Create kind cluster + ingress + deploy chart"
	@echo "    make kind-down       Delete the kind cluster"
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
	./k8s/kind/setup-kind.sh

.PHONY: kind-down
kind-down:
	./k8s/kind/teardown-kind.sh

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
