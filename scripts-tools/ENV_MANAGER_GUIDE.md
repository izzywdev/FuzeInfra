# Environment Variable Manager Guide

A comprehensive tool for safely managing `.env` files in the FuzeInfra Platform with automatic backups and validation.

## 🎯 Purpose

Since `.env` files contain sensitive data and are protected from direct editing, this tool provides a safe way to:

- ✅ **Add** new environment variables
- ✅ **Modify** existing values  
- ✅ **Remove** variables safely
- ✅ **Create automatic backups** before changes
- ✅ **Validate** .env file syntax
- ✅ **List** variables with sensitive data masking
- ✅ **Restore** from backup files

## 🚀 Quick Start

### Windows Users
```batch
# Use the convenient batch wrapper
env_manager.bat list
env_manager.bat add NEW_VARIABLE=value
env_manager.bat backup
```

### Linux/Mac Users
```bash
cd scripts-tools/
python env_manager.py list
python env_manager.py add NEW_VARIABLE=value
python env_manager.py backup
```

## 📋 Available Commands

### **📝 List Variables**
Display all environment variables (sensitive values are masked):
```bash
python env_manager.py list
```

**Output Example:**
```
📋 Environment variables in .env:
==================================================
POSTGRES_DB=fuzeinfra_db
POSTGRES_USER=fuzeinfra
POSTGRES_PASSWORD=fuze**********************
MONGODB_HOST=localhost
GRAFANA_ADMIN_PASSWORD=graf**********************
JWT_SECRET=your_**********************
```

### **➕ Add New Variable**
Add a new environment variable with optional comment:
```bash
# Basic addition
python env_manager.py add NEW_VARIABLE=new_value

# With comment
python env_manager.py add NEW_VARIABLE=new_value --comment "Description of this variable"
```

**Features:**
- ✅ Automatically creates backup before adding
- ✅ Prevents duplicate keys
- ✅ Validates KEY=VALUE format
- ✅ Adds variables at the end of file

### **🔄 Modify Existing Variable**
Change the value of an existing environment variable:
```bash
python env_manager.py modify EXISTING_VARIABLE=updated_value
```

**Features:**
- ✅ Shows old → new value comparison
- ✅ Creates backup before modification
- ✅ Preserves original line position
- ✅ Validates variable exists

### **🗑️ Remove Variable**
Safely remove an environment variable:
```bash
python env_manager.py remove VARIABLE_TO_DELETE
```

**Features:**
- ✅ Shows removed variable and value
- ✅ Creates backup before removal
- ✅ Validates variable exists
- ✅ Maintains file formatting

### **💾 Backup Management**

#### Create Manual Backup
```bash
python env_manager.py backup
```

#### List Available Backups
```bash
python env_manager.py list-backups
```

**Output Example:**
```
📋 Available backup files in backups/env:
============================================================
.env.backup.before_add.20241211_143022    2024-12-11 14:30:22 (1752 bytes)
.env.backup.20241211_142015               2024-12-11 14:20:15 (1431 bytes)
.env.backup.manual.20241211_141000        2024-12-11 14:10:00 (1431 bytes)
```

#### Restore from Backup
```bash
# Restore from specific backup
python env_manager.py restore .env.backup.20241211_142015

# Or just the filename if in backups/env directory
python env_manager.py restore .env.backup.before_add.20241211_143022
```

### **✅ Validate .env File**
Check for common syntax issues:
```bash
python env_manager.py validate
```

**Validation Checks:**
- Missing `=` signs
- Empty keys
- Multiple `=` characters (warns about potential issues)
- File existence and readability

**Output Examples:**
```bash
# Valid file
✅ .env file validation passed

# Invalid file
❌ Validation failed:
   Line 5: Missing '=' in 'INVALID_LINE'
   Line 8: Key cannot be empty in '=some_value'
```

## 🔧 Advanced Usage

### **Custom .env File Location**
Work with .env files in different locations:
```bash
python env_manager.py --env-file /path/to/custom/.env list
python env_manager.py --env-file frontend/.env.local add API_URL=http://localhost:8090
```

### **Custom Backup Directory**
Use a different backup directory:
```bash
python env_manager.py --backup-dir /custom/backup/path backup
python env_manager.py --backup-dir /custom/backup/path list-backups
```

## 🛡️ Security Features

### **Automatic Backups**
Every destructive operation creates a timestamped backup:
- `before_add` - Before adding new variables
- `before_modify` - Before changing values
- `before_remove` - Before deleting variables
- `before_restore` - Before restoring from backup

### **Sensitive Data Masking**
When listing variables, sensitive data is automatically masked:
- Keys containing: `password`, `secret`, `key`, `token`
- Shows first 4 characters + asterisks
- Full values are never displayed in logs

### **Validation & Safety**
- Prevents duplicate variable addition
- Validates file syntax before operations
- Creates backups before any modification
- Handles file encoding properly (UTF-8)
- Graceful error handling with clear messages

## 📁 File Organization

The tool integrates with the project's backup system:

```
backups/
└── env/
    ├── .env.backup.20241211_143022           # Timestamped backup
    ├── .env.backup.before_add.20241211_143022 # Pre-operation backup
    ├── .env.backup.manual.20241211_141000     # Manual backup
    └── .env.backup.before_restore.20241211_144000
```

## 🔍 Common Use Cases

### **Initial Setup**
```bash
# Create initial .env file
python env_manager.py add POSTGRES_DB=fuzeinfra_db
python env_manager.py add MONGODB_HOST=localhost
python env_manager.py add REDIS_PASSWORD=secure_redis_password
python env_manager.py add GRAFANA_ADMIN_PASSWORD=secure_grafana_password
```

### **Credential Updates**
```bash
# Backup before major changes
python env_manager.py backup

# Update passwords
python env_manager.py modify POSTGRES_PASSWORD=new_secure_password
python env_manager.py modify JWT_SECRET=new_jwt_secret_key
python env_manager.py modify ENCRYPTION_KEY=new_encryption_key

# Validate changes
python env_manager.py validate
```

### **Environment Migration**
```bash
# Development to Production migration
python env_manager.py modify POSTGRES_HOST=prod-server
python env_manager.py modify APP_ENV=production
python env_manager.py remove DEV_MODE

# Verify changes
python env_manager.py list
```

### **Infrastructure Configuration**
```bash
# Update service ports
python env_manager.py modify PROMETHEUS_PORT=9091
python env_manager.py modify GRAFANA_PORT=3002

# Update network settings
python env_manager.py modify NETWORK_NAME=FuzeInfra-prod
python env_manager.py modify COMPOSE_PROJECT_NAME=fuzeinfra-prod

# Validate configuration
python env_manager.py validate
```

### **Cleanup and Maintenance**
```bash
# Remove deprecated variables
python env_manager.py remove OLD_API_KEY
python env_manager.py remove DEPRECATED_SETTING

# Clean up old backups (manual)
ls -la backups/env/
# Remove old backups manually as needed

# Validate final state
python env_manager.py validate
```

## 🚨 Error Handling

### **Common Errors and Solutions**

#### **Variable Already Exists**
```bash
python env_manager.py add EXISTING_VAR=value
# ⚠️  Variable EXISTING_VAR already exists. Use 'modify' to change it.

# Solution: Use modify instead
python env_manager.py modify EXISTING_VAR=new_value
```

#### **Variable Not Found**
```bash
python env_manager.py modify NON_EXISTENT_VAR=value
# ⚠️  Variable NON_EXISTENT_VAR does not exist. Use 'add' to create it.

# Solution: Use add instead
python env_manager.py add NON_EXISTENT_VAR=value
```

#### **Invalid Format**
```bash
python env_manager.py add INVALID_FORMAT
# ❌ Error: add requires KEY=VALUE format

# Solution: Use proper KEY=VALUE format
python env_manager.py add VALID_FORMAT=value
```

#### **Backup Not Found**
```bash
python env_manager.py restore non_existent_backup
# ❌ Backup file not found: non_existent_backup

# Solution: List available backups first
python env_manager.py list-backups
python env_manager.py restore .env.backup.20241211_143022
```

## 🔄 Integration with Platform

### **Platform Setup Scripts**
The env_manager integrates with platform deployment:

```bash
# In deployment scripts
cd scripts-tools/
python env_manager.py validate || echo "❌ .env validation failed"
python env_manager.py backup
```

### **Docker Integration**
Environment variables are automatically used by Docker Compose:

```bash
# After updating .env
python env_manager.py modify POSTGRES_PASSWORD=new_password

# Restart services to pick up changes
docker-compose -f docker-compose.FuzeInfra.yml restart postgres
```

### **Development Workflow**
```bash
# Before making changes
python env_manager.py backup

# Make changes
python env_manager.py modify APP_DEBUG=true
python env_manager.py add NEW_FEATURE_FLAG=enabled

# Test platform
./infra-up.sh

# If issues, restore backup
cd scripts-tools/
python env_manager.py restore .env.backup.before_modify.20241211_143022
```

## 📋 Best Practices

1. **Always Backup**: Create backups before major changes
2. **Validate Regularly**: Run validation after modifications
3. **Use Comments**: Add comments for complex variables
4. **Mask Sensitive Data**: Use the list command to safely view variables
5. **Clean Backups**: Periodically remove old backup files
6. **Test Changes**: Validate platform functionality after env changes
7. **Version Control**: Never commit .env files - they're in .gitignore
8. **Document Changes**: Keep track of important environment variable changes

## 🆘 Emergency Procedures

### **Corrupted .env File**
```bash
# Check validation
python env_manager.py validate

# If corrupted, restore from backup
python env_manager.py list-backups
python env_manager.py restore .env.backup.LATEST_GOOD_BACKUP

# Verify restoration
python env_manager.py validate
```

### **Lost .env File**
```bash
# Check if backups exist
python env_manager.py list-backups

# Restore from most recent backup
python env_manager.py restore .env.backup.20241211_143022

# Verify and validate
python env_manager.py list
python env_manager.py validate
```

### **Infrastructure Won't Start**
```bash
# Check .env file
python env_manager.py validate

# Compare with working backup
python env_manager.py list

# Restore known good configuration
python env_manager.py restore .env.backup.working_config
```

This tool ensures safe and reliable management of your environment variables while maintaining the security and integrity of your sensitive configuration data for the FuzeInfra platform. 