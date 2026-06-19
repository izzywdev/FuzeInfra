terraform {
  required_version = ">= 1.6"

  required_providers {
    contabo = {
      source  = "contabo/contabo"
      version = "~> 0.1"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }

  # Uncomment to store state remotely (recommended for production):
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "fuzeinfra/contabo/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "contabo" {
  oauth2_client_id     = var.contabo_client_id
  oauth2_client_secret = var.contabo_client_secret
  oauth2_user          = var.contabo_api_user
  oauth2_pass          = var.contabo_api_password
}

# Cloudflare provider is active only when cloudflare_api_token is set.
# All cloudflare_* resources in cloudflare.tf are gated on local.cloudflare_enabled.
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

provider "null" {}
provider "local" {}
provider "random" {}

