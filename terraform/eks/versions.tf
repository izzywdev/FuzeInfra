terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Reuse the same remote-state bucket pattern as the EC2 deployment.
  # Configure via `terraform init -backend-config=...` or edit here.
  backend "s3" {
    bucket = "fuzefront-terraform-state"
    key    = "fuzeinfra/eks/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "FuzeInfra"
      Component = "eks"
      ManagedBy = "Terraform"
    }
  }
}
