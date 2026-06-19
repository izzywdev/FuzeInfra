# Cloudflare API Token Setup Guide

## Step 1: Create Cloudflare API Token

You need to create a Cloudflare API token with the following permissions:

### Required Permissions:
1. **Zone:Read** - fuzefront.com
2. **Zone:Edit** - fuzefront.com  
3. **Zone Settings:Edit** - fuzefront.com
4. **Access:Edit** - Account level
5. **DNS:Edit** - fuzefront.com

### Method 1: Via Cloudflare Dashboard (Recommended)

1. Go to https://dash.cloudflare.com/profile/api-tokens
2. Click **"Create Token"**
3. Choose **"Get started"** next to **"Custom token"**
4. Configure the token:

   **Token name:** `FuzeInfra-Terraform`
   
   **Permissions:**
   ```
   Zone | Zone:Read | Include | Specific zone | fuzefront.com
   Zone | Zone:Edit | Include | Specific zone | fuzefront.com  
   Zone | Zone Settings:Edit | Include | Specific zone | fuzefront.com
   Zone | DNS:Edit | Include | Specific zone | fuzefront.com
   Account | Cloudflare Access:Edit | Include | All accounts
   ```

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
  --permissions "Zone:Read,Zone:Edit,Zone Settings:Edit,DNS:Edit,Access:Edit" \
  --resources "fuzefront.com"
```

## Step 2: Update Terraform Configuration

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
If you get permission errors, ensure your token includes:
- **Access:Edit** permission at the **Account** level (not Zone level)
- **Zone:Edit** permission for **fuzefront.com**

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