#!/usr/bin/env python3
"""
Consul Helper - Wrapper class for Consul operations in FuzeInfra
Provides simplified interface for service registration, discovery, and health checks.
"""

import os
import sys
import json
import yaml
import consul
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
from datetime import datetime


class ConsulHelper:
    """Helper class for Consul service discovery operations."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize Consul helper with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        self.logger = self._setup_logging()
        self.consul = self._connect_consul()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "consul": {
                "host": "localhost",
                "port": 8500,
                "scheme": "http",
                "timeout": 10,
                "consistency": "default"
            },
            "service_discovery": {
                "default_health_check_interval": "10s",
                "default_health_check_timeout": "5s",
                "default_health_check_path": "/health",
                "service_ttl": 30,
                "cleanup_interval": 60
            },
            "dns": {
                "domain_suffix": "dev.local"
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
    
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration."""
        logger = logging.getLogger("consul-helper")
        logger.setLevel(getattr(logging, self.config["logging"]["level"]))
        
        # Create handler if not exists
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(self.config["logging"]["format"])
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger
    
    def _connect_consul(self) -> consul.Consul:
        """Establish connection to Consul."""
        try:
            consul_config = self.config["consul"]
            return consul.Consul(
                host=consul_config["host"],
                port=consul_config["port"],
                scheme=consul_config["scheme"],
                timeout=consul_config["timeout"],
                consistency=consul_config["consistency"]
            )
        except Exception as e:
            self.logger.error(f"Failed to connect to Consul: {e}")
            raise
    
    def register_service(self, 
                        name: str, 
                        port: int, 
                        address: str = None,
                        health_check_path: str = None,
                        health_check_interval: str = None,
                        health_check_timeout: str = None,
                        tags: List[str] = None,
                        meta: Dict[str, str] = None) -> Dict:
        """Register a service with Consul."""
        try:
            # Use service name as address if not provided (Docker container name)
            if address is None:
                address = name
            
            # Prepare health check
            health_check = None
            if health_check_path:
                health_check_interval = health_check_interval or self.config["service_discovery"]["default_health_check_interval"]
                health_check_timeout = health_check_timeout or self.config["service_discovery"]["default_health_check_timeout"]
                
                health_check = consul.Check.http(
                    f"http://{address}:{port}{health_check_path}",
                    interval=health_check_interval,
                    timeout=health_check_timeout
                )
            
            # Prepare service registration
            service_id = f"{name}-{port}"
            service_data = {
                "name": name,
                "service_id": service_id,
                "address": address,
                "port": port,
                "tags": tags or [],
                "meta": meta or {},
                "check": health_check
            }
            
            # Add metadata
            service_data["meta"].update({
                "registered_at": datetime.now().isoformat(),
                "registered_by": "fuzeinfra-service-discovery",
                "domain": f"{name}.{self.config['dns']['domain_suffix']}"
            })
            
            # Register service
            self.consul.agent.service.register(**service_data)
            
            self.logger.info(f"Successfully registered service: {name} at {address}:{port}")
            
            return {
                "success": True,
                "service_id": service_id,
                "service_name": name,
                "address": address,
                "port": port,
                "domain": f"{name}.{self.config['dns']['domain_suffix']}",
                "health_check": health_check_path is not None,
                "tags": tags or [],
                "message": f"Service {name} registered successfully"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to register service {name}: {e}")
            return {
                "success": False,
                "error": f"Failed to register service: {str(e)}"
            }
    
    def deregister_service(self, service_name: str, port: int = None) -> Dict:
        """Deregister a service from Consul."""
        try:
            if port:
                service_id = f"{service_name}-{port}"
            else:
                # Find service by name and deregister all instances
                services = self.list_services()
                matching_services = [s for s in services if s.get("service_name") == service_name]
                
                if not matching_services:
                    return {
                        "success": True,
                        "message": f"No services found with name {service_name}"
                    }
                
                # Deregister all matching services
                deregistered = []
                for service in matching_services:
                    self.consul.agent.service.deregister(service["service_id"])
                    deregistered.append(service["service_id"])
                
                return {
                    "success": True,
                    "deregistered_services": deregistered,
                    "message": f"Deregistered {len(deregistered)} services for {service_name}"
                }
            
            # Deregister specific service
            self.consul.agent.service.deregister(service_id)
            self.logger.info(f"Successfully deregistered service: {service_id}")
            
            return {
                "success": True,
                "service_id": service_id,
                "message": f"Service {service_id} deregistered successfully"
            }
            
        except Exception as e:
            self.logger.error(f"Failed to deregister service {service_name}: {e}")
            return {
                "success": False,
                "error": f"Failed to deregister service: {str(e)}"
            }
    
    def discover_service(self, service_name: str, healthy_only: bool = True) -> Dict:
        """Discover a service by name."""
        try:
            # Get service health information
            _, services = self.consul.health.service(service_name, passing=healthy_only)
            
            if not services:
                return {
                    "success": False,
                    "error": f"No {'healthy ' if healthy_only else ''}services found for {service_name}"
                }
            
            # Format service information
            service_instances = []
            for service_data in services:
                service = service_data["Service"]
                checks = service_data["Checks"]
                
                # Determine health status
                health_status = "passing"
                for check in checks:
                    if check["Status"] != "passing":
                        health_status = "failing"
                        break
                
                instance = {
                    "service_id": service["ID"],
                    "service_name": service["Service"],
                    "address": service["Address"],
                    "port": service["Port"],
                    "url": f"http://{service['Address']}:{service['Port']}",
                    "domain": f"{service['Service']}.{self.config['dns']['domain_suffix']}",
                    "domain_url": f"http://{service['Service']}.{self.config['dns']['domain_suffix']}",
                    "tags": service.get("Tags", []),
                    "meta": service.get("Meta", {}),
                    "health_status": health_status,
                    "health_checks": len(checks)
                }
                service_instances.append(instance)
            
            return {
                "success": True,
                "service_name": service_name,
                "instances": service_instances,
                "total_instances": len(service_instances),
                "primary_instance": service_instances[0] if service_instances else None
            }
            
        except Exception as e:
            self.logger.error(f"Failed to discover service {service_name}: {e}")
            return {
                "success": False,
                "error": f"Failed to discover service: {str(e)}"
            }
    
    def list_services(self) -> List[Dict]:
        """List all registered services."""
        try:
            services = []
            
            # Get all services
            _, service_list = self.consul.catalog.services()
            
            for service_name, tags in service_list.items():
                # Skip consul service itself
                if service_name == "consul":
                    continue
                
                # Get detailed service information
                _, service_instances = self.consul.health.service(service_name)
                
                for instance_data in service_instances:
                    service = instance_data["Service"]
                    checks = instance_data["Checks"]
                    
                    # Determine health status
                    health_status = "passing"
                    for check in checks:
                        if check["Status"] != "passing":
                            health_status = "failing"
                            break
                    
                    services.append({
                        "service_id": service["ID"],
                        "service_name": service["Service"],
                        "address": service["Address"],
                        "port": service["Port"],
                        "url": f"http://{service['Address']}:{service['Port']}",
                        "domain": f"{service['Service']}.{self.config['dns']['domain_suffix']}",
                        "domain_url": f"http://{service['Service']}.{self.config['dns']['domain_suffix']}",
                        "tags": service.get("Tags", []),
                        "meta": service.get("Meta", {}),
                        "health_status": health_status,
                        "health_checks": len(checks)
                    })
            
            return services
            
        except Exception as e:
            self.logger.error(f"Failed to list services: {e}")
            return []
    
    def get_service_health(self, service_name: str) -> Dict:
        """Get health status for a specific service."""
        try:
            # Get service health information
            _, services = self.consul.health.service(service_name)
            
            if not services:
                return {
                    "success": False,
                    "error": f"Service {service_name} not found"
                }
            
            health_summary = {
                "service_name": service_name,
                "total_instances": len(services),
                "healthy_instances": 0,
                "unhealthy_instances": 0,
                "instances": []
            }
            
            for service_data in services:
                service = service_data["Service"]
                checks = service_data["Checks"]
                
                # Analyze health checks
                all_passing = True
                check_details = []
                
                for check in checks:
                    check_details.append({
                        "check_id": check["CheckID"],
                        "name": check["Name"],
                        "status": check["Status"],
                        "output": check.get("Output", ""),
                        "notes": check.get("Notes", "")
                    })
                    
                    if check["Status"] != "passing":
                        all_passing = False
                
                if all_passing:
                    health_summary["healthy_instances"] += 1
                else:
                    health_summary["unhealthy_instances"] += 1
                
                health_summary["instances"].append({
                    "service_id": service["ID"],
                    "address": service["Address"],
                    "port": service["Port"],
                    "healthy": all_passing,
                    "checks": check_details
                })
            
            health_summary["overall_status"] = "healthy" if health_summary["unhealthy_instances"] == 0 else "unhealthy"
            
            return {
                "success": True,
                **health_summary
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get service health for {service_name}: {e}")
            return {
                "success": False,
                "error": f"Failed to get service health: {str(e)}"
            }
    
    def check_consul_status(self) -> Dict:
        """Check Consul cluster status."""
        try:
            # Get cluster leader
            leader = self.consul.status.leader()
            
            # Get cluster members
            members = self.consul.agent.members()
            
            # Get service catalog stats
            _, services = self.consul.catalog.services()
            service_count = len(services)
            
            return {
                "success": True,
                "consul_status": "healthy",
                "leader": leader,
                "cluster_members": len(members),
                "registered_services": service_count,
                "consul_ui_url": f"{self.config['consul']['scheme']}://{self.config['consul']['host']}:{self.config['consul']['port']}/ui",
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Failed to check consul status: {e}")
            return {
                "success": False,
                "consul_status": "unhealthy",
                "error": f"Failed to connect to Consul: {str(e)}"
            }


def main():
    """Simple test of the ConsulHelper class."""
    helper = ConsulHelper()
    
    # Test consul status
    status = helper.check_consul_status()
    print("Consul Status:")
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()