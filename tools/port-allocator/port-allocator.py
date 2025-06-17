#!/usr/bin/env python3
"""
Port Allocation Service for Local Development Orchestrator
Automatically discovers and allocates available ports for projects.
"""

import os
import sys
import json
import yaml
import socket
import argparse
import re
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from datetime import datetime

class PortAllocator:
    """Manages port allocation for development projects."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the port allocator with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "port_ranges": [
                {"start": 3000, "end": 3999, "description": "Frontend applications"},
                {"start": 5000, "end": 5999, "description": "Backend APIs"}, 
                {"start": 6000, "end": 6999, "description": "Database services"},
                {"start": 7000, "end": 7999, "description": "Cache services"}
            ],
            "consecutive_block_size": 5,
            "excluded_ports": [5432, 6379, 3306, 27017, 3000, 8080, 9090, 9093],
            "port_variable_patterns": [
                r"\$\{([A-Z_]*PORT[A-Z_]*)\}",  # ${FRONTEND_PORT}, ${PORT_BACKEND}
                r"\$\{([A-Z_]+_PORT)\}",        # ${SERVICE_PORT}
                r"\$\{(PORT_[A-Z_]+)\}",        # ${PORT_SERVICE}
            ]
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
    
    def analyze_docker_compose(self, compose_file: Path, verbose: bool = False) -> Dict:
        """Analyze docker-compose.yml to discover required ports."""
        try:
            if verbose:
                print(f"📋 Analyzing docker-compose file: {compose_file}")
            
            with open(compose_file, 'r') as f:
                compose_data = yaml.safe_load(f)
            
            if verbose:
                print(f"📄 Successfully loaded YAML file")
            
            port_vars = set()
            service_info = {}
            warnings = []
            
            services = compose_data.get('services', {})
            if verbose:
                print(f"🐳 Found {len(services)} services: {', '.join(services.keys())}")
            
            for service_name, service_config in services.items():
                if verbose:
                    print(f"\n🔍 Analyzing service: {service_name}")
                
                service_ports = []
                service_urls = []
                
                # Analyze ports section
                ports = service_config.get('ports', [])
                if verbose and ports:
                    print(f"  📊 Found {len(ports)} port mappings")
                for port_mapping in ports:
                    if isinstance(port_mapping, str):
                        # Check for port ranges (skip for now)
                        if '-' in port_mapping:
                            warnings.append(f"Port range detected in {service_name}: {port_mapping} - skipping range allocation")
                            continue
                        
                        # Extract port variables
                        external_port = port_mapping.split(':')[0]
                        port_vars_found = self._extract_port_variables(external_port)
                        if verbose and port_vars_found:
                            print(f"    ✅ Found port variables: {', '.join(port_vars_found)}")
                        port_vars.update(port_vars_found)
                        service_ports.extend(port_vars_found)
                        
                        # Check for hardcoded ports that should be variables
                        if external_port.isdigit():
                            suggested_var = f"{service_name.upper()}_PORT"
                            warnings.append(f"Hardcoded port {external_port} in {service_name} - suggest using ${{{suggested_var}}}")
                
                # Analyze environment variables for embedded ports
                env_vars = service_config.get('environment', [])
                if isinstance(env_vars, list):
                    for env_var in env_vars:
                        if isinstance(env_var, str) and '=' in env_var:
                            key, value = env_var.split('=', 1)
                            embedded_ports = self._find_embedded_ports(value)
                            for url, suggested_var in embedded_ports:
                                service_urls.append({'original': url, 'suggested_var': suggested_var})
                                warnings.append(f"Embedded port in {service_name}.{key}: '{url}' - suggest using ${{{suggested_var}}}")
                
                elif isinstance(env_vars, dict):
                    for key, value in env_vars.items():
                        if isinstance(value, str):
                            embedded_ports = self._find_embedded_ports(value)
                            for url, suggested_var in embedded_ports:
                                service_urls.append({'original': url, 'suggested_var': suggested_var})
                                warnings.append(f"Embedded port in {service_name}.{key}: '{value}' - suggest using ${{{suggested_var}}}")
                
                service_info[service_name] = {
                    'port_variables': service_ports,
                    'embedded_urls': service_urls
                }
            
            # Categorize port variables by type
            categorized_ports = self._categorize_port_variables(list(port_vars))
            
            if verbose:
                print(f"\n📊 SUMMARY:")
                print(f"  Total port variables found: {len(port_vars)}")
                if port_vars:
                    print(f"  Port variables: {', '.join(sorted(port_vars))}")
                print(f"  Services with port requirements: {len([s for s in service_info.values() if s['port_variables']])}")
                if warnings:
                    print(f"  Warnings: {len(warnings)}")
            
            return {
                'success': True,
                'compose_file': str(compose_file),
                'total_ports_needed': len(port_vars),
                'port_variables': sorted(list(port_vars)),
                'categorized_ports': categorized_ports,
                'service_info': service_info,
                'warnings': warnings,
                'analysis_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to analyze docker-compose file: {str(e)}"
            }
    
    def _extract_port_variables(self, text: str) -> List[str]:
        """Extract port variables from text using configured patterns."""
        port_vars = []
        for pattern in self.config['port_variable_patterns']:
            matches = re.findall(pattern, text)
            port_vars.extend(matches)
        return port_vars
    
    def _find_embedded_ports(self, text: str) -> List[Tuple[str, str]]:
        """Find hardcoded ports in URLs and suggest variable names."""
        embedded_ports = []
        
        # Look for URLs with hardcoded ports
        url_patterns = [
            r'(https?://[^:]+:(\d+))',      # http://host:3000
            r'(redis://[^:]+:(\d+))',       # redis://host:6379
            r'(postgresql://[^:]+:(\d+))',  # postgresql://host:5432
            r'(mongodb://[^:]+:(\d+))',     # mongodb://host:27017
        ]
        
        for pattern in url_patterns:
            matches = re.findall(pattern, text)
            for full_url, port in matches:
                # Suggest variable name based on URL protocol/context
                if 'redis' in full_url.lower():
                    suggested_var = 'REDIS_PORT'
                elif 'postgresql' in full_url.lower() or 'postgres' in full_url.lower():
                    suggested_var = 'DATABASE_PORT'
                elif 'mongodb' in full_url.lower() or 'mongo' in full_url.lower():
                    suggested_var = 'MONGODB_PORT'
                elif 'localhost' in full_url and port in ['3000', '3001', '3002']:
                    suggested_var = 'FRONTEND_PORT'
                elif 'localhost' in full_url and port in ['5000', '5001', '5002']:
                    suggested_var = 'BACKEND_PORT'
                else:
                    suggested_var = f'SERVICE_PORT_{port}'
                
                embedded_ports.append((full_url, suggested_var))
        
        return embedded_ports
    
    def _categorize_port_variables(self, port_vars: List[str]) -> Dict:
        """Categorize port variables by their likely service type."""
        categories = {
            'frontend': [],
            'backend': [],
            'database': [],
            'cache': [],
            'monitoring': [],
            'other': []
        }
        
        for var in port_vars:
            var_lower = var.lower()
            if any(keyword in var_lower for keyword in ['frontend', 'web', 'ui', 'client', 'react', 'vue', 'angular']):
                categories['frontend'].append(var)
            elif any(keyword in var_lower for keyword in ['backend', 'api', 'server', 'app']):
                categories['backend'].append(var)
            elif any(keyword in var_lower for keyword in ['db', 'database', 'postgres', 'mysql', 'mongo']):
                categories['database'].append(var)
            elif any(keyword in var_lower for keyword in ['redis', 'cache', 'memcache']):
                categories['cache'].append(var)
            elif any(keyword in var_lower for keyword in ['prometheus', 'grafana', 'monitor']):
                categories['monitoring'].append(var)
            else:
                categories['other'].append(var)
        
        return categories
    
    def allocate_ports(self, project_name: str, port_vars: List[str] = None, num_ports: int = None, start_port: int = None) -> Dict:
        """Allocate ports for a project."""
        try:
            if port_vars:
                # Use provided port variables (from compose file analysis)
                num_ports = len(port_vars)
            elif num_ports is None:
                num_ports = 5  # Default fallback
            
            # Find available ports
            available_ports = self._find_available_ports(num_ports, start_port)
            
            if len(available_ports) < num_ports:
                return {
                    "success": False,
                    "error": f"Could not find {num_ports} available ports. Found only {len(available_ports)}"
                }
            
            # Create port mapping
            port_mapping = {}
            if port_vars:
                # Map specific variables to ports
                for i, var in enumerate(port_vars):
                    port_mapping[var] = available_ports[i]
            else:
                # Create generic mapping
                service_types = ['frontend', 'backend', 'database', 'cache', 'monitoring']
                for i in range(num_ports):
                    service_type = service_types[i] if i < len(service_types) else f'service_{i+1}'
                    port_mapping[service_type] = available_ports[i]
            
            return {
                "success": True,
                "project_name": project_name,
                "port_mapping": port_mapping,
                "allocated_ports": available_ports[:num_ports],
                "allocation_timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to allocate ports: {str(e)}"
            }
    
    def _find_available_ports(self, num_ports: int, start_port: int = None) -> List[int]:
        """Find available ports within configured ranges."""
        available_ports = []
        excluded_ports = set(self.config["excluded_ports"])
        
        # Determine the search ranges
        if start_port is not None:
            # Use custom start port, search within configured ranges but starting from start_port
            search_ranges = []
            for port_range in self.config["port_ranges"]:
                range_start = max(start_port, port_range["start"])
                range_end = port_range["end"]
                if range_start <= range_end:
                    search_ranges.append((range_start, range_end))
        else:
            # Use configured ranges as-is
            search_ranges = [(r["start"], r["end"]) for r in self.config["port_ranges"]]
        
        for start, end in search_ranges:
            for port in range(start, end + 1):
                if port in excluded_ports:
                    continue
                
                if self._is_port_available(port):
                    available_ports.append(port)
                    
                if len(available_ports) >= num_ports:
                    return available_ports
        
        return available_ports
    
    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                result = sock.bind(('127.0.0.1', port))
                return True
        except OSError:
            return False
    
    def scan_ports(self, start_port: int = 3000, end_port: int = 9999) -> Dict:
        """Scan for available ports in a range."""
        available_ports = []
        
        for port in range(start_port, end_port + 1):
            if self._is_port_available(port):
                available_ports.append(port)
        
        return {
            "success": True,
            "range": f"{start_port}-{end_port}",
            "available_ports": available_ports,
            "total_available": len(available_ports),
            "scan_timestamp": datetime.now().isoformat()
        }

def main():
    """Main entry point for the port allocator."""
    parser = argparse.ArgumentParser(description="Port allocation service for local development")
    parser.add_argument("action", choices=["analyze", "allocate", "scan"], help="Action to perform")
    parser.add_argument("project_name", nargs="?", help="Project name")
    parser.add_argument("--compose-file", type=str, help="Path to docker-compose.yml file")
    parser.add_argument("--num-ports", type=int, help="Number of ports to allocate")
    parser.add_argument("--start-port", type=int, default=3000, help="Start of port range to scan")
    parser.add_argument("--end-port", type=int, help="End of port range to scan")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    allocator = PortAllocator()
    
    if args.action == "analyze":
        if not args.compose_file:
            print(json.dumps({
                "success": False,
                "error": "analyze action requires --compose-file"
            }))
            sys.exit(1)
        
        compose_file = Path(args.compose_file)
        if not compose_file.exists():
            print(json.dumps({
                "success": False,
                "error": f"Docker compose file not found: {compose_file}"
            }))
            sys.exit(1)
        
        result = allocator.analyze_docker_compose(compose_file, verbose=args.verbose)
        if not args.verbose:
            print(json.dumps(result, indent=2))
        else:
            print("\n🎯 ANALYSIS COMPLETE")
            print("=" * 50)
            print(json.dumps(result, indent=2))
    
    elif args.action == "allocate":
        if not args.project_name:
            print(json.dumps({
                "success": False,
                "error": "allocate action requires project_name"
            }))
            sys.exit(1)
        
        # Validate parameter combinations
        if args.compose_file and args.num_ports:
            print(json.dumps({
                "success": False,
                "error": "Cannot specify both --compose-file and --num-ports. When using compose file, port count is determined automatically."
            }))
            sys.exit(1)
        
        if args.num_ports and args.end_port:
            print(json.dumps({
                "success": False,
                "error": "Cannot specify both --num-ports and --end-port. Use --num-ports for allocation or --end-port for scanning."
            }))
            sys.exit(1)
        
        # If compose file provided, analyze it first
        port_vars = None
        if args.compose_file:
            compose_file = Path(args.compose_file)
            if compose_file.exists():
                analysis = allocator.analyze_docker_compose(compose_file)
                if analysis.get('success'):
                    port_vars = analysis['port_variables']
                    if args.verbose:
                        print(f"🔍 Discovered {len(port_vars)} port variables from compose file")
                        print(f"📍 Starting allocation from port {args.start_port if args.start_port else 'default range'}")
            else:
                print(json.dumps({
                    "success": False,
                    "error": f"Docker compose file not found: {compose_file}"
                }))
                sys.exit(1)
        
        result = allocator.allocate_ports(args.project_name, port_vars, args.num_ports, args.start_port)
        print(json.dumps(result, indent=2))
    
    elif args.action == "scan":
        end_port = args.end_port if args.end_port else 9999
        result = allocator.scan_ports(args.start_port, end_port)
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()