#!/usr/bin/env python3
"""
Test script to demonstrate nginx configuration generation with allocated ports.
"""

import os
import sys
sys.path.append('../nginx-generator')

# Import the nginx generator directly
import importlib.util
spec = importlib.util.spec_from_file_location("nginx_generator", "../nginx-generator/nginx-generator.py")
nginx_gen_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nginx_gen_module)
NginxConfigGenerator = nginx_gen_module.NginxConfigGenerator
from pathlib import Path

def load_env_file(env_file_path):
    """Load environment variables from a file."""
    env_vars = {}
    with open(env_file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value
    return env_vars

def generate_config_with_ports():
    """Generate nginx config using allocated port values."""
    
    # Load environment variables from test.env
    env_file = Path(__file__).parent / "test.env"
    env_vars = load_env_file(env_file)
    
    print("🔧 LOADED ENVIRONMENT VARIABLES:")
    print("=" * 50)
    for key, value in sorted(env_vars.items()):
        print(f"  {key}={value}")
    
    print("\n🔍 ANALYZING TEMPLATE FILE:")
    print("=" * 50)
    
    # Initialize nginx generator
    generator = NginxConfigGenerator()
    
    # Analyze the template to see what variables it needs
    template_path = Path(__file__).parent / "sample-nginx.conf"
    analysis = generator.analyze_template_variables(template_path)
    
    if analysis['success']:
        print(f"  Template: {analysis['template_file']}")
        print(f"  Total variables needed: {analysis['total_variables']}")
        print(f"  Port variables: {', '.join(analysis['port_variables'])}")
        
        # Check if we have all required variables
        missing_vars = [var for var in analysis['port_variables'] if var not in env_vars]
        if missing_vars:
            print(f"  ❌ Missing variables: {', '.join(missing_vars)}")
        else:
            print(f"  ✅ All required variables available!")
    
    print("\n🎯 GENERATING NGINX CONFIGURATION:")
    print("=" * 50)
    
    # Read template content
    with open(template_path, 'r') as f:
        template_content = f.read()
    
    # Substitute variables
    final_config = template_content
    for key, value in env_vars.items():
        final_config = final_config.replace(f"${{{key}}}", value)
        final_config = final_config.replace(f"${key}", value)
    
    # Write output file
    output_file = Path(__file__).parent / "generated-nginx.conf"
    with open(output_file, 'w') as f:
        f.write(final_config)
    
    print(f"✅ Generated nginx config: {output_file}")
    print("\n📄 GENERATED CONFIGURATION:")
    print("=" * 50)
    print(final_config)
    
    print("\n🌐 NGINX RUNTIME BEHAVIOR:")
    print("=" * 50)
    print("In a real nginx deployment, this configuration would be loaded and nginx")
    print("would proxy requests to the actual port numbers that were allocated.")
    print("Environment variable substitution happens at nginx startup time.")
    print()
    print("Example requests:")
    print("  http://test-project.dev.local/        → http://localhost:3005 (Grafana)")
    print("  http://test-project.dev.local/api/    → http://localhost:3002 (Airflow)")
    print("  http://test-project.dev.local/mongo/  → http://localhost:3010 (Mongo Express)")
    print("  http://test-project.dev.local/prometheus/ → http://localhost:3013 (Prometheus)")

if __name__ == "__main__":
    generate_config_with_ports() 