# SSH Key Pair Management for FuzeInfra EC2 Instances

# Generate SSH key pair for new instances
resource "tls_private_key" "fuzeinfra" {
  count     = var.create_instance ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

# Create AWS key pair from generated public key
resource "aws_key_pair" "fuzeinfra" {
  count      = var.create_instance ? 1 : 0
  key_name   = "${var.instance_name}-${formatdate("YYYYMMDD-HHMM", timestamp())}"
  public_key = tls_private_key.fuzeinfra[0].public_key_openssh

  tags = {
    Name        = "${var.instance_name}-keypair"
    Environment = var.environment
    CreatedBy   = "Terraform"
    Purpose     = "FuzeInfra SSH Access"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Store private key locally (will be handled by local-exec provisioner)
resource "local_file" "private_key" {
  count           = var.create_instance ? 1 : 0
  content         = tls_private_key.fuzeinfra[0].private_key_pem
  filename        = "${path.root}/../../keys/fuzeinfra-${var.instance_name}-${formatdate("YYYYMMDD-HHMM", timestamp())}.pem"
  file_permission = "0600"

  provisioner "local-exec" {
    command = "echo 'SSH private key saved to ${self.filename}'"
  }
}

# Backup key to GitHub secrets using local-exec
resource "null_resource" "github_secret_key" {
  count = var.create_instance && var.upload_key_to_github ? 1 : 0

  triggers = {
    key_content = tls_private_key.fuzeinfra[0].private_key_pem
    key_name    = aws_key_pair.fuzeinfra[0].key_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      echo "Uploading SSH private key to GitHub secrets..."
      echo '${tls_private_key.fuzeinfra[0].private_key_pem}' | gh secret set EC2_SSH_PRIVATE_KEY
      echo "✅ SSH private key uploaded to GitHub secrets as EC2_SSH_PRIVATE_KEY"
    EOT
  }

  depends_on = [
    tls_private_key.fuzeinfra,
    aws_key_pair.fuzeinfra,
    local_file.private_key
  ]
}

# Data source for existing key pairs (when not creating instance)
data "aws_key_pair" "existing" {
  count = var.create_instance ? 0 : 1
  
  filter {
    name   = "key-name"
    values = ["${var.instance_name}-*"]
  }
}

# Output key information
output "key_pair_info" {
  description = "SSH key pair information"
  value = var.create_instance ? {
    key_name        = aws_key_pair.fuzeinfra[0].key_name
    key_fingerprint = aws_key_pair.fuzeinfra[0].fingerprint
    private_key_file = local_file.private_key[0].filename
    created         = true
  } : {
    key_name        = try(data.aws_key_pair.existing[0].key_name, "not-found")
    key_fingerprint = try(data.aws_key_pair.existing[0].fingerprint, "not-found")
    private_key_file = "existing-key"
    created         = false
  }
  sensitive = false
}

# Validation for GitHub CLI availability
resource "null_resource" "validate_github_cli" {
  count = var.create_instance && var.upload_key_to_github ? 1 : 0

  provisioner "local-exec" {
    command = <<-EOT
      if ! command -v gh &> /dev/null; then
        echo "❌ GitHub CLI (gh) is not installed. Cannot upload key to GitHub secrets."
        echo "Install GitHub CLI: https://cli.github.com/"
        exit 1
      fi
      
      if ! gh auth status &> /dev/null; then
        echo "❌ GitHub CLI is not authenticated. Cannot upload key to GitHub secrets."
        echo "Run: gh auth login"
        exit 1
      fi
      
      echo "✅ GitHub CLI is available and authenticated"
    EOT
  }
}

# Local provisioner to set correct permissions and validate key
resource "null_resource" "key_setup" {
  count = var.create_instance ? 1 : 0

  triggers = {
    key_file = local_file.private_key[0].filename
  }

  provisioner "local-exec" {
    command = <<-EOT
      # Ensure key has correct permissions
      chmod 600 '${local_file.private_key[0].filename}'
      
      # Validate key format
      if ssh-keygen -l -f '${local_file.private_key[0].filename}' >/dev/null 2>&1; then
        echo "✅ SSH private key is valid: ${local_file.private_key[0].filename}"
      else
        echo "❌ SSH private key validation failed"
        exit 1
      fi
      
      # Create key inventory entry
      echo "$(date): Created key for ${var.instance_name} - ${aws_key_pair.fuzeinfra[0].key_name}" >> '${path.root}/../../keys/key-inventory.log'
    EOT
  }

  depends_on = [local_file.private_key]
}

# Variables for key management
variable "upload_key_to_github" {
  description = "Whether to upload private key to GitHub secrets"
  type        = bool
  default     = true
}

variable "key_algorithm" {
  description = "SSH key algorithm"
  type        = string
  default     = "RSA"
  
  validation {
    condition     = contains(["RSA", "ECDSA", "ED25519"], var.key_algorithm)
    error_message = "Key algorithm must be RSA, ECDSA, or ED25519."
  }
}

variable "key_size" {
  description = "SSH key size (bits)"
  type        = number
  default     = 4096
  
  validation {
    condition     = var.key_size >= 2048
    error_message = "Key size must be at least 2048 bits."
  }
}