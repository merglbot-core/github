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
from typing import Dict, List, Any, Optional, Tuple


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


ALLOWED_IDENTIFIER_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/@"
)
# Bounds to guard against pathological identifiers and filesystem operations
MAX_IDENTIFIER_INPUT_LENGTH = 4096  # matches common filesystem path limits
MAX_IDENTIFIER_SEGMENTS = 10  # repo identifiers should rarely exceed this depth
MAX_PATH_LENGTH = 4096
MAX_SECRET_MATCHES_PER_PATTERN = 500
MAX_TOTAL_SECRET_MATCHES = 5000


def sanitize_identifier(identifier: Any) -> Tuple[str, Optional[str]]:
    """
    Normalize repository identifiers to a constrained, safe form so audit
    reports cannot inject path traversal or control characters.
    """
    raw = str(identifier)
    length_truncated = False
    if len(raw) > MAX_IDENTIFIER_INPUT_LENGTH:
        raw = raw[:MAX_IDENTIFIER_INPUT_LENGTH]
        length_truncated = True
    filtered = "".join(char for char in raw if char in ALLOWED_IDENTIFIER_CHARS)
    normalized = filtered.replace(":", "/").replace("@", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.strip("/.")
    traversal_detected = False
    segments: List[str] = []
    reserved_names = {".git", ".ssh", ""}
    # Simplified sanitization loop
    for segment in normalized.split("/"):
        if not segment or segment == ".":
            continue
        if ".." in segment:
            traversal_detected = True
            continue
        
        seg = segment
        if seg.startswith("."):
            traversal_detected = True
            seg = seg.lstrip(".")
        seg = seg.rstrip(".")
        
        # Check reserved names before further processing if needed, 
        # but here we just check the final segment
        if not seg or seg in reserved_names:
            traversal_detected = True
            continue
            
        segments.append(seg.lower())
        if len(segments) >= MAX_IDENTIFIER_SEGMENTS:
            traversal_detected = True
            break
            
    cleaned = "/".join(segments)
    
    note: Optional[str] = None
    if not cleaned:
        # Prevent collision for unknown repositories by appending a hash of the original
        import hashlib
        repo_hash = hashlib.md5(str(identifier).encode()).hexdigest()[:8]
        cleaned = f"unknown-repository-{repo_hash}"
        note = "Repository identifier missing; substituted placeholder with hash"
    elif len(cleaned) > 253:
        cleaned = f"{cleaned[:250]}..."
        note = "Repository identifier truncated for reporting"
    elif traversal_detected:
        note = "Repository identifier sanitized for path traversal segments"
    elif cleaned != raw or length_truncated:
        note = "Repository identifier sanitized for reporting"
    return cleaned, note


def _any_rglob_pruned(root: Path, pattern: str, prune: List[str]) -> bool:
    """Check for pattern match while pruning specific directories."""
    try:
        for base, dirs, files in os.walk(root, topdown=True):
            # prune heavy/common directories in place
            dirs[:] = [d for d in dirs if d not in prune]
            
            # Check files in current directory
            # Simple glob matching for extension
            if pattern.startswith("*."):
                ext = pattern[1:]
                if any(f.endswith(ext) for f in files):
                    return True
            # Fallback for other patterns (less efficient but functional)
            else:
                import fnmatch
                if any(fnmatch.fnmatch(f, pattern) for f in files):
                    return True
    except OSError:
        return False
    return False


def detect_project_type(repo_path: str) -> str:
    """Detect the type of project based on files present."""
    path = Path(repo_path).resolve()
    prune_dirs = ["node_modules", ".git", "vendor", ".venv", "venv", "dist", "build"]
    
    # Check for infrastructure markers with short-circuiting
    # Combine checks to avoid redundant existence checks
    has_tf = any(path.glob("*.tf"))
    if not has_tf:
        infra_dir = path / "infra"
        if infra_dir.exists():
            has_tf = _any_rglob_pruned(infra_dir, "*.tf", prune_dirs)
            
    has_k8s_yaml = False
    if not has_tf: # Optimization: don't check k8s if already infra
        for kdir in (path / "k8s", path / "kubernetes"):
            if kdir.exists():
                if _any_rglob_pruned(kdir, "*.yaml", prune_dirs):
                    has_k8s_yaml = True
                    break

    has_helm = (path / "Chart.yaml").exists() or (path / "charts").exists()
    has_kustomize = (path / "kustomization.yaml").exists()
    has_compose = (path / "docker-compose.yaml").exists() or (path / "compose.yaml").exists()
    
    if has_tf or has_k8s_yaml or has_helm or has_kustomize or has_compose:
        return "infrastructure"
    
    # Check for frontend
    pkg = path / "package.json"
    if pkg.exists():
        try:
            # Skip large package.json files (likely generated or non-standard) to avoid performance issues
            if pkg.stat().st_size > 2 * 1024 * 1024:
                return "backend"
            with pkg.open("r", encoding="utf-8", errors="replace") as f:
                content = json.load(f)
            deps = content.get("dependencies") or {}
            dev_deps = content.get("devDependencies") or {}
            frontend_markers = {"react", "vue", "angular", "next", "nuxt", "vite"}
            if any(marker in deps for marker in frontend_markers) or any(
                marker in dev_deps for marker in frontend_markers
            ):
                return "frontend"
            if (path / "public").exists() or (path / "src" / "index.html").exists():
                return "frontend"
        except (OSError, json.JSONDecodeError):
            pass
    
    # Check for backend
    if (path / "requirements.txt").exists() or (path / "pyproject.toml").exists():
        return "backend"
    if (path / "go.mod").exists():
        return "backend"
    if (path / "pom.xml").exists() or (path / "build.gradle").exists():
        return "backend"
    
    # Default to backend for general projects
    return "backend"


def check_gitignore_compliance(repo_path: str, repo_identifier: str) -> Dict[str, Any]:
    """
    Check if repository has proper gitignore configuration.
    
    Args:
        repo_path (str): Filesystem path to the repository under audit.
        repo_identifier (str): Human-readable identifier (e.g., owner/repo).
    
    Returns:
        Dict[str, Any]: Compliance details including missing patterns and issues.
    """
    safe_identifier, identifier_note = sanitize_identifier(repo_identifier)
    issues: List[str] = []
    if identifier_note:
        issues.append(identifier_note)
    path = Path(repo_path).resolve()
    
    if not path.exists() or not path.is_dir():
        issues.append("invalid_repository_path")
        return {
            "repo": safe_identifier,
            "has_gitignore": False,
            "project_type": "unknown",
            "missing_patterns": [],
            "compliance_score": 0,
            "issues": issues
        }
    
    gitignore_path = path / ".gitignore"
    
    result = {
        "repo": safe_identifier,
        "has_gitignore": gitignore_path.exists(),
        "project_type": detect_project_type(str(path)),
        "missing_patterns": [],
        "compliance_score": 0,
        "issues": issues
    }
    
    if not gitignore_path.exists():
        result["issues"].append("No .gitignore file found")
        return result
    
    # Read gitignore content safely
    try:
        with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
            gitignore_content = f.read()
    except OSError as exc:
        result["issues"].append(f".gitignore_unreadable:{type(exc).__name__}")
        return result
    gitignore_lines: List[str] = []
    for line in gitignore_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        gitignore_lines.append(stripped)
    
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
    result["potential_secrets"] = check_for_secrets(str(path))
    
    if result["potential_secrets"]:
        result["issues"].append(f"Found {len(result['potential_secrets'])} potential secrets")
    
    if result["compliance_score"] < 80:
        result["issues"].append(f"Low compliance score: {result['compliance_score']:.1f}%")
    
    return result


def check_for_secrets(repo_path: str) -> List[str]:
    """Quick check for potential secrets in repository."""
    suspicious_files: List[str] = []
    path = Path(repo_path).resolve()
    base_path = path
    
    # Common secret file patterns
    secret_patterns = [
        "*.key", "*.pem", "*.p12", "*.pfx",
        ".env", ".env.*", "*.env",
        "credentials", "credentials.*",
        "secret", "secrets", "secret.*", "secrets.*",
        "config.prod.json", "settings.prod.py"
    ]
    
    total_matches = 0
    for pattern in secret_patterns:
        if total_matches >= MAX_TOTAL_SECRET_MATCHES:
            break
        per_pattern_matches = 0
        for file in path.rglob(pattern):
            if (
                total_matches >= MAX_TOTAL_SECRET_MATCHES
                or per_pattern_matches >= MAX_SECRET_MATCHES_PER_PATTERN
            ):
                break
            try:
                if file.is_symlink() or not file.is_file():
                    continue
                resolved = file.resolve()
            except OSError:
                continue
            if len(str(resolved)) > MAX_PATH_LENGTH:
                continue
            try:
                relative = resolved.relative_to(base_path)
            except ValueError:
                continue
            
            # Efficiently check for excluded directories using string operations
            # instead of creating a set of parts which is slower
            rel_str = str(relative)
            if ".git/" in rel_str or "node_modules/" in rel_str or rel_str.startswith(".git") or rel_str.startswith("node_modules"):
                continue
                
            suspicious_files.append(str(relative))
            total_matches += 1
            per_pattern_matches += 1
    
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
            
            # Check compliance and isolate per-repo failures
            try:
                repo_result = check_gitignore_compliance(str(repo_path), repo)
            except Exception as exc:
                safe_repo, note = sanitize_identifier(repo)
                print(
                    f"[ERROR] compliance_check_failed for {safe_repo!r}: {type(exc).__name__}",
                    file=sys.stderr,
                )
                issues = [f"compliance_check_failed:{type(exc).__name__}"]
                if note:
                    issues.append(note)
                repo_result = {
                    "repo": safe_repo,
                    "has_gitignore": False,
                    "project_type": "unknown",
                    "missing_patterns": [],
                    "compliance_score": 0,
                    "issues": issues
                }
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
