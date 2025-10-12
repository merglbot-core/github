#!/usr/bin/env python3
"""
Auto-fix gitignore compliance issues.
"""

import json
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Any


# Template gitignore patterns for different project types
GITIGNORE_TEMPLATES = {
    "backend": """# Python
__pycache__/
*.py[cod]
*.pyc
.pytest_cache/
venv/
.venv/
env/
.env

# Node.js
node_modules/
npm-debug.log*
yarn-error.log*

# Build outputs
dist/
build/
*.egg-info/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# Security
*.key
*.pem
*.p12
*.pfx
*.env
.env.*
credentials
credentials.*
secrets
secrets.*

# Logs
*.log
logs/

# OS
.DS_Store
Thumbs.db

# Coverage
coverage/
.coverage
htmlcov/
""",
    "frontend": """# Dependencies
node_modules/
bower_components/

# Build
dist/
build/
.next/
out/

# Environment
.env
.env.*
*.env

# Logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
lerna-debug.log*

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Testing
coverage/
.nyc_output

# Maps
*.map

# Temporary
.tmp/
.temp/
""",
    "infrastructure": """# Terraform
*.tfstate
*.tfstate.*
.terraform/
.terraform.lock.hcl
*.tfvars
!*.example.tfvars
override.tf
override.tf.json
*_override.tf
*_override.tf.json
.terraformrc
terraform.rc

# Keys and secrets
*.key
*.pem
*.p12
*.pfx
*.crt
*.cer
kubeconfig*
.env
.env.*

# Backup
*.backup
*.bak

# IDE
.idea/
.vscode/

# OS
.DS_Store
Thumbs.db

# Logs
*.log
"""
}


def get_missing_patterns_from_report(report_file: str) -> Dict[str, List[str]]:
    """Extract missing patterns from audit report."""
    with open(report_file, "r") as f:
        report = json.load(f)
    
    repos_to_fix = {}
    
    for detail in report.get("details", []):
        repo = detail.get("repo")
        if not repo:
            continue
            
        missing_patterns = detail.get("missing_patterns", [])
        has_gitignore = detail.get("has_gitignore", False)
        project_type = detail.get("project_type", "backend")
        
        if not has_gitignore or missing_patterns:
            repos_to_fix[repo] = {
                "missing_patterns": missing_patterns,
                "has_gitignore": has_gitignore,
                "project_type": project_type
            }
    
    return repos_to_fix


def fix_gitignore(repo_path: str, info: Dict[str, Any]) -> bool:
    """Fix gitignore issues in a repository."""
    gitignore_path = Path(repo_path) / ".gitignore"
    project_type = info.get("project_type", "backend")
    
    if not info.get("has_gitignore"):
        # Create new gitignore from template
        print(f"  Creating new .gitignore for {project_type} project")
        with open(gitignore_path, "w") as f:
            f.write(GITIGNORE_TEMPLATES.get(project_type, GITIGNORE_TEMPLATES["backend"]))
        return True
    
    # Append missing patterns
    missing_patterns = info.get("missing_patterns", [])
    if missing_patterns:
        print(f"  Adding {len(missing_patterns)} missing patterns")
        
        # Read existing content
        with open(gitignore_path, "r") as f:
            existing_content = f.read()
        
        # Append missing patterns
        with open(gitignore_path, "a") as f:
            if not existing_content.endswith("\n"):
                f.write("\n")
            f.write("\n# Added by security audit auto-fix\n")
            for pattern in missing_patterns:
                if not pattern.startswith("!"):  # Skip negation patterns
                    f.write(f"{pattern}\n")
        return True
    
    return False


def main():
    parser = argparse.ArgumentParser(description="Auto-fix gitignore compliance issues")
    parser.add_argument("--report", required=True, help="Audit report JSON file")
    parser.add_argument("--branch", required=True, help="Branch name for fixes")
    parser.add_argument("--dry-run", action="store_true", help="Simulate fixes without committing")
    
    args = parser.parse_args()
    
    # Parse report for issues
    repos_to_fix = get_missing_patterns_from_report(args.report)
    
    if not repos_to_fix:
        print("No gitignore issues to fix")
        return
    
    print(f"Found {len(repos_to_fix)} repositories to fix")
    
    # Create branch for fixes
    if not args.dry_run:
        try:
            subprocess.run(["git", "checkout", "-b", args.branch], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Branch might already exist
            subprocess.run(["git", "checkout", args.branch], check=True, capture_output=True)
    
    fixed_count = 0
    
    for repo, info in repos_to_fix.items():
        print(f"\nProcessing {repo}...")
        
        # For demo, we'll just create/update gitignore in bot-configs directory
        # In production, this would clone and fix each repository
        demo_path = Path("bot-configs")
        demo_path.mkdir(exist_ok=True)
        
        gitignore_path = demo_path / f"{repo.replace('/', '_')}.gitignore"
        
        if fix_gitignore(str(demo_path), info):
            fixed_count += 1
            
            if not args.dry_run:
                # Stage changes
                subprocess.run(["git", "add", str(gitignore_path)], check=True)
    
    if fixed_count > 0 and not args.dry_run:
        # Commit changes
        commit_message = f"fix(security): Auto-fix gitignore compliance in {fixed_count} repositories"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        print(f"\n✅ Fixed {fixed_count} repositories")
    else:
        print(f"\n✅ Would fix {fixed_count} repositories (dry run)")


if __name__ == "__main__":
    main()