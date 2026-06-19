#!/usr/bin/env python3
"""
Python Client Example for FuzeInfra Service Discovery
Demonstrates how to integrate Consul service discovery in Python applications (FastAPI/Flask)
"""

import os
import sys
import consul
import atexit
import signal
import requests
from typing import Dict, List, Optional, Union
from datetime import datetime


class FuzeInfraServiceDiscovery:
    """Client class for FuzeInfra service discovery using Consul."""
    
    def __init__(self, host: str = "localhost", port: int = 8500, domain_suffix: str = "dev.local"):
        """Initialize the service discovery client."""
        self.consul = consul.Consul(host=host, port=port)
        self.domain_suffix = domain_suffix
        self._registered_services = []
    
    def register_service(self, 
                        service_name: str, 
                        port: int, 
                        address: str = None,
                        health_check_path: str = None,
                        health_check_interval: str = "10s",
                        health_check_timeout: str = "5s",
                        tags: List[str] = None,
                        meta: Dict[str, str] = None) -> Dict:
        """Register this service with Consul."""
        try:
            # Use service name as address if not provided (Docker container name)
            if address is None:
                address = service_name
            
            service_id = f"{service_name}-{port}"
            
            # Prepare service configuration
            service_config = {
                "name": service_name,
                "service_id": service_id,
                "address": address,
                "port": port,
                "tags": tags or [],
                "meta": {
                    "registered_at": datetime.now().isoformat(),
                    "registered_by": "fuzeinfra-python-client",
                    "domain": f"{service_name}.{self.domain_suffix}",
                    **(meta or {})
                }
            }
            
            # Add health check if provided
            if health_check_path:
                service_config["check"] = consul.Check.http(
                    f"http://{address}:{port}{health_check_path}",
                    interval=health_check_interval,
                    timeout=health_check_timeout
                )
            
            # Register with Consul
            self.consul.agent.service.register(**service_config)
            
            # Track for cleanup
            self._registered_services.append(service_id)
            
            print(f"✅ Service registered: {service_name} at {address}:{port}")
            
            return {
                "success": True,
                "service_id": service_id,
                "service_name": service_name,
                "address": address,
                "port": port,
                "domain": f"{service_name}.{self.domain_suffix}"
            }
            
        except Exception as e:
            print(f"❌ Failed to register service {service_name}: {e}")
            return {"success": False, "error": str(e)}
    
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
            
            # Format service instances
            instances = []
            for service_data in services:
                service = service_data["Service"]
                checks = service_data["Checks"]
                
                # Determine health status
                health_status = "passing" if all(check["Status"] == "passing" for check in checks) else "failing"
                
                instance = {
                    "service_id": service["ID"],
                    "service_name": service["Service"],
                    "address": service["Address"],
                    "port": service["Port"],
                    "url": f"http://{service['Address']}:{service['Port']}",
                    "domain": f"{service['Service']}.{self.domain_suffix}",
                    "domain_url": f"http://{service['Service']}.{self.domain_suffix}",
                    "tags": service.get("Tags", []),
                    "meta": service.get("Meta", {}),
                    "health_status": health_status,
                    "health_checks": len(checks)
                }
                instances.append(instance)
            
            return {
                "success": True,
                "service_name": service_name,
                "instances": instances,
                "total_instances": len(instances),
                "primary_instance": instances[0] if instances else None
            }
            
        except Exception as e:
            print(f"❌ Failed to discover service {service_name}: {e}")
            return {"success": False, "error": str(e)}
    
    def get_service_url(self, service_name: str, use_docker: bool = True) -> str:
        """Get service URL (convenience method)."""
        discovery = self.discover_service(service_name)
        
        if not discovery["success"] or not discovery["primary_instance"]:
            raise Exception(f"Service {service_name} not found or unhealthy")
        
        instance = discovery["primary_instance"]
        
        # Return Docker network URL (for container-to-container communication)
        # or domain URL (for local development)
        return instance["url"] if use_docker else instance["domain_url"]
    
    def deregister_service(self, service_name: str, port: int) -> Dict:
        """Deregister a service from Consul."""
        try:
            service_id = f"{service_name}-{port}"
            self.consul.agent.service.deregister(service_id)
            
            # Remove from tracking
            if service_id in self._registered_services:
                self._registered_services.remove(service_id)
            
            print(f"✅ Service deregistered: {service_id}")
            return {"success": True, "service_id": service_id}
            
        except Exception as e:
            print(f"❌ Failed to deregister service: {e}")
            return {"success": False, "error": str(e)}
    
    def setup_graceful_shutdown(self, service_name: str, port: int):
        """Setup automatic service deregistration on process exit."""
        def cleanup():
            print("\n🔄 Shutting down gracefully...")
            self.deregister_service(service_name, port)
        
        # Register cleanup for normal exit
        atexit.register(cleanup)
        
        # Register cleanup for signals
        signal.signal(signal.SIGINT, lambda s, f: cleanup())
        signal.signal(signal.SIGTERM, lambda s, f: cleanup())
    
    def cleanup_all_services(self):
        """Cleanup all registered services."""
        for service_id in self._registered_services[:]:  # Copy list to avoid modification during iteration
            service_name, port = service_id.rsplit('-', 1)
            self.deregister_service(service_name, int(port))


# FastAPI Integration Example
def create_fastapi_service_discovery():
    """Example of integrating with FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
    except ImportError:
        print("FastAPI not installed. Install with: pip install fastapi uvicorn")
        return None
    
    app = FastAPI(title="Example Service with Service Discovery")
    service_discovery = FuzeInfraServiceDiscovery()
    
    @app.on_event("startup")
    async def startup_event():
        """Register service on startup."""
        registration = service_discovery.register_service(
            service_name="example-python-service",
            port=8000,
            health_check_path="/health",
            tags=["api", "python", "fastapi"],
            meta={"version": "1.0.0", "framework": "fastapi"}
        )
        
        if registration["success"]:
            print("Service registration successful:", registration)
            service_discovery.setup_graceful_shutdown("example-python-service", 8000)
        else:
            print("Service registration failed:", registration)
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    
    @app.get("/discover/{service_name}")
    async def discover_service_endpoint(service_name: str):
        """Endpoint to discover other services."""
        discovery = service_discovery.discover_service(service_name)
        
        if discovery["success"]:
            return discovery
        else:
            raise HTTPException(status_code=404, detail=discovery["error"])
    
    @app.get("/call-fuzeagent")
    async def call_fuzeagent():
        """Example of calling another service."""
        try:
            fuzeagent_url = service_discovery.get_service_url("fuzeagent")
            
            # Make API call to fuzeagent
            response = requests.get(f"{fuzeagent_url}/api/status", timeout=5)
            return {
                "success": True,
                "fuzeagent_url": fuzeagent_url,
                "fuzeagent_response": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
            }
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"fuzeagent not available: {str(e)}")
    
    return app, service_discovery


# Flask Integration Example
def create_flask_service_discovery():
    """Example of integrating with Flask application."""
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        print("Flask not installed. Install with: pip install flask")
        return None
    
    app = Flask(__name__)
    service_discovery = FuzeInfraServiceDiscovery()
    
    @app.route('/health')
    def health_check():
        """Health check endpoint."""
        return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})
    
    @app.route('/discover/<service_name>')
    def discover_service_endpoint(service_name):
        """Endpoint to discover other services."""
        discovery = service_discovery.discover_service(service_name)
        
        if discovery["success"]:
            return jsonify(discovery)
        else:
            return jsonify(discovery), 404
    
    @app.route('/call-fuzeagent')
    def call_fuzeagent():
        """Example of calling another service."""
        try:
            fuzeagent_url = service_discovery.get_service_url("fuzeagent")
            
            # Make API call to fuzeagent
            response = requests.get(f"{fuzeagent_url}/api/status", timeout=5)
            return jsonify({
                "success": True,
                "fuzeagent_url": fuzeagent_url,
                "fuzeagent_response": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"fuzeagent not available: {str(e)}"}), 503
    
    # Register service
    registration = service_discovery.register_service(
        service_name="example-flask-service",
        port=5000,
        health_check_path="/health",
        tags=["api", "python", "flask"],
        meta={"version": "1.0.0", "framework": "flask"}
    )
    
    if registration["success"]:
        print("Service registration successful:", registration)
        service_discovery.setup_graceful_shutdown("example-flask-service", 5000)
    
    return app, service_discovery


def example_usage():
    """Example usage of the service discovery client."""
    service_discovery = FuzeInfraServiceDiscovery()
    
    # Example 1: Register this service
    service_name = "example-python-service"
    port = 8000
    
    registration = service_discovery.register_service(
        service_name=service_name,
        port=port,
        health_check_path="/health",
        tags=["api", "example"],
        meta={"version": "1.0.0"}
    )
    
    if registration["success"]:
        print("Service registration successful:", registration)
        service_discovery.setup_graceful_shutdown(service_name, port)
    
    # Example 2: Discover another service
    try:
        fuzeagent_url = service_discovery.get_service_url("fuzeagent")
        print(f"🔍 fuzeagent URL: {fuzeagent_url}")
        
        # Use the service URL for API calls
        # response = requests.get(f"{fuzeagent_url}/api/status")
        # print("fuzeagent response:", response.json())
        
    except Exception as e:
        print(f"⚠️  fuzeagent not available: {e}")
    
    # Example 3: Discover with detailed information
    discovery = service_discovery.discover_service("fuzeagent")
    if discovery["success"]:
        print("🔍 fuzeagent discovery result:", discovery)
        
        # Access specific instance information
        instance = discovery["primary_instance"]
        print(f"Primary instance: {instance['address']}:{instance['port']}")
        print(f"Health status: {instance['health_status']}")
        print(f"Tags: {', '.join(instance['tags'])}")
    else:
        print(f"❌ Discovery failed: {discovery['error']}")


if __name__ == "__main__":
    example_usage()