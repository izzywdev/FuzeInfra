#!/usr/bin/env python3
"""
Nginx Configuration Generator for Local Development Orchestrator
Generates project-specific nginx configurations using environment variables.
"""

import os
import sys
import json
import yaml
import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

class NginxConfigGenerator:
    """Generates nginx configurations using environment variables."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the nginx generator with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "templates_dir": "templates",
            "output_dir": "../../infrastructure/shared-nginx/conf.d",
            "domain_suffix": "dev.local",
            "default_template": "fullstack.conf.template",
            "nginx_reload_command": "docker exec fuzeinfra-shared-nginx nginx -s reload",
            "nginx_test_command": "docker exec fuzeinfra-shared-nginx nginx -t"
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
    
    def analyze_template_variables(self, template_path: Path) -> Dict:
        """Analyze nginx template to discover required environment variables."""
        try:
            with open(template_path, 'r') as f:
                template_content = f.read()
            
            # Extract all ${VAR} patterns
            env_var_pattern = r'\$\{([A-Z_]+)\}'
            env_vars = set(re.findall(env_var_pattern, template_content))
            
            # Categorize variables
            port_vars = [var for var in env_vars if 'PORT' in var]
            other_vars = [var for var in env_vars if 'PORT' not in var]
            
            return {
                'success': True,
                'template_file': str(template_path),
                'total_variables': len(env_vars),
                'all_variables': sorted(list(env_vars)),
                'port_variables': sorted(port_vars),
                'other_variables': sorted(other_vars),
                'analysis_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to analyze template: {str(e)}"
            }
    
    def generate_nginx_config(self, project_name: str, env_vars: Dict = None) -> Dict:
        """Generate nginx config using environment variables."""
        try:
            # Use provided env vars or fall back to OS environment
            if env_vars is None:
                env_vars = dict(os.environ)
            
            # Get template path
            template_path = self._get_template_path(self.config["default_template"])
            
            if not template_path.exists():
                return {
                    "success": False,
                    "error": f"Template file not found: {template_path}"
                }
            
            # Analyze template to see what variables it needs
            template_analysis = self.analyze_template_variables(template_path)
            if not template_analysis['success']:
                return template_analysis
            
            required_vars = template_analysis['all_variables']
            
            # Prepare variables for template substitution
            variables = self._prepare_template_variables(project_name, env_vars, required_vars)
            
            # Check for missing variables
            missing_vars = [var for var in required_vars if var not in variables or variables[var] is None]
            if missing_vars:
                return {
                    "success": False,
                    "error": f"Missing required environment variables: {missing_vars}",
                    "required_variables": required_vars,
                    "missing_variables": missing_vars
                }
            
            # Generate config content
            config_content = self._render_template(template_path, variables)
            
            # Write configuration file
            output_path = self._get_output_path(project_name)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w') as f:
                f.write(config_content)
            
            return {
                "success": True,
                "project_name": project_name,
                "config_file": str(output_path),
                "template_used": self.config["default_template"],
                "variables_used": variables,
                "required_variables": required_vars,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to generate nginx config: {str(e)}"
            }
    
    def _get_template_path(self, template_file: str) -> Path:
        """Get full path to template file."""
        templates_dir = Path(__file__).parent / self.config["templates_dir"]
        return templates_dir / template_file
    
    def _get_output_path(self, project_name: str) -> Path:
        """Get output path for nginx config file."""
        output_dir = Path(__file__).parent / self.config["output_dir"]
        return output_dir / f"{project_name}.conf"
    
    def _prepare_template_variables(self, project_name: str, env_vars: Dict, required_vars: List[str]) -> Dict:
        """Prepare variables for template substitution."""
        variables = {
            "PROJECT_NAME": project_name,
            "DOMAIN": f"{project_name}.{self.config['domain_suffix']}",
            "TIMESTAMP": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Add environment variables
        for var in required_vars:
            if var in env_vars:
                variables[var] = env_vars[var]
            else:
                # Set to None to indicate missing
                variables[var] = None
        
        return variables
    
    def _render_template(self, template_path: Path, variables: Dict) -> str:
        """Render template with variables."""
        with open(template_path, 'r') as f:
            template_content = f.read()
        
        # Simple variable substitution
        for key, value in variables.items():
            if value is not None:
                template_content = template_content.replace(f"${{{key}}}", str(value))
                template_content = template_content.replace(f"${key}", str(value))
        
        return template_content
    
    def reload_nginx(self) -> Dict:
        """Reload nginx configuration."""
        try:
            import subprocess
            
            reload_command = self.config["nginx_reload_command"]
            result = subprocess.run(
                reload_command.split(),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "message": "Nginx reloaded successfully",
                    "output": result.stdout
                }
            else:
                return {
                    "success": False,
                    "error": f"Nginx reload failed: {result.stderr}",
                    "return_code": result.returncode
                }
                
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Nginx reload timed out"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to reload nginx: {str(e)}"
            }
    
    def remove_project_config(self, project_name: str) -> Dict:
        """Remove project nginx configuration."""
        try:
            config_path = self._get_output_path(project_name)
            
            if config_path.exists():
                config_path.unlink()
                return {
                    "success": True,
                    "message": f"Removed nginx config for {project_name}",
                    "config_file": str(config_path)
                }
            else:
                return {
                    "success": True,
                    "message": f"No nginx config found for {project_name}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to remove nginx config: {str(e)}"
            }

def main():
    """Main entry point for the nginx generator."""
    parser = argparse.ArgumentParser(description="Generate nginx configurations for projects")
    parser.add_argument("action", choices=["generate", "analyze", "reload", "remove"], help="Action to perform")
    parser.add_argument("--project-name", help="Project name")
    parser.add_argument("--template", help="Template file to analyze (for analyze action)")
    
    args = parser.parse_args()
    
    generator = NginxConfigGenerator()
    
    if args.action == "generate":
        if not args.project_name:
            print(json.dumps({
                "success": False,
                "error": "generate action requires --project-name"
            }))
            sys.exit(1)
        
        result = generator.generate_nginx_config(args.project_name)
        print(json.dumps(result, indent=2))
    
    elif args.action == "analyze":
        template_file = args.template or generator.config["default_template"]
        
        # Handle absolute paths or relative paths outside templates directory
        if args.template and (os.path.isabs(args.template) or '..' in args.template):
            template_path = Path(args.template)
        else:
            template_path = generator._get_template_path(template_file)
        
        result = generator.analyze_template_variables(template_path)
        print(json.dumps(result, indent=2))
    
    elif args.action == "reload":
        result = generator.reload_nginx()
        print(json.dumps(result, indent=2))
    
    elif args.action == "remove":
        if not args.project_name:
            print(json.dumps({
                "success": False,
                "error": "remove action requires --project-name"
            }))
            sys.exit(1)
        
        result = generator.remove_project_config(args.project_name)
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()