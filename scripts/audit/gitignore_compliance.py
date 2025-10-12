#!/usr/bin/env python3
"""
Check gitignore compliance across repositories.
"""

import json
import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Any


# Required patterns for different project types
REQUIRED_PATTERNS = {
    "backend": [
        "*.env",
        ".env.*",
        "*.key",
        "*.pem",
        "node_modules/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        "venv/",
        ".venv/",
        "dist/",
        "build/",
        "*.log",
        "logs/",
        ".DS_Store",
        "*.swp",
        ".idea/",
        ".vscode/",
        "coverage/",
        ".coverage",
    ],
    "frontend": [
        "node_modules/",
        "dist/",
        "build/",
        ".env",
        ".env.*",
        "*.log",
        "npm-debug.log*",
        ".DS_Store",
        ".idea/",
        ".vscode/",
        "coverage/",
        "*.map",
    ],
    "infrastructure": [
        "*.tfstate",
        "*.tfstate.*",
        ".terraform/",
        "*.tfvars",
        "!*.example.tfvars",
        "*.key",
        "*.pem",
        "*.p12",
        ".env",
        "kubeconfig*",
        "*.backup",
    ],
}


def detect_project_type(repo_path: str) -> str:
    """Detect the type of project based on files present."""
    path = Path(repo_path)
    
    # Check for infrastructure
    if (any(path.glob("*.tf")) or any(path.glob("*.yaml"))) and (path / ".terraform").exists():
        return "infrastructure"
    
    # Check for frontend
    if (path / "package.json").exists():
        with open(path / "package.json", "r") as f:
            content = json.load(f)
            if any(dep in content.get("dependencies", {}) for dep in ["react", "vue", "angular"]):
                return "frontend"
    
    # Check for backend
    if (path / "requirements.txt").exists() or (path / "pyproject.toml").exists():
        return "backend"
    if (path / "go.mod").exists():
        return "backend"
    if (path / "pom.xml").exists() or (path / "build.gradle").exists():
        return "backend"
    
    # Default to backend for general projects
    return "backend"


def check_gitignore_compliance(repo_path: str) -> Dict[str, Any]:
    """Check if repository has proper gitignore configuration."""
    path = Path(repo_path)
    gitignore_path = path / ".gitignore"
    
    result = {
        "repo": repo_path,
        "has_gitignore": gitignore_path.exists(),
        "project_type": detect_project_type(repo_path),
        "missing_patterns": [],
        "compliance_score": 0,
        "issues": []
    }
    
    if not gitignore_path.exists():
        result["issues"].append("No .gitignore file found")
        return result
    
    # Read gitignore content
    with open(gitignore_path, "r") as f:
        gitignore_content = f.read()
        gitignore_lines = [line.strip() for line in gitignore_content.splitlines() 
                          if line.strip() and not line.strip().startswith("#")]
    
    # Get required patterns for project type
    required = REQUIRED_PATTERNS.get(result["project_type"], REQUIRED_PATTERNS["backend"])
    
    # Check for missing patterns
    for pattern in required:
        # Simple check - can be improved with proper gitignore parsing
        if pattern.startswith("!"):
            # Negation pattern
            continue
        
        found = False
        for line in gitignore_lines:
            if pattern == line or (pattern.endswith('/') and line == pattern.rstrip('/')):
                found = True
                break
        
        if not found:
            result["missing_patterns"].append(pattern)
    
    # Calculate compliance score
    total_patterns = len(required)
    found_patterns = total_patterns - len(result["missing_patterns"])
    result["compliance_score"] = (found_patterns / total_patterns * 100) if total_patterns > 0 else 0
    
    # Check for potential secrets in repository
    result["potential_secrets"] = check_for_secrets(repo_path)
    
    if result["potential_secrets"]:
        result["issues"].append(f"Found {len(result['potential_secrets'])} potential secrets")
    
    if result["compliance_score"] < 80:
        result["issues"].append(f"Low compliance score: {result['compliance_score']:.1f}%")
    
    return result


def check_for_secrets(repo_path: str) -> List[str]:
    """Quick check for potential secrets in repository."""
    suspicious_files = []
    path = Path(repo_path)
    
    # Common secret file patterns
    secret_patterns = [
        "*.key", "*.pem", "*.p12", "*.pfx",
        ".env", ".env.*", "*.env",
        "credentials", "credentials.*",
        "secret", "secrets", "secret.*", "secrets.*",
        "config.prod.json", "settings.prod.py"
    ]
    
    for pattern in secret_patterns:
        for file in path.rglob(pattern):
            # Skip if in .git or node_modules
            if ".git" in str(file) or "node_modules" in str(file):
                continue
            suspicious_files.append(str(file.relative_to(path)))
    
    return suspicious_files


def validate_repo_name(repo: str) -> bool:
    """Validate repository name format."""
    import re
    # Repository name should be in format: owner/repo
    pattern = r'^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
    return bool(re.match(pattern, repo))


def audit_repositories(repos: List[str]) -> Dict[str, Any]:
    """Audit multiple repositories for gitignore compliance."""
    results = {
        "total_repos": len(repos),
        "compliant_repos": 0,
        "non_compliant_repos": 0,
        "repos_without_gitignore": 0,
        "total_issues": 0,
        "details": []
    }
    
    for repo in repos:
        print(f"Checking {repo}...")
        
        # Validate repository name to prevent command injection
        if not validate_repo_name(repo):
            print(f"ERROR: Invalid repository name format: {repo}")
            results["details"].append({
                "repo": repo,
                "has_gitignore": False,
                "compliance_score": 0,
                "issues": ["Invalid repository name format"]
            })
            results["non_compliant_repos"] += 1
            results["total_issues"] += 1
            continue
        
        # Clone or use existing repo
        import tempfile

        # Create a secure temporary directory and ensure cleanup
        with tempfile.TemporaryDirectory(prefix="audit-") as temp_dir:
            # Sanitize repo name for path - extract only the repo name after the slash
            # For 'owner/repo', we want just 'repo'
            if '/' in repo:
                repo_name = repo.split('/')[-1]
            else:
                repo_name = repo
            
            # Additional validation after extraction
            if not repo_name or ".." in repo_name or "/" in repo_name or "\\" in repo_name:
                print(f"ERROR: Invalid repository name after extraction: {repo_name} from {repo}")
                results["details"].append({
                    "repo": repo,
                    "has_gitignore": False,
                    "compliance_score": 0,
                    "issues": ["Invalid repository name for path"]
                })
                results["non_compliant_repos"] += 1
                results["total_issues"] += 1
                continue
            
            # Final sanitization - remove any remaining special characters
            import re
            repo_name = re.sub(r'[^a-zA-Z0-9_.-]', '', repo_name)
            if not repo_name:
                repo_name = "repo_" + str(abs(hash(repo)) % 100000)
                
            repo_path = Path(temp_dir) / repo_name
            
            if not repo_path.exists():
                # Clone the repository with depth limit and timeout
                try:
                    subprocess.run(
                        ["git", "clone", "--depth=1", f"https://github.com/{repo}.git", str(repo_path)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                except subprocess.CalledProcessError as e:
                    print(f"ERROR: Failed to clone {repo}: {e.stderr.strip() if e.stderr else e}")
                    # Record the error in results and continue to next repo
                    results["details"].append({
                        "repo": repo,
                        "has_gitignore": False,
                        "compliance_score": 0,
                        "issues": [f"Failed to clone repository: {e.stderr.strip() if e.stderr else str(e)}"]
                    })
                    results["repos_without_gitignore"] += 1
                    results["non_compliant_repos"] += 1
                    results["total_issues"] += 1
                    continue
            
            # Check compliance
            repo_result = check_gitignore_compliance(repo_path)
            results["details"].append(repo_result)
            
            # Update summary stats
            if not repo_result["has_gitignore"]:
                results["repos_without_gitignore"] += 1
                results["non_compliant_repos"] += 1
            elif repo_result["compliance_score"] >= 80:
                results["compliant_repos"] += 1
            else:
                results["non_compliant_repos"] += 1
            
            results["total_issues"] += len(repo_result["issues"])
    
    # Calculate overall compliance rate
    results["compliance_rate"] = (
        results["compliant_repos"] / results["total_repos"] * 100
        if results["total_repos"] > 0 else 0
    )
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Check gitignore compliance")
    parser.add_argument("--repos", required=True, help="JSON list of repositories")
    parser.add_argument("--output", required=True, help="Output file path")
    
    args = parser.parse_args()
    
    # Parse repository list
    repos = json.loads(args.repos)
    
    # Create output directory
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    
    # Run audit
    results = audit_repositories(repos)
    
    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    print(f"\n=== Gitignore Compliance Report ===")
    print(f"Total repositories: {results['total_repos']}")
    print(f"Compliant: {results['compliant_repos']}")
    print(f"Non-compliant: {results['non_compliant_repos']}")
    print(f"Without .gitignore: {results['repos_without_gitignore']}")
    print(f"Compliance rate: {results['compliance_rate']:.1f}%")
    print(f"Total issues: {results['total_issues']}")
    
    # Exit with error if compliance is below threshold
    if results["compliance_rate"] < 80:
        sys.exit(1)


if __name__ == "__main__":
    main()