# Cloudflare API Token Setup Guide

> **There are TWO tokens, with different scopes.** This guide covers both.
> The authoritative scope table for the Terraform token lives in
> [`docs/TERRAFORM_CD.md`](docs/TERRAFORM_CD.md#cloudflare_api_token-scope) —
> this page links to it rather than repeating it, because the copy that used to
> live here drifted and produced under-scoped tokens that plan cleanly and then
> fail at apply. If the two ever disagree again, `docs/TERRAFORM_CD.md` wins.

| Token | Where it lives | Scope |
|---|---|---|
| **`FuzeInfra-Terraform`** | GitHub Actions secret `CLOUDFLARE_API_TOKEN` | zone + account; see Step 1 |
| **custom-hostname-api runtime** | `deploy/sealed-secrets/custom-hostname-api-secret.yaml` | two zone permissions, no account access; see Step 1b |

## Step 1: Create the Terraform token

`terraform/contabo/cloudflare.tf` manages the tunnel, Access apps, Workers **and**
the Cloudflare for SaaS fallback origin — so this token is **not DNS-only**.

### Required permissions

| Permission | Scope |
|------------|-------|
| Zone → DNS → Edit | `fuzefront.com` |
| Zone → Zone → Read | `fuzefront.com` |
| Zone → SSL and Certificates → Edit | `fuzefront.com` |
| Account → Cloudflare Tunnel → Edit | the account |
| Account → Access: Apps and Policies → Edit | the account |
| Account → Workers Scripts → Edit | the account |

> **`Zone Settings → Edit` is NOT the same as `SSL and Certificates → Edit`.**
> They sit next to each other in the permission dropdown and read similarly. Only
> the latter grants the fallback-origin write, and picking the wrong one is the
> single most likely reason an apply fails on `cloudflare_*` with error `10000`.

### Method 1: Via Cloudflare Dashboard (Recommended)

1. Go to https://dash.cloudflare.com/profile/api-tokens
2. Click **"Create Token"**
3. Choose **"Get started"** next to **"Custom token"**
4. Configure the token:

   **Token name:** `FuzeInfra-Terraform`

   **Permissions:** exactly the six rows in the table above.

> **Changing an EXISTING token keeps its value.** If you are adding a missing
> permission, edit the token in place — you will not need to update the GitHub
> secret or re-seal anything. Creating a replacement means rotating both.

   **Zone Resources:**
   ```
   Include | Specific zone | fuzefront.com
   ```

   **Account Resources:**
   ```
   Include | All accounts
   ```

5. Click **"Continue to summary"**
6. Click **"Create Token"**
7. **Copy the token** - it will only be shown once!

### Method 2: Using Cloudflare CLI (Advanced)

If you have the Cloudflare CLI installed:

```bash
# Install Cloudflare CLI if not already installed
npm install -g @cloudflare/cli
# or
pip install cloudflare

# Login to Cloudflare
cloudflare login

# Create API token (this will open browser for authentication)
cloudflare create-api-token \
  --name "FuzeInfra-Terraform" \
  --permissions "Zone:Read,DNS:Edit,SSL and Certificates:Edit,Cloudflare Tunnel:Edit,Access: Apps and Policies:Edit,Workers Scripts:Edit" \
  --resources "fuzefront.com"
```

## Step 1b: The custom-hostname-api runtime token

A **separate**, deliberately minimal token. It is held by the
`custom-hostname-api` pod, which calls Cloudflare for SaaS at runtime to issue
certificates for customer-owned domains. It must NOT be the Terraform token —
that one carries account-level access the pod has no business holding.

| Permission | Scope |
|------------|-------|
| Zone → Zone → Read | `fuzefront.com` |
| Zone → SSL and Certificates → Edit | `fuzefront.com` |

No account permissions, one zone. Seal it with `scripts/seal-secret.sh` into
`deploy/sealed-secrets/custom-hostname-api-secret.yaml` alongside
`CLOUDFLARE_ZONE_ID` and `CONSUMER_TOKEN_FUZEFRONT` — see the GITOPS GATE note in
`helm/fuzeinfra/values-contabo.yaml`.

To tell the two apart in the dashboard: the Terraform token is the only one with
**account**-level rows. The runtime token has exactly the two zone rows above.

## Step 2: Update Terraform Configuration

> **Steps 2–5 below describe the retired EC2 deployment** (`terraform/ec2-deployment`,
> hard-coded Windows paths, `*.infra.fuzefront.com` hostnames). Production now runs
> on Contabo k3s: the live config is `terraform/contabo/`, applies go through the
> `terraform-plan-apply.yml` workflow rather than a local `terraform apply`, and
> admin UIs are served at `*.prod.fuzefront.com`. Treat what follows as historical
> — only Steps 1 and 1b above are current.

Once you have your API token:

1. **Copy the token** (it will look like: `1234567890abcdef_example_token_here`)

2. **Update the terraform.tfvars file:**
   ```bash
   cd /mnt/c/Users/izzyw/source/FuzeInfra/terraform/ec2-deployment
   
   # Edit terraform.tfvars and replace PLACEHOLDER_FOR_API_TOKEN with your real token
   nano terraform.tfvars
   ```

3. **Update this line:**
   ```hcl
   cloudflare_api_token = "your_actual_api_token_here"
   ```

## Step 3: Deploy Zero Trust Access

After updating the token:

```bash
cd /mnt/c/Users/izzyw/source/FuzeInfra/terraform/ec2-deployment

# Plan the deployment
terraform plan -var-file="terraform.tfvars"

# Apply the configuration
terraform apply -var-file="terraform.tfvars"
```

## Step 4: Access Your Services

Once deployed, you'll be able to access services at:

### 🛡️ Admin Services (require izzy.weinberg@gmail.com authentication):
- **Main Dashboard**: https://infra.fuzefront.com
- **Grafana**: https://grafana.infra.fuzefront.com
- **Prometheus**: https://prometheus.infra.fuzefront.com
- **PostgreSQL Admin**: https://pgadmin.infra.fuzefront.com (if configured)
- **MongoDB Express**: https://mongo.infra.fuzefront.com
- **RabbitMQ Management**: https://rabbitmq.infra.fuzefront.com
- **Neo4j Browser**: https://neo4j.infra.fuzefront.com
- **DNS Management**: https://dns.infra.fuzefront.com

### 👨‍💻 Development Services:
- **Airflow**: https://airflow.infra.fuzefront.com
- **Flower (Celery)**: https://flower.infra.fuzefront.com
- **Kafka UI**: https://kafka.infra.fuzefront.com
- **Elasticsearch**: https://elastic.infra.fuzefront.com
- **ChromaDB**: https://chroma.infra.fuzefront.com

## Step 5: Authentication Flow

1. Visit any service URL (e.g., https://grafana.infra.fuzefront.com)
2. Cloudflare Zero Trust will challenge you for authentication
3. Enter your email: **izzy.weinberg@gmail.com**
4. Check your email for the One-Time PIN (OTP)
5. Enter the PIN to access the service
6. You'll be authenticated for all other services automatically

## Troubleshooting

### Token Permissions Issues

Cloudflare returns a generic `10000` for "this token may not write that
resource", so the error names the resource but never the missing scope. **The
plan cannot catch it** — permission is only evaluated on write, so a token
missing a permission plans perfectly cleanly and fails at apply.

If an apply fails on a `cloudflare_*` resource with `10000`, check the token's
permissions before looking anywhere else. The most common instance:

```
Error: failed to create custom hostname fallback origin: Authentication error (10000)
  with cloudflare_custom_hostname_fallback_origin.saas[0]
```

That one is always **Zone → SSL and Certificates → Edit** missing. Note again
that `Zone Settings → Edit` does not substitute for it.

### No App Launcher Visible
The app launcher will appear at: https://team-name.cloudflareaccess.com
- Replace `team-name` with your Cloudflare for Teams team name
- You can find this in your Zero Trust dashboard

### Direct Database Access
For direct database access (PostgreSQL, MongoDB, etc.), you have several options:

1. **Through pgAdmin/Adminer** (Web-based, recommended)
2. **SSH Tunneling** through the EC2 instance
3. **Cloudflare Access with desktop apps** (advanced)

## Security Notes

- 🔒 **No direct database ports** are exposed to the internet
- 🔐 **All access requires email authentication**
- 📝 **All access attempts are logged**
- 🌐 **All traffic is encrypted** through Cloudflare tunnels
- 🛡️ **DDoS protection** is automatically enabled

Your infrastructure is now enterprise-grade secure! 🚀