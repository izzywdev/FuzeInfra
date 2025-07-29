#!/usr/bin/env python3
"""
DNS Management Service for Local Development Orchestrator
Handles local DNS routing for *.dev.local domains via dnsmasq and hosts file fallback.
"""

import os
import sys
import json
import yaml
import argparse
import platform
import subprocess
import shutil
import requests
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

class DNSManager:
    """Manages local DNS entries for development domains."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the DNS manager with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        self.platform = platform.system().lower()
        self.dnsmasq_available = self._check_dnsmasq_availability()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "domain_suffix": "dev.local",
            "default_ip": "127.0.0.1",
            "backup_enabled": True,
            "hosts_file_paths": {
                "windows": "C:\\Windows\\System32\\drivers\\etc\\hosts",
                "linux": "/etc/hosts",
                "darwin": "/etc/hosts"
            },
            "comment_prefix": "# Local Dev Orchestrator:",
            "dnsmasq": {
                "web_ui_url": "http://localhost:8053",
                "container_name": "fuzeinfra-dnsmasq",
                "api_enabled": True
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
    
    def add_dns_entry(self, project_name: str, domain_suffix: str = None, ip: str = None) -> Dict:
        """Add DNS entry for project via dnsmasq or hosts file fallback."""
        domain_suffix = domain_suffix or self.config["domain_suffix"]
        ip = ip or self.config["default_ip"]
        domain = f"{project_name}.{domain_suffix}"
        
        # Try dnsmasq first if available
        if self.dnsmasq_available:
            result = self._add_dnsmasq_entry(domain, ip, project_name)
            if result["success"]:
                return result
            # If dnsmasq fails, fall back to hosts file
            print(f"Warning: dnsmasq failed ({result['error']}), falling back to hosts file")
        
        # Hosts file fallback
        return self._add_hosts_entry(domain, ip, project_name)
    
    def _add_dnsmasq_entry(self, domain: str, ip: str, project_name: str) -> Dict:
        """Add DNS entry via dnsmasq container."""
        try:
            # Add entry to dnsmasq configuration via Docker exec
            cmd = [
                "docker", "exec", self.config["dnsmasq"]["container_name"],
                "sh", "-c", f"echo 'address=/{domain}/{ip}' >> /etc/dnsmasq.conf"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Reload dnsmasq configuration
            reload_cmd = [
                "docker", "exec", self.config["dnsmasq"]["container_name"],
                "killall", "-HUP", "dnsmasq"
            ]
            subprocess.run(reload_cmd, capture_output=True, text=True, check=True)
            
            return {
                "success": True,
                "domain": domain,
                "ip": ip,
                "method": "dnsmasq",
                "message": f"Added DNS entry via dnsmasq: {domain} -> {ip}"
            }
            
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error": f"Failed to add dnsmasq entry: {e.stderr}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to communicate with dnsmasq: {str(e)}"
            }
    
    def _add_hosts_entry(self, domain: str, ip: str, project_name: str) -> Dict:
        """Add DNS entry to hosts file (fallback method)."""
        try:
            hosts_file = Path(self.config["hosts_file_paths"][self.platform])
            
            # Check permissions
            if not self._check_permissions(hosts_file):
                return {
                    "success": False,
                    "error": "Insufficient permissions to modify hosts file. Run as administrator/sudo."
                }
            
            # Backup if enabled
            if self.config["backup_enabled"]:
                self._backup_hosts_file(hosts_file)
            
            # Read current content
            hosts_content = self._read_hosts_file(hosts_file)
            
            # Check if entry exists
            if self._entry_exists(hosts_content, domain):
                return {
                    "success": True,
                    "method": "hosts_file",
                    "message": f"DNS entry for {domain} already exists in hosts file"
                }
            
            # Add entry
            entry_line = f"{ip}\t{domain}\t{self.config['comment_prefix']} {project_name}"
            hosts_content.append(entry_line)
            
            # Write back
            self._write_hosts_file(hosts_file, hosts_content)
            
            # Flush DNS cache
            self._flush_dns_cache()
            
            return {
                "success": True,
                "domain": domain,
                "ip": ip,
                "method": "hosts_file",
                "message": f"Added DNS entry to hosts file: {domain} -> {ip}"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to add hosts file entry: {str(e)}"
            }
    
    def remove_dns_entry(self, project_name: str, domain_suffix: str = None) -> Dict:
        """Remove DNS entry for project."""
        domain_suffix = domain_suffix or self.config["domain_suffix"]
        domain = f"{project_name}.{domain_suffix}"
        
        try:
            hosts_file = Path(self.config["hosts_file_paths"][self.platform])
            
            if not self._check_permissions(hosts_file):
                return {
                    "success": False,
                    "error": "Insufficient permissions to modify hosts file. Run as administrator/sudo."
                }
            
            if self.config["backup_enabled"]:
                self._backup_hosts_file(hosts_file)
            
            hosts_content = self._read_hosts_file(hosts_file)
            original_length = len(hosts_content)
            
            # Remove lines containing the domain and our comment prefix
            hosts_content = [line for line in hosts_content 
                           if not (domain in line and self.config["comment_prefix"] in line)]
            
            removed_count = original_length - len(hosts_content)
            
            if removed_count > 0:
                self._write_hosts_file(hosts_file, hosts_content)
                self._flush_dns_cache()
                
                return {
                    "success": True,
                    "domain": domain,
                    "message": f"Removed {removed_count} DNS entries for {domain}"
                }
            else:
                return {
                    "success": True,
                    "message": f"No DNS entries found for {domain}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to remove DNS entry: {str(e)}"
            }
    
    def _check_permissions(self, hosts_file: Path) -> bool:
        """Check if we have permissions to modify hosts file."""
        try:
            if self.platform == "windows":
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin()
            else:
                return os.access(hosts_file.parent, os.W_OK)
        except:
            return False
    
    def _read_hosts_file(self, hosts_file: Path) -> List[str]:
        """Read hosts file content."""
        with open(hosts_file, 'r', encoding='utf-8') as f:
            return [line.rstrip() for line in f.readlines()]
    
    def _write_hosts_file(self, hosts_file: Path, content: List[str]) -> None:
        """Write content to hosts file."""
        with open(hosts_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content) + '\n')
    
    def _entry_exists(self, hosts_content: List[str], domain: str) -> bool:
        """Check if domain entry already exists."""
        return any(domain in line and not line.strip().startswith('#') for line in hosts_content)
    
    def _backup_hosts_file(self, hosts_file: Path) -> None:
        """Create backup of hosts file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = hosts_file.with_suffix(f'.backup_{timestamp}')
        shutil.copy2(hosts_file, backup_path)
    
    def _flush_dns_cache(self) -> None:
        """Flush DNS cache."""
        try:
            if self.platform == "windows":
                subprocess.run(['ipconfig', '/flushdns'], capture_output=True, check=True)
            elif self.platform == "darwin":  # macOS
                subprocess.run(['sudo', 'dscacheutil', '-flushcache'], capture_output=True, check=True)
            elif self.platform == "linux":
                # Try different methods for Linux
                commands = [
                    ['sudo', 'systemctl', 'restart', 'systemd-resolved'],
                    ['sudo', 'service', 'network-manager', 'restart']
                ]
                for cmd in commands:
                    try:
                        subprocess.run(cmd, capture_output=True, check=True)
                        break
                    except:
                        continue
        except:
            pass  # Non-critical if fails
    
    def _check_dnsmasq_availability(self) -> bool:
        """Check if dnsmasq container is running and accessible."""
        try:
            # Check if container is running
            result = subprocess.run([
                "docker", "ps", "--filter", f"name={self.config['dnsmasq']['container_name']}", 
                "--format", "{{.Names}}"
            ], capture_output=True, text=True, check=True)
            
            return self.config["dnsmasq"]["container_name"] in result.stdout
        except:
            return False
    
    def get_dns_status(self) -> Dict:
        """Get current DNS management status."""
        return {
            "dnsmasq_available": self.dnsmasq_available,
            "dnsmasq_container": self.config["dnsmasq"]["container_name"],
            "web_ui_url": self.config["dnsmasq"]["web_ui_url"],
            "domain_suffix": self.config["domain_suffix"],
            "platform": self.platform
        }

def main():
    """Command line interface for the DNS manager."""
    parser = argparse.ArgumentParser(description="DNS Manager for Local Development Orchestrator")
    parser.add_argument("action", choices=["add", "remove", "status"], help="Action to perform")
    parser.add_argument("project_name", nargs="?", help="Project name for DNS entry (not required for status)")
    parser.add_argument("--domain-suffix", type=str, help="Domain suffix (default: dev.local)")
    parser.add_argument("--ip", type=str, help="IP address (default: 127.0.0.1)")
    
    args = parser.parse_args()
    
    dns_manager = DNSManager()
    
    try:
        if args.action == "status":
            result = dns_manager.get_dns_status()
        elif args.action == "add":
            if not args.project_name:
                print("Error: project_name is required for add action", file=sys.stderr)
                sys.exit(1)
            result = dns_manager.add_dns_entry(args.project_name, args.domain_suffix, args.ip)
        elif args.action == "remove":
            if not args.project_name:
                print("Error: project_name is required for remove action", file=sys.stderr)
                sys.exit(1)
            result = dns_manager.remove_dns_entry(args.project_name, args.domain_suffix)
        
        print(json.dumps(result, indent=2))
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main() 