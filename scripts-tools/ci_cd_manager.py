#!/usr/bin/env python3
"""
Mendys Robot Scraper Platform - CI/CD Management Script

This script provides local CI/CD pipeline management and validation.
Usage: python scripts-tools/ci_cd_manager.py <command> [options]
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Colors for terminal output
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

class CICDManager:
    """Main CI/CD management class."""
    
    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.scripts_dir = self.project_root / "scripts-tools"
        self.frontend_dir = self.project_root / "frontend-app" / "frontend"
        self.backend_dir = self.project_root / "backend"
        self.src_dir = self.project_root / "src"
        
    def log_info(self, message: str) -> None:
        print(f"{Colors.BLUE}ℹ{Colors.END} {message}")
        
    def log_success(self, message: str) -> None:
        print(f"{Colors.GREEN}✅{Colors.END} {message}")
        
    def log_warning(self, message: str) -> None:
        print(f"{Colors.YELLOW}⚠{Colors.END} {message}")
        
    def log_error(self, message: str) -> None:
        print(f"{Colors.RED}❌{Colors.END} {message}")
        
    def log_header(self, message: str) -> None:
        print(f"\n{Colors.BOLD}{Colors.CYAN}{message}{Colors.END}")
        print("=" * len(message))
        
    def run_command(self, command: List[str], cwd: Optional[Path] = None, 
                   capture_output: bool = False) -> Tuple[int, str, str]:
        """Run a shell command and return exit code, stdout, stderr."""
        try:
            if cwd is None:
                cwd = self.project_root
                
            self.log_info(f"Running: {' '.join(command)} (in {cwd})")
            
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=capture_output,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            return result.returncode, result.stdout or "", result.stderr or ""
            
        except subprocess.TimeoutExpired:
            self.log_error(f"Command timed out: {' '.join(command)}")
            return 1, "", "Command timed out"
        except Exception as e:
            self.log_error(f"Failed to run command: {e}")
            return 1, "", str(e)
    
    def check_dependencies(self) -> bool:
        """Check if required tools are available."""
        self.log_header("Checking Dependencies")
        
        required_tools = {
            'python': ['python', '--version'],
            'npm': ['npm', '--version'],
            'git': ['git', '--version'],
        }
        
        missing_tools = []
        
        for tool, command in required_tools.items():
            exit_code, stdout, stderr = self.run_command(command, capture_output=True)
            if exit_code == 0:
                version = stdout.strip().split('\n')[0]
                self.log_success(f"{tool}: {version}")
            else:
                self.log_error(f"{tool}: Not found")
                missing_tools.append(tool)
        
        if missing_tools:
            self.log_error(f"Missing required tools: {', '.join(missing_tools)}")
            return False
            
        return True
    
    def check_python_code(self) -> bool:
        """Run Python code quality checks."""
        self.log_header("Python Code Quality Checks")
        
        python_dirs = []
        for dir_path in [self.src_dir, self.backend_dir, self.scripts_dir]:
            if dir_path.exists():
                python_dirs.append(str(dir_path))
        
        if not python_dirs:
            self.log_warning("No Python directories found")
            return True
            
        checks_passed = True
        
        # Install dev dependencies
        self.log_info("Installing Python development dependencies...")
        exit_code, _, _ = self.run_command(['pip', 'install', '-r', 'requirements-dev.txt'])
        if exit_code != 0:
            self.log_warning("Could not install dev dependencies, some checks may fail")
        
        # Black formatting check
        self.log_info("Checking Python code formatting (Black)...")
        exit_code, _, stderr = self.run_command(
            ['black', '--check', '--diff'] + python_dirs,
            capture_output=True
        )
        if exit_code == 0:
            self.log_success("Black formatting: PASSED")
        else:
            self.log_error("Black formatting: FAILED")
            checks_passed = False
        
        # Flake8 linting
        self.log_info("Running Python linting (flake8)...")
        exit_code, _, stderr = self.run_command(
            ['flake8'] + python_dirs + ['--max-line-length=88'],
            capture_output=True
        )
        if exit_code == 0:
            self.log_success("Flake8 linting: PASSED")
        else:
            self.log_error("Flake8 linting: FAILED")
            checks_passed = False
        
        return checks_passed
    
    def check_frontend_code(self) -> bool:
        """Run frontend code quality checks."""
        self.log_header("Frontend Code Quality Checks")
        
        if not self.frontend_dir.exists():
            self.log_warning("Frontend directory not found")
            return True
            
        checks_passed = True
        
        # Install dependencies
        self.log_info("Installing frontend dependencies...")
        exit_code, _, stderr = self.run_command(['npm', 'ci'], cwd=self.frontend_dir)
        if exit_code != 0:
            self.log_error("Failed to install frontend dependencies")
            return False
        
        # TypeScript type checking
        self.log_info("Running TypeScript type checking...")
        exit_code, _, stderr = self.run_command(
            ['npm', 'run', 'type-check'], 
            cwd=self.frontend_dir,
            capture_output=True
        )
        if exit_code == 0:
            self.log_success("TypeScript type checking: PASSED")
        else:
            self.log_error("TypeScript type checking: FAILED")
            checks_passed = False
        
        # ESLint
        self.log_info("Running ESLint...")
        exit_code, _, stderr = self.run_command(
            ['npm', 'run', 'lint'], 
            cwd=self.frontend_dir,
            capture_output=True
        )
        if exit_code == 0:
            self.log_success("ESLint: PASSED")
        else:
            self.log_error("ESLint: FAILED")
            checks_passed = False
        
        return checks_passed
    
    def run_all_checks(self) -> bool:
        """Run all CI/CD checks."""
        self.log_header("Running All CI/CD Checks")
        
        if not self.check_dependencies():
            return False
        
        all_passed = True
        
        if not self.check_python_code():
            all_passed = False
        
        if not self.check_frontend_code():
            all_passed = False
        
        if all_passed:
            self.log_success("All checks PASSED! 🎉")
        else:
            self.log_error("Some checks FAILED!")
        
        return all_passed

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="CI/CD Manager")
    parser.add_argument(
        'command',
        choices=['check-all', 'check-python', 'check-frontend'],
        help='Command to run'
    )
    
    args = parser.parse_args()
    
    manager = CICDManager()
    
    try:
        if args.command == 'check-all':
            success = manager.run_all_checks()
        elif args.command == 'check-python':
            success = manager.check_python_code()
        elif args.command == 'check-frontend':
            success = manager.check_frontend_code()
        else:
            manager.log_error(f"Unknown command: {args.command}")
            success = False
        
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        manager.log_warning("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        manager.log_error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
 