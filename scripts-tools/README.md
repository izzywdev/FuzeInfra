# FuzeInfra Scripts & Tools

This directory contains management tools and utilities for the FuzeInfra shared infrastructure platform.

## 🛠️ Available Tools

### Infrastructure Management
- **`infra-up.sh`** / **`infra-up.bat`** - Start shared infrastructure services
- **`infra-down.sh`** - Stop shared infrastructure services

### Environment Management
- **`env_manager.py`** - Comprehensive environment variable management
- **`env_manager.bat`** - Windows wrapper for env_manager.py
- **`setup_environment.py`** - Interactive initial environment setup
- **`ENV_MANAGER_GUIDE.md`** - Detailed guide for environment management

### Version & Release Management
- **`version_manager.py`** - Semantic versioning and release management
- **`version_manager.bat`** - Windows wrapper for version_manager.py
- **`version_manager.sh`** - Linux/Mac wrapper for version_manager.py

### CI/CD Management
- **`ci_cd_manager.py`** - CI/CD pipeline management utilities
- **`ci_cd_manager.bat`** - Windows wrapper for ci_cd_manager.py

### Development Tools
- **`setup_figma_mcp.bat`** - Figma MCP server setup (development)
- **`run_tests.py`** - Complete infrastructure test suite runner

## 📋 Quick Reference

### Start/Stop Infrastructure
```bash
# Start all shared infrastructure services
./infra-up.sh  # Linux/Mac
./infra-up.bat # Windows

# Stop all shared infrastructure services  
./infra-down.sh
```

### Environment Setup (First Time)
```bash
# Interactive setup with secure password generation
python setup_environment.py

# Manual environment variable management
python env_manager.py list
python env_manager.py add NEW_VAR=value
python env_manager.py modify EXISTING_VAR=new_value
```

### Version Management
```bash
# Check current version
python version_manager.py current

# Bump version
python version_manager.py bump patch  # 1.0.0 -> 1.0.1
python version_manager.py bump minor  # 1.0.1 -> 1.1.0
python version_manager.py bump major  # 1.1.0 -> 2.0.0

# Create release
python version_manager.py release --message "Release description"
```

## 🔧 Tool Details

### Environment Manager (`env_manager.py`)
Safely manage `.env` file variables with automatic backups:
- Add, modify, remove environment variables
- Automatic backup before changes
- Sensitive data masking in output
- Validation and error checking

See `ENV_MANAGER_GUIDE.md` for comprehensive usage instructions.

### Version Manager (`version_manager.py`)
Semantic versioning with Git integration:
- Automatic version bumping
- Git tag creation
- Release notes generation
- Component tracking (infrastructure, monitoring, database)

### CI/CD Manager (`ci_cd_manager.py`)
Utilities for continuous integration and deployment:
- Pipeline configuration management
- Deployment automation helpers
- Environment promotion tools

### Setup Environment (`setup_environment.py`)
Interactive first-time setup:
- Creates `.env` from template
- Generates secure passwords
- Validates configuration
- Provides security guidance

## 🏗️ Infrastructure Scripts

### Infrastructure Startup (`infra-up.sh` / `infra-up.bat`)
Starts the complete shared infrastructure stack:
1. Creates FuzeInfra Docker network if needed
2. Starts all services via docker-compose
3. Waits for services to be healthy
4. Displays service URLs and access information

### Infrastructure Shutdown (`infra-down.sh`)
Cleanly stops all infrastructure services:
1. Stops all containers
2. Removes containers and volumes (optional)
3. Preserves data by default

## 📁 Directory Structure

```
scripts-tools/
├── env/                     # Environment backups (auto-created)
├── infra-up.sh             # Infrastructure startup (Linux/Mac)
├── infra-up.bat            # Infrastructure startup (Windows)
├── infra-down.sh           # Infrastructure shutdown
├── env_manager.py          # Environment variable management
├── env_manager.bat         # Windows wrapper
├── setup_environment.py    # Initial environment setup
├── version_manager.py      # Version management
├── version_manager.bat     # Windows wrapper
├── version_manager.sh      # Linux/Mac wrapper
├── ci_cd_manager.py        # CI/CD utilities
├── ci_cd_manager.bat       # Windows wrapper
├── ENV_MANAGER_GUIDE.md    # Environment management guide
└── README.md               # This file
```

## 🔒 Security Features

### Environment Management
- Automatic `.env` file backups before changes
- Sensitive data masking in console output
- Validation of environment variable syntax
- Secure password generation

### Access Control
- No hardcoded credentials in scripts
- Environment-based configuration
- Secure defaults for all services

## 🚀 Usage Examples

### Complete Infrastructure Setup
```bash
# 1. Initial setup (first time only)
python setup_environment.py

# 2. Start infrastructure
./infra-up.sh

# 3. Verify services are running
docker ps
```

### Environment Variable Management
```bash
# List all variables (sensitive data masked)
python env_manager.py list

# Add a new service password
python env_manager.py add NEW_SERVICE_PASSWORD=secure_password_123

# Update existing variable
python env_manager.py modify POSTGRES_PASSWORD=new_secure_password

# Remove deprecated variable
python env_manager.py remove OLD_VARIABLE

# Backup current environment
python env_manager.py backup
```

### Version Management Workflow
```bash
# Check current version
python version_manager.py current

# Make changes to infrastructure...

# Bump version and create release
python version_manager.py bump minor --message "Added Redis clustering support"

# View version history
python version_manager.py history
```

## 🔄 Integration with Main Platform

These tools are designed to work seamlessly with the FuzeInfra platform:
- All scripts respect the `.env` configuration
- Infrastructure scripts use the `FuzeInfra` network
- Version management tracks infrastructure components
- Environment tools maintain security best practices

## 📖 Additional Documentation

- **Environment Management**: See `ENV_MANAGER_GUIDE.md` for detailed usage
- **Project Templates**: See `../docs/PROJECT_TEMPLATES.md` for application integration
- **Main Platform**: See `../README.md` for overall platform documentation

## 🤝 Contributing

When adding new tools:
1. Follow the existing naming conventions
2. Include both Windows (.bat) and Linux/Mac wrappers
3. Add comprehensive error handling
4. Update this README with new tool documentation
5. Ensure tools work with the shared `.env` configuration 