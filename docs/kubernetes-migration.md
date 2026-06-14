# FuzeInfra Kubernetes Migration

This document describes the migration of FuzeInfra from a Docker Compose stack
to Kubernetes — running locally on **kind** and on AWS via **Amazon EKS**, using
a single **Helm chart** as the source of truth for both.

## Why

The Docker Compose stack (`docker-compose.FuzeInfra.yml`) and the single-EC2
Terraform deployment (`terraform/ec2-deployment/`) work, but don't scale, don't
self-heal, and diverge between "local" and "cloud". Kubernetes gives us:

- The **same manifests** locally (kind) and on AWS (EKS).
- Self-healing, rolling updates, horizontal scaling (e.g. Airflow Celery workers).
- Native service discovery (`postgres`, `redis`, `kafka`, … as in-cluster DNS),
  so application connection strings are **unchanged** from the Compose setup.

## What's in this change (Phase 1)

| Area | Path | Status |
|------|------|--------|
| Helm chart (full stack) | `helm/fuzeinfra/` | ✅ renders + schema-valid |
| Local cluster (kind) | `k8s/kind/` | ✅ scripted |
| AWS cluster (EKS) | `terraform/eks/` | ✅ HCL written (`fmt` clean) |
| gp3 StorageClass for EKS | `k8s/aws/gp3-storageclass.yaml` | ✅ |
| Kafka KRaft fix (Compose) | `docker-compose.FuzeInfra.yml` | ✅ validated |

### Validation performed in CI/sandbox

- `helm lint` — passes.
- `helm template` with `values.yaml`, `values-local.yaml`, `values-aws.yaml` —
  renders 49 objects each.
- `kubeconform -strict` against Kubernetes 1.29 schemas — **49/49 valid**.
- `docker compose config` — valid.
- `terraform fmt` — clean.

> ⚠️ Not yet validated live: an actual `kind`/`EKS` deploy and `terraform plan`.
> The sandbox has no running Docker daemon and blocks `registry.terraform.io`
> (so the community VPC/EKS modules can't be fetched here). Run the live steps
> below in an environment with cluster access and registry connectivity.

## Architecture mapping (Compose → Kubernetes)

| Compose service | Kubernetes workload | Notes |
|-----------------|---------------------|-------|
| postgres, mongodb, redis, neo4j, elasticsearch, chromadb | StatefulSet + headless/ClusterIP Service + PVC | Stable network id + persistent storage |
| kafka (+ zookeeper) | **single** StatefulSet, **KRaft mode** | ZooKeeper removed — fixes startup races |
| rabbitmq | StatefulSet + PVC | |
| kafka-ui, mongo-express | Deployment + Service | |
| prometheus, grafana, alertmanager, loki | StatefulSet + PVC | Configs as ConfigMaps |
| promtail, node-exporter | DaemonSet | Per-node log/metric collection |
| nginx (reverse proxy) | **Ingress** (ingress-nginx) | `*.dev.local` / `*.infra.<domain>` |
| dnsmasq | Deployment + Service (optional) | CoreDNS handles in-cluster DNS; dnsmasq serves the wildcard dev domain. Off by default, on in `values-local`. |
| cloudflare-tunnel | Deployment (optional, token from Secret) | Off by default; enable + set `credentials.cloudflare.tunnelToken` |
| airflow-* (init/webserver/scheduler/worker/flower) | Job (Helm hook) + Deployments | CeleryExecutor, Redis broker, Postgres backend |

Stateful data services run **in-cluster** for now (StatefulSets), with a
documented path to AWS managed services later (see "Roadmap").

## Run it locally (kind)

Prereqs: `docker`, `kind`, `kubectl`, `helm`.

```bash
# One command: create cluster, install ingress-nginx, deploy the chart
make kind-up          # or: ./k8s/kind/setup-kind.sh

# Point *.dev.local at the ingress (add to /etc/hosts -> 127.0.0.1):
#   grafana.dev.local prometheus.dev.local airflow.dev.local flower.dev.local
#   kafka-ui.dev.local mongo-express.dev.local rabbitmq.dev.local neo4j.dev.local

kubectl -n fuzeinfra get pods
# Grafana:  http://grafana.dev.local   (admin / admin)
# Airflow:  http://airflow.dev.local   (admin / admin)

make kind-down        # tear everything down
```

## Deploy to AWS (EKS)

Prereqs: `terraform`, `aws` CLI (configured), `kubectl`, `helm`, and network
access to `registry.terraform.io`.

```bash
cd terraform/eks
cp terraform.tfvars.example terraform.tfvars   # edit admin_access_cidrs etc.
terraform init
terraform apply

# Wire up kubectl to the new cluster
aws eks update-kubeconfig --region us-east-1 --name fuzeinfra

# Default gp3 StorageClass for the chart's PVCs
kubectl apply -f ../../k8s/aws/gp3-storageclass.yaml

# Ingress controller (creates an AWS NLB)
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace

# Deploy FuzeInfra
helm upgrade --install fuzeinfra ../../helm/fuzeinfra \
  -n fuzeinfra --create-namespace -f ../../helm/fuzeinfra/values-aws.yaml

# Point a wildcard DNS record (*.infra.<domain>) at the ingress NLB:
kubectl -n ingress-nginx get svc ingress-nginx-controller
```

## Configuration

- **Credentials**: `helm/fuzeinfra/values.yaml > credentials`. For real
  environments, set `credentials.existingSecret` to a Secret you manage instead.
- **Storage class**: `global.storageClass` (`standard` for kind, `gp3` for EKS).
- **Domain**: `global.domain` builds ingress hostnames.
- **Enable/disable services**: every service has an `enabled` flag.
- **Scale Airflow**: `airflow.workerReplicas`.

## Roadmap (next phases)

1. **Live validation**: `kind` bring-up + smoke tests; `terraform plan/apply` on EKS.
2. **CI**: add a GitHub Actions job running `helm lint` + `kubeconform` (offline)
   and a `kind`-based integration test of the chart.
3. **TLS**: cert-manager + ClusterIssuer; enable `ingress.tls`.
4. **Managed data services (AWS)**: optional swap of in-cluster StatefulSets for
   RDS (Postgres), ElastiCache (Redis), MSK (Kafka), OpenSearch, DocumentDB.
   Disable the corresponding chart services and point `credentials`/Secret at the
   managed endpoints.
5. **DAG delivery**: replace the example-DAG ConfigMap with git-sync or a baked
   Airflow image.
6. **Autoscaling**: HPAs for stateless services and Airflow workers; Cluster
   Autoscaler / Karpenter on EKS.
