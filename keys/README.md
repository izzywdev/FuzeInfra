# SSH Key Management for FuzeInfra

This directory contains SSH private keys for FuzeInfra EC2 instances. Keys are automatically generated during deployment and stored securely.

## 🔐 Security Rules

### ✅ What's Tracked
- `README.md` - This documentation
- `.gitkeep` - Directory structure placeholder

### ❌ What's NOT Tracked (in .gitignore)
- `*.pem` - Private key files
- `*.key` - Private key files  
- Any files containing sensitive key material

## 🗝️ Key Naming Convention

```
fuzeinfra-{instance-name}-{timestamp}.pem
```

**Examples:**
- `fuzeinfra-fuzefront-infra-20240129.pem`
- `fuzeinfra-staging-infra-20240201.pem`

## 🔧 Key Management

### Automatic Generation
Keys are automatically generated when:
- Deploying to a new EC2 instance
- Running initial Terraform deployment
- Instance doesn't have an associated key pair

### Manual Key Operations

#### Generate New Key Pair
```bash
# Generate key pair for specific instance
./scripts/generate-ssh-key.sh fuzefront-infra

# This creates:
# - keys/fuzeinfra-fuzefront-infra-{timestamp}.pem (local)
# - Uploads public key to AWS as key pair
# - Sets private key as GitHub secret
```

#### Rotate Existing Keys
```bash
# Rotate keys for security
./scripts/rotate-ssh-key.sh fuzefront-infra

# This:
# - Generates new key pair
# - Updates instance to use new key
# - Archives old key with .old extension
# - Updates GitHub secret
```

#### Validate Key Security
```bash
# Check key file permissions
./scripts/validate-keys.sh

# Ensures all .pem files have 600 permissions
# Validates key format and integrity
```

## 🛡️ Security Best Practices

### File Permissions
All private keys must have restrictive permissions:
```bash
chmod 600 keys/*.pem
```

### Key Rotation
- **Monthly rotation** recommended for production
- **Immediate rotation** if key compromise suspected
- **Automated rotation** via GitHub Actions (optional)

### Backup Strategy
```bash
# Encrypt and backup keys (for disaster recovery)
tar czf keys-backup-$(date +%Y%m%d).tar.gz keys/*.pem
gpg -c keys-backup-$(date +%Y%m%d).tar.gz
rm keys-backup-$(date +%Y%m%d).tar.gz

# Store encrypted backup securely (not in git)
```

### Access Control
- **Local access**: Only repository owner
- **GitHub secrets**: Restricted to deployment workflows
- **AWS key pairs**: Associated with specific instances only

## 🚨 Incident Response

### If Key is Compromised
1. **Immediately rotate** the compromised key
2. **Audit access logs** in CloudTrail
3. **Review instance security** for unauthorized access
4. **Update monitoring** for suspicious activity

### If Key is Lost
1. **Generate new key pair** using scripts
2. **Update instance** to use new key
3. **Test SSH connectivity** before deployment
4. **Archive old key references**

## 📊 Key Inventory

Keys are tracked automatically. To list current keys:
```bash
# List local keys
ls -la keys/*.pem

# List AWS key pairs
aws ec2 describe-key-pairs --query 'KeyPairs[?starts_with(KeyName, `fuzeinfra-`)].{Name:KeyName,Fingerprint:KeyFingerprint}'

# List GitHub secrets
gh secret list | grep -E "(SSH|KEY)"
```

## 🔍 Troubleshooting

### Permission Denied Errors
```bash
# Fix key permissions
chmod 600 keys/your-key.pem

# Test SSH connection
ssh -i keys/your-key.pem ubuntu@your-instance-ip
```

### Key Not Found
```bash
# Verify key exists locally
ls -la keys/

# Check AWS key pairs
aws ec2 describe-key-pairs

# Regenerate if needed
./scripts/generate-ssh-key.sh your-instance-name
```

### GitHub Secret Issues
```bash
# Update GitHub secret with new key
gh secret set EC2_SSH_PRIVATE_KEY < keys/your-key.pem

# Verify secret was set
gh secret list | grep EC2_SSH_PRIVATE_KEY
```

## ⚙️ Integration

### Terraform Integration
Keys are automatically:
- Generated during `terraform apply`
- Associated with EC2 instances
- Configured in launch templates

### GitHub Actions Integration
Keys are automatically:
- Retrieved from GitHub secrets
- Used for SSH connections
- Validated before deployment

### Local Development
Keys can be used for:
- Direct SSH access to instances
- Manual deployment operations
- Debugging and troubleshooting

---

**⚠️ IMPORTANT**: Never commit private keys to version control. Always verify .gitignore rules before committing changes.