# FuzeInfra EC2 Deployment - Terraform Configuration
# Deploys FuzeInfra to existing EC2 instance at infra.fuzefront.com

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
  
  # Store state in S3 (configure backend as needed)
  backend "s3" {
    bucket = "fuzefront-terraform-state"
    key    = "fuzeinfra/ec2-deployment/terraform.tfstate"
    region = "us-east-1"
  }
}

# Configure AWS Provider
provider "aws" {
  region = var.aws_region
  
  default_tags {
    tags = {
      Project     = "FuzeInfra"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Owner       = "FuzeFrontier"
    }
  }
}

# Local values are defined in ec2-instance.tf


# Security Group for FuzeInfra services
resource "aws_security_group" "fuzeinfra" {
  name_prefix = "fuzeinfra-${var.environment}-"
  description = "Security group for FuzeInfra services"
  vpc_id      = local.vpc_id

  # HTTP (nginx)
  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTPS (nginx)
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # DNS (dnsmasq) - restricted to VPC
  ingress {
    description = "DNS"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [local.vpc_cidr]
  }

  # FuzeInfra Web UIs (restricted to specific IPs)
  ingress {
    description = "FuzeInfra Web UIs"
    from_port   = 3000
    to_port     = 9999
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # SSH (existing - don't modify)
  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # All outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "fuzeinfra-${var.environment}"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Attach security group to existing instance (only if not creating new instance)
resource "aws_network_interface_sg_attachment" "fuzeinfra" {
  count                = var.create_instance ? 0 : 1
  security_group_id    = aws_security_group.fuzeinfra.id
  network_interface_id = local.network_interface_id
}

# Elastic IP for existing instance (if not already assigned and not creating new)
resource "aws_eip" "fuzeinfra" {
  count    = var.create_eip && !var.create_instance ? 1 : 0
  instance = local.instance_id
  domain   = "vpc"

  tags = {
    Name = "fuzeinfra-${var.environment}"
  }
}

# DNS configuration has been moved to cloudflare-dns.tf
# since fuzefront.com is managed by Cloudflare, not Route53

# CloudWatch Log Group for FuzeInfra
resource "aws_cloudwatch_log_group" "fuzeinfra" {
  name              = "/aws/ec2/fuzeinfra-${var.environment}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "fuzeinfra-${var.environment}"
  }
}

# IAM role for EC2 instance (CloudWatch logs, etc.)
resource "aws_iam_role" "fuzeinfra_instance" {
  name = "fuzeinfra-instance-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

# IAM policy for CloudWatch logs
resource "aws_iam_role_policy" "fuzeinfra_cloudwatch" {
  name = "fuzeinfra-cloudwatch-${var.environment}"
  role = aws_iam_role.fuzeinfra_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "${aws_cloudwatch_log_group.fuzeinfra.arn}:*"
      }
    ]
  })
}

# Instance profile
resource "aws_iam_instance_profile" "fuzeinfra" {
  name = "fuzeinfra-${var.environment}"
  role = aws_iam_role.fuzeinfra_instance.name
}

# Attach instance profile to existing instance (if not already attached)
resource "aws_iam_instance_profile_attachment" "fuzeinfra" {
  count                = var.attach_iam_profile ? 1 : 0
  instance_profile     = aws_iam_instance_profile.fuzeinfra.name
  instance_id          = data.aws_instance.fuzeinfra_instance.id
  
  lifecycle {
    ignore_changes = [instance_profile]
  }
}

# S3 bucket for deployment artifacts (optional)
resource "aws_s3_bucket" "fuzeinfra_artifacts" {
  count  = var.create_artifacts_bucket ? 1 : 0
  bucket = "fuzeinfra-artifacts-${var.environment}-${random_string.bucket_suffix.result}"
}

resource "random_string" "bucket_suffix" {
  length  = 8
  special = false
  upper   = false
}

resource "aws_s3_bucket_versioning" "fuzeinfra_artifacts" {
  count  = var.create_artifacts_bucket ? 1 : 0
  bucket = aws_s3_bucket.fuzeinfra_artifacts[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fuzeinfra_artifacts" {
  count  = var.create_artifacts_bucket ? 1 : 0
  bucket = aws_s3_bucket.fuzeinfra_artifacts[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}