#!/usr/bin/env python3
"""
Environment Manager Integration for Local Development Orchestrator
Extends the existing EnvManager to inject allocated ports into project .env files.
"""

import os
import sys
import json
import yaml
import argparse
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

# Add the envmanager submodule to the path
project_root = Path(__file__).parent.parent.parent
envmanager_path = project_root / "envmanager"
sys.path.insert(0, str(envmanager_path))

try:
    from env_manager import EnvManager
except ImportError:
    print("Error: Could not import EnvManager from envmanager submodule")
    print(f"Make sure the envmanager submodule is properly initialized at: {envmanager_path}")
    sys.exit(1)

class PortEnvInjector:
    """Handles port injection into environment files using the existing EnvManager."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the port injector with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        self.env_manager = EnvManager()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "env_file_name": ".env",
            "backup_suffix": ".backup",
            "template_dir": "templates",
            "port_variable_mapping": {
                "frontend": "FRONTEND_PORT",
                "backend": "BACKEND_PORT", 
                "database": "DATABASE_PORT",
                "cache": "CACHE_PORT"
            },
            "additional_variables": {
                "HOST": "0.0.0.0",
                "NODE_ENV": "development"
            }
        }
        
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f)
                return {**default_config, **config}
            except Exception as e:
                print(f"Warning: Could not load config file {config_file}: {e}")
                print("Using default configuration")
        
        return default_config
    
    def inject_ports_to_env(self, project_path: str, port_allocation: Dict) -> Dict:
        """Inject allocated ports into project .env file."""
        project_path = Path(project_path)
        env_file = project_path / self.config["env_file_name"]
        
        if not project_path.exists():
            return {
                "success": False,
                "error": f"Project path does not exist: {project_path}"
            }
        
        try:
            # Backup existing .env file if it exists
            if env_file.exists():
                backup_result = self.backup_existing_env(project_path)
                if not backup_result["success"]:
                    return backup_result
            
            # Prepare port variables for injection
            port_variables = self._prepare_port_variables(port_allocation)
            
            # Add additional variables
            all_variables = {
                **port_variables,
                **self.config.get("additional_variables", {})
            }
            
            # Use EnvManager to handle the environment file creation/update
            env_content = self._generate_env_content(all_variables)
            
            # Write the new .env file
            with open(env_file, 'w') as f:
                f.write(env_content)
            
            return {
                "success": True,
                "env_file": str(env_file),
                "injected_variables": list(all_variables.keys()),
                "port_mapping": port_variables,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to inject ports: {str(e)}"
            }
    
    def _prepare_port_variables(self, port_allocation: Dict) -> Dict:
        """Convert port allocation to environment variables."""
        port_variables = {}
        
        if "port_mapping" in port_allocation:
            mapping = self.config["port_variable_mapping"]
            port_mapping = port_allocation["port_mapping"]
            
            for service_type, var_name in mapping.items():
                if service_type in port_mapping and port_mapping[service_type]:
                    port_variables[var_name] = str(port_mapping[service_type])
        
        elif "allocated_ports" in port_allocation:
            # Fallback: map ports to variables in order
            ports = port_allocation["allocated_ports"]
            mapping = self.config["port_variable_mapping"]
            
            for i, (service_type, var_name) in enumerate(mapping.items()):
                if i < len(ports):
                    port_variables[var_name] = str(ports[i])
        
        return port_variables
    
    def _generate_env_content(self, variables: Dict) -> str:
        """Generate .env file content from variables."""
        lines = [
            "# Generated by Local Development Orchestrator",
            f"# Timestamp: {datetime.now().isoformat()}",
            "",
            "# Port Configuration"
        ]
        
        # Add port variables first
        port_vars = {k: v for k, v in variables.items() if "PORT" in k}
        for key, value in sorted(port_vars.items()):
            lines.append(f"{key}={value}")
        
        if port_vars:
            lines.append("")
        
        # Add other variables
        other_vars = {k: v for k, v in variables.items() if "PORT" not in k}
        if other_vars:
            lines.append("# Additional Configuration")
            for key, value in sorted(other_vars.items()):
                lines.append(f"{key}={value}")
        
        return "\n".join(lines) + "\n"
    
    def backup_existing_env(self, project_path: Path) -> Dict:
        """Backup existing .env file before modification."""
        env_file = project_path / self.config["env_file_name"]
        
        if not env_file.exists():
            return {"success": True, "message": "No existing .env file to backup"}
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{self.config['env_file_name']}{self.config['backup_suffix']}_{timestamp}"
            backup_path = project_path / backup_name
            
            shutil.copy2(env_file, backup_path)
            
            return {
                "success": True,
                "backup_path": str(backup_path),
                "message": f"Backed up existing .env to {backup_name}"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to backup .env file: {str(e)}"
            }
    
    def generate_env_from_template(self, template_path: str, variables: Dict, output_path: str) -> Dict:
        """Generate .env from template with variables."""
        template_file = Path(template_path)
        
        if not template_file.exists():
            return {
                "success": False,
                "error": f"Template file does not exist: {template_path}"
            }
        
        try:
            with open(template_file, 'r') as f:
                template_content = f.read()
            
            # Simple variable substitution
            for key, value in variables.items():
                template_content = template_content.replace(f"${{{key}}}", str(value))
                template_content = template_content.replace(f"${key}", str(value))
            
            with open(output_path, 'w') as f:
                f.write(template_content)
            
            return {
                "success": True,
                "output_path": output_path,
                "variables_substituted": list(variables.keys())
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to generate from template: {str(e)}"
            }
    
    def validate_project_env(self, project_path: str) -> Dict:
        """Validate that project has required environment variables."""
        project_path = Path(project_path)
        env_file = project_path / self.config["env_file_name"]
        
        if not env_file.exists():
            return {
                "valid": False,
                "error": f"No .env file found at {env_file}"
            }
        
        try:
            # Use EnvManager to parse the .env file
            with open(env_file, 'r') as f:
                content = f.read()
            
            # Simple validation - check for required port variables
            required_vars = set(self.config["port_variable_mapping"].values())
            found_vars = set()
            
            for line in content.split('\n'):
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    var_name = line.split('=')[0].strip()
                    found_vars.add(var_name)
            
            missing_vars = required_vars - found_vars
            
            return {
                "valid": len(missing_vars) == 0,
                "found_variables": list(found_vars),
                "missing_variables": list(missing_vars),
                "env_file": str(env_file)
            }
            
        except Exception as e:
            return {
                "valid": False,
                "error": f"Failed to validate .env file: {str(e)}"
            }

def main():
    """Command line interface for the environment injector."""
    parser = argparse.ArgumentParser(description="Environment Manager Integration for Port Injection")
    parser.add_argument("action", choices=["inject", "backup", "validate", "template"], 
                       help="Action to perform")
    parser.add_argument("project_path", help="Path to the project directory")
    parser.add_argument("--ports", type=str, help="JSON string of port allocation")
    parser.add_argument("--template", type=str, help="Template file path")
    parser.add_argument("--variables", type=str, help="JSON string of additional variables")
    parser.add_argument("--output", choices=["json", "yaml", "simple"], default="json",
                       help="Output format")
    
    args = parser.parse_args()
    
    injector = PortEnvInjector()
    
    try:
        if args.action == "inject":
            if not args.ports:
                print("Error: --ports is required for injection", file=sys.stderr)
                sys.exit(1)
            
            port_allocation = json.loads(args.ports)
            result = injector.inject_ports_to_env(args.project_path, port_allocation)
            
        elif args.action == "backup":
            result = injector.backup_existing_env(Path(args.project_path))
            
        elif args.action == "validate":
            result = injector.validate_project_env(args.project_path)
            
        elif args.action == "template":
            if not args.template:
                print("Error: --template is required for template generation", file=sys.stderr)
                sys.exit(1)
            
            variables = json.loads(args.variables) if args.variables else {}
            output_path = Path(args.project_path) / ".env"
            result = injector.generate_env_from_template(args.template, variables, str(output_path))
        
        # Output results
        if args.output == "json":
            print(json.dumps(result, indent=2))
        elif args.output == "yaml":
            print(yaml.dump(result, default_flow_style=False))
        else:  # simple
            if result.get("success", result.get("valid", False)):
                print("OK")
            else:
                print(f"ERROR: {result.get('error', 'Operation failed')}")
    
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main() 