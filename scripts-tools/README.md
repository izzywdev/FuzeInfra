# Scripts & Tools Directory

This directory contains utility scripts and tools for managing the Mendys Robot Scraper Platform. These tools help with development, deployment, maintenance, and troubleshooting tasks.

## 🔧 Available Tools

### 🛡️ Environment Variable Manager (`env_manager.py`)

**⭐ NEW - Complete .env file management solution**

A comprehensive tool for safely managing `.env` files with automatic backups and validation.

#### Quick Start
```bash
# Windows users - use the batch wrapper
env_manager.bat list
env_manager.bat add NEW_VARIABLE=value
env_manager.bat backup

# Linux/Mac users - use Python directly
python env_manager.py list
python env_manager.py add NEW_VARIABLE=value
python env_manager.py backup
```

#### Key Features
- ✅ **Safe Operations**: Automatic backups before any changes
- ✅ **Sensitive Data Protection**: Masks passwords, keys, tokens in output
- ✅ **Validation**: Checks .env file syntax for common issues
- ✅ **Multiple Operations**: Add, modify, remove, backup, restore
- ✅ **Cross-Platform**: Works on Windows, Linux, and Mac
- ✅ **Integration Ready**: Designed for CI/CD and deployment workflows

#### Common Commands
```bash
# List all variables (sensitive data masked)
python env_manager.py list

# Add new variable with comment
python env_manager.py add DATABASE_URL=postgresql://localhost:5432/db --comment "Primary database"

# Modify existing variable
python env_manager.py modify OPENAI_API_KEY=sk-new-key-here

# Remove variable safely
python env_manager.py remove OLD_VARIABLE

# Create manual backup
python env_manager.py backup

# Validate .env syntax
python env_manager.py validate

# List available backups
python env_manager.py list-backups

# Restore from backup
python env_manager.py restore .env.backup.20241211_143022
```

#### Advanced Usage
```bash
# Work with custom .env file
python env_manager.py --env-file frontend/.env.local list

# Use custom backup directory
python env_manager.py --backup-dir /custom/backup/path backup
```

**📖 Full Documentation**: See [ENV_MANAGER_GUIDE.md](ENV_MANAGER_GUIDE.md) for complete usage instructions, examples, and troubleshooting.

---

### 🏷️ Version Manager (`version_manager.py`)

**⭐ NEW - Complete semantic versioning management solution**

A comprehensive tool for managing platform versions with Git integration and component synchronization.

#### Quick Start
```bash
# Windows users - use the batch wrapper
version_manager.bat current
version_manager.bat bump patch
version_manager.bat tag --push

# Linux/Mac users - use Python directly
python version_manager.py current
python version_manager.py bump minor --pre-release alpha
python version_manager.py validate
```

#### Key Features
- ✅ **Semantic Versioning**: MAJOR.MINOR.PATCH with pre-release support
- ✅ **Git Integration**: Automatic tagging and commit tracking
- ✅ **Component Sync**: Synchronized version updates across platform
- ✅ **Build Tracking**: Automatic build number and metadata tracking
- ✅ **Validation**: Comprehensive version format validation
- ✅ **Platform Integration**: Backend and frontend utilities included

#### Common Commands
```bash
# Check current version
python version_manager.py current

# Bump versions (patch/minor/major)
python version_manager.py bump patch
python version_manager.py bump minor
python version_manager.py bump major

# Pre-release versions
python version_manager.py bump minor --pre-release alpha
python version_manager.py bump pre-release --pre-release beta

# Git tagging
python version_manager.py tag --push

# Comprehensive info
python version_manager.py info

# Validate configuration
python version_manager.py validate
```

**📖 Full Documentation**: See [../docs/VERSION_MANAGEMENT.md](../docs/VERSION_MANAGEMENT.md) for complete version management guide.

---

### 🔄 CI/CD Manager (`ci_cd_manager.py`)

**⭐ NEW - Local CI/CD pipeline testing and validation**

A comprehensive tool for running the same quality checks locally that GitHub Actions runs in the cloud.

#### Quick Start
```bash
# Windows users - use the batch wrapper
ci_cd_manager.bat check-all
ci_cd_manager.bat check-python
ci_cd_manager.bat check-frontend

# Linux/Mac users - use Python directly
python ci_cd_manager.py check-all
python ci_cd_manager.py check-python
python ci_cd_manager.py check-frontend
```

#### Key Features
- ✅ **Complete Pipeline Simulation**: Run full CI/CD checks locally
- ✅ **Code Quality**: Python Black, flake8, isort + TypeScript ESLint, Prettier
- ✅ **Security Scanning**: Safety, bandit, npm audit integration
- ✅ **Test Execution**: Automated test running with coverage
- ✅ **Dependency Checking**: Validates all required tools available
- ✅ **Cross-Platform**: Works on Windows, Linux, and Mac

#### Common Commands
```bash
# Run all quality checks (recommended before committing)
python ci_cd_manager.py check-all

# Run only Python checks (faster for backend changes)
python ci_cd_manager.py check-python

# Run only frontend checks (faster for frontend changes)
python ci_cd_manager.py check-frontend
```

#### Integration with Development
```bash
# Pre-commit workflow
git add .
python ci_cd_manager.py check-all
git commit -m "feat: add new feature"

# Pre-push validation
python ci_cd_manager.py check-all && git push
```

**📖 Full Documentation**: See [../docs/CI_CD_SETUP.md](../docs/CI_CD_SETUP.md) for complete CI/CD pipeline documentation.

---

## 🎨 Figma MCP Setup (`setup_figma_mcp.bat`)

**Purpose**: Configure Figma Model Context Protocol integration for design-to-code workflows.

The Figma MCP integration brings design context directly into your AI coding workflow, enabling design-informed code generation from Figma files.

### **Available MCP Servers**
- **Official Figma Dev Mode MCP**: Real-time design context extraction via SSE
- **Community Figma MCP**: File access and commenting via NPM package

### **Key Features**
- ✅ **Interactive Setup Wizard**: Guided configuration process
- ✅ **Multiple Integration Options**: Official and community servers
- ✅ **Environment Management**: Automatic API key configuration
- ✅ **Cursor Integration**: Pre-configured `.cursor/mcp.json` setup
- ✅ **Design-to-Code**: AI-powered component generation from Figma

### **Usage**
```bash
# Interactive setup wizard
scripts-tools\setup_figma_mcp.bat

# Manual API key configuration
scripts-tools\env_manager.bat set FIGMA_API_KEY "figd_your_api_key_here"

# Check current Figma configuration
scripts-tools\env_manager.bat get FIGMA_API_KEY
```

### **Configuration Files**
```bash
# MCP server configuration
.cursor/mcp.json         # Cursor MCP server definitions

# Environment variables  
.env                     # FIGMA_API_KEY for community server

# Documentation
docs/chats/FIGMA_MCP_SETUP.md  # Complete setup guide
```

### **Integration Examples**
```text
# Design-to-code generation
"Generate React components from my selected Figma frames"

# Design analysis  
"Analyze this Figma file: https://figma.com/design/ABC123"

# Component extraction
"Extract design tokens and create a component library"
```

**📖 Full Documentation**: See [../docs/chats/FIGMA_MCP_SETUP.md](../docs/chats/FIGMA_MCP_SETUP.md) for complete Figma MCP setup guide.

---

## 📁 Directory Structure

```
scripts-tools/
├── env_manager.py          # Environment variable management script
├── env_manager.bat         # Windows batch wrapper for env_manager.py
├── ENV_MANAGER_GUIDE.md    # Comprehensive env_manager documentation
├── version_manager.py      # Semantic version management script (Python)
├── version_manager.sh      # Version management script (Linux/macOS shell)
├── version_manager.bat     # Windows batch wrapper for version_manager.py
├── ci_cd_manager.py        # CI/CD pipeline testing and validation script
├── ci_cd_manager.bat       # Windows batch wrapper for ci_cd_manager.py
├── setup_figma_mcp.bat     # Figma MCP integration setup wizard
└── README.md               # This file
```

## 🚀 Usage Guidelines

### **Development Workflow**
1. **Always backup** before making environment changes
2. **Validate** .env files after modifications
3. **Use the tools** rather than manually editing sensitive files
4. **Test changes** in development before production

### **Production Deployment**
1. **Backup existing .env** before deployment
2. **Validate configuration** with `env_manager.py validate`
3. **Use structured approach** for environment updates
4. **Keep backup history** for rollback capabilities

### **Security Best Practices**
1. **Never commit .env files** to version control
2. **Use masked output** when debugging environment issues
3. **Maintain backup security** - backups contain sensitive data
4. **Validate permissions** on .env and backup files

## 🛠️ Development

### **Adding New Tools**
When adding new utility scripts to this directory:

1. **Follow naming convention**: Use snake_case for Python files
2. **Include help/usage**: Provide `--help` option and clear documentation
3. **Error handling**: Implement proper error handling and user feedback
4. **Integration**: Consider integration with existing platform workflows
5. **Cross-platform**: Ensure compatibility across operating systems
6. **Documentation**: Update this README with new tool information

### **Testing Tools**
Test all scripts before deployment:

```bash
# Test help functionality
python script_name.py --help

# Test with sample data
python script_name.py --dry-run

# Validate error handling
python script_name.py invalid_input
```

## 🔗 Integration Points

### **Platform Services**
These tools integrate with various platform components:

- **Backend Services**: Environment configuration management
- **Docker Compose**: Container environment variables
- **Deployment Scripts**: Automated configuration updates
- **Monitoring**: Configuration validation and health checks

### **CI/CD Pipeline**
Tools can be integrated into automated workflows:

```yaml
# Example GitHub Actions step
- name: Validate Environment
  run: |
    cd scripts-tools
    python env_manager.py validate
    python env_manager.py backup
```

### **Development Environment**
Essential for local development setup:

```bash
# Initial development setup
cd scripts-tools
python env_manager.py add DATABASE_URL=postgresql://localhost:5432/dev_db
python env_manager.py add OPENAI_API_KEY=your_dev_key_here
python env_manager.py validate
```

## 🆘 Troubleshooting

### **Common Issues**

#### **Permission Errors**
```bash
# Fix file permissions (Linux/Mac)
chmod +x env_manager.py
chmod 600 ../.env  # Secure .env file

# Fix directory permissions
chmod 755 ../backups/env/
```

#### **Python Path Issues**
```bash
# Ensure Python is available
python --version

# Use absolute path if needed
/usr/bin/python3 env_manager.py list
```

#### **Windows-Specific Issues**
```batch
REM Use the batch wrapper for convenience
env_manager.bat list

REM Or call Python directly
python env_manager.py list
```

### **Getting Help**

1. **Built-in Help**: Most scripts provide `--help` option
2. **Documentation**: Check individual guide files (e.g., ENV_MANAGER_GUIDE.md)
3. **Validation**: Use validation commands to diagnose issues
4. **Backup Recovery**: Use backup/restore functionality for recovery

## 📊 Tool Statistics

**Environment Manager Testing Results:**
- ✅ **List Command**: Successfully displays 35 environment variables
- ✅ **Backup Creation**: Automatic timestamped backups working
- ✅ **Add/Modify/Remove**: All CRUD operations functional
- ✅ **Validation**: Syntax checking working correctly
- ✅ **Security**: Sensitive data masking operational
- ✅ **Cross-Platform**: Windows batch wrapper functional

**Version Manager Testing Results:**
- ✅ **Version Parsing**: Semantic versioning fully functional
- ✅ **Version Bumping**: Patch/minor/major/pre-release working
- ✅ **Git Integration**: Automatic commit tracking and tagging
- ✅ **Component Sync**: Multi-component version management
- ✅ **Validation**: Complete version format validation
- ✅ **Platform Integration**: Backend and frontend utilities working

**Platform Integration:**
- 🔗 **Environment**: Integrates with existing `backups/env/` structure
- 🔗 **Versioning**: Central `version.json` with component synchronization
- 🔗 **Git Workflow**: Automatic tagging and branch tracking
- 🔗 **Build System**: Version metadata in build processes
- 🔗 **APIs**: Health check endpoints with version information
- 🔗 **Frontend**: TypeScript utilities for UI version display

---

💡 **Pro Tip**: Always run `python env_manager.py validate` after making environment changes to ensure your platform will start correctly.

🔐 **Security Note**: All tools in this directory respect sensitive data handling - passwords, keys, and tokens are automatically masked in output and logs. 