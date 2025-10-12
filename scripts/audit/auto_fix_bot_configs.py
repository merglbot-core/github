#!/usr/bin/env python3
"""
Auto-fix bot configuration issues.
"""

import json
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any


# Template bot configurations
BOT_CONFIG_TEMPLATES = {
    ".cursorrules": """# Cursor AI Development Rules

## Code Style and Best Practices
- Follow PEP 8 for Python code
- Use TypeScript for type safety in JavaScript projects
- Write clear, self-documenting code with meaningful variable names
- Add docstrings to all public functions and classes
- Keep functions small and focused on a single responsibility

## Security
- NEVER hardcode credentials or API keys
- NO secrets in code - use environment variables
- Always validate input data
- Sanitize output to prevent injection attacks
- Use environment variables for configuration
- Follow least privilege principle for permissions

## Error Handling
- Always handle exceptions appropriately
- Log errors with sufficient context
- Fail gracefully with user-friendly error messages
- Implement retry logic for network operations
- Use proper HTTP status codes in APIs

## Testing
- Write unit tests for all new functions
- Maintain test coverage above 80%
- Use mocking for external dependencies
- Test edge cases and error conditions

## Documentation
- Update README when adding new features
- Document API endpoints with examples
- Keep CHANGELOG up to date
- Add inline comments for complex logic
""",
    ".cursorbot": """{
  "version": "1.0",
  "rules": {
    "security": [
      "no secrets in code",
      "no hardcoded credentials",
      "validate input",
      "sanitize output",
      "use environment variables",
      "follow least privilege"
    ],
    "code_quality": [
      "write tests",
      "document functions",
      "handle errors",
      "use type hints"
    ]
  },
  "behaviors": {
    "auto_suggest": true,
    "explain_code": true,
    "suggest_improvements": true,
    "security_scanning": true
  }
}
""",
    "copilot-config.yml": """# GitHub Copilot Configuration
version: 1.0

# Suggestion settings
suggestions:
  max_length: 500
  include_comments: true
  include_docstrings: true

# Security settings
security:
  enabled: true
  alert_on_vulnerabilities: true
  block_insecure_code: true
  scan_dependencies: true

# Language preferences
languages:
  python:
    style_guide: "PEP 8"
    type_hints: true
  javascript:
    prefer_typescript: true
    style_guide: "StandardJS"
  go:
    style_guide: "Effective Go"

# Code review settings
code_review:
  enabled: true
  auto_review_prs: true
  check_style: true
  check_security: true

# Additional security scanning
security_scanning:
  scan_secrets: true
  scan_vulnerabilities: true
  scan_licenses: true
"""
}


def parse_bot_config_report(report_file: str) -> Dict[str, Any]:
    """Parse bot configuration audit report."""
    with open(report_file, "r") as f:
        report = json.load(f)
    
    issues = {
        "missing_configs": [],
        "invalid_configs": report.get("invalid_configs", []),
        "warnings": report.get("warnings", [])
    }
    
    # Check which configs are missing
    expected_configs = [".cursorrules", ".cursorbot", "copilot-config.yml"]
    found_configs = []
    
    for detail in report.get("details", []):
        config_file = Path(detail.get("file", "")).name
        if config_file:
            found_configs.append(config_file)
    
    for expected in expected_configs:
        if expected not in found_configs:
            issues["missing_configs"].append(expected)
    
    return issues


def fix_bot_configs(issues: Dict[str, Any], branch: str, dry_run: bool = False) -> int:
    """Fix bot configuration issues."""
    fixed_count = 0
    
    # Create bot-configs directory if it doesn't exist
    bot_configs_dir = Path("bot-configs")
    bot_configs_dir.mkdir(exist_ok=True)
    
    # Fix missing configurations
    for config_name in issues["missing_configs"]:
        if config_name in BOT_CONFIG_TEMPLATES:
            config_path = bot_configs_dir / config_name
            print(f"Creating missing config: {config_name}")
            
            with open(config_path, "w") as f:
                f.write(BOT_CONFIG_TEMPLATES[config_name])
            
            if not dry_run:
                subprocess.run(["git", "add", str(config_path)], check=True)
            
            fixed_count += 1
    
    # Fix invalid configurations by replacing with templates
    for config_name in issues["invalid_configs"]:
        # Extract just the filename from the path
        config_filename = Path(config_name).name
        
        if config_filename in BOT_CONFIG_TEMPLATES:
            config_path = bot_configs_dir / config_filename
            print(f"Replacing invalid config: {config_filename}")
            
            with open(config_path, "w") as f:
                f.write(BOT_CONFIG_TEMPLATES[config_filename])
            
            if not dry_run:
                subprocess.run(["git", "add", str(config_path)], check=True)
            
            fixed_count += 1
    
    return fixed_count


def main():
    parser = argparse.ArgumentParser(description="Auto-fix bot configuration issues")
    parser.add_argument("--report", required=True, help="Bot config audit report JSON file")
    parser.add_argument("--branch", required=True, help="Branch name for fixes")
    parser.add_argument("--dry-run", action="store_true", help="Simulate fixes without committing")
    
    args = parser.parse_args()
    
    # Parse report
    issues = parse_bot_config_report(args.report)
    
    total_issues = len(issues["missing_configs"]) + len(issues["invalid_configs"])
    
    if total_issues == 0:
        print("No bot configuration issues to fix")
        return
    
    print(f"Found {total_issues} bot configuration issues to fix")
    print(f"  Missing configs: {len(issues['missing_configs'])}")
    print(f"  Invalid configs: {len(issues['invalid_configs'])}")
    
    # Create or switch to branch
    if not args.dry_run:
        try:
            subprocess.run(["git", "checkout", "-b", args.branch], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Branch might already exist
            subprocess.run(["git", "checkout", args.branch], check=True, capture_output=True)
    
    # Fix issues
    fixed_count = fix_bot_configs(issues, args.branch, args.dry_run)
    
    # Commit changes
    if fixed_count > 0 and not args.dry_run:
        commit_message = f"fix(bot-config): Auto-fix {fixed_count} bot configuration issues"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        print(f"\n✅ Fixed {fixed_count} bot configuration issues")
    else:
        print(f"\n✅ Would fix {fixed_count} bot configuration issues (dry run)")


if __name__ == "__main__":
    main()