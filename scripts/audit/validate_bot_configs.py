#!/usr/bin/env python3
"""
Validate bot configuration files.
"""

import json
import os
import sys
import argparse
import yaml
from pathlib import Path
from typing import Dict, List, Any


# Bot configuration schemas
BOT_SCHEMAS = {
    ".cursorrules": {
        "required_sections": [
            "code_style",
            "security",
            "error_handling"
        ],
        "required_keywords": [
            "typescript", "python", "security", "best practices"
        ]
    },
    ".cursorbot": {
        "required_fields": [
            "version",
            "rules",
            "behaviors"
        ]
    },
    "copilot-config.yml": {
        "required_fields": [
            "version",
            "suggestions",
            "security",
            "languages"
        ]
    },
    ".github/copilot/config.yml": {
        "required_fields": [
            "code_review",
            "suggestions",
            "security_scanning"
        ]
    }
}

# Security rules that should be present
REQUIRED_SECURITY_RULES = [
    "no secrets in code",
    "no hardcoded credentials",
    "validate input",
    "sanitize output",
    "use environment variables",
    "follow least privilege"
]


def validate_cursorrules(file_path: Path) -> Dict[str, Any]:
    """Validate .cursorrules file."""
    result = {
        "file": str(file_path),
        "valid": True,
        "issues": [],
        "warnings": []
    }
    
    if not file_path.exists():
        result["valid"] = False
        result["issues"].append("File does not exist")
        return result
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read().lower()
    
    schema = BOT_SCHEMAS[".cursorrules"]
    
    # Check for required sections
    for section in schema["required_sections"]:
        if section.lower() not in content:
            result["warnings"].append(f"Missing section: {section}")
    
    # Check for required keywords
    for keyword in schema["required_keywords"]:
        if keyword.lower() not in content:
            result["warnings"].append(f"Missing keyword: {keyword}")
    
    # Check for security rules
    security_rules_found = 0
    for rule in REQUIRED_SECURITY_RULES:
        if any(word in content for word in rule.split()):
            security_rules_found += 1
    
    if security_rules_found < 3:
        result["issues"].append("Insufficient security rules")
        result["valid"] = False
    
    return result


def validate_cursorbot(file_path: Path) -> Dict[str, Any]:
    """Validate .cursorbot file."""
    result = {
        "file": str(file_path),
        "valid": True,
        "issues": [],
        "warnings": []
    }
    
    if not file_path.exists():
        result["valid"] = False
        result["issues"].append("File does not exist")
        return result
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        result["valid"] = False
        result["issues"].append(f"Invalid JSON: {e}")
        return result
    
    schema = BOT_SCHEMAS[".cursorbot"]
    
    # Check required fields
    for field in schema["required_fields"]:
        if field not in data:
            result["issues"].append(f"Missing field: {field}")
            result["valid"] = False
    
    # Validate version
    if "version" in data:
        try:
            version = float(data["version"])
            if version < 1.0:
                result["warnings"].append(f"Old version: {version}")
        except (ValueError, TypeError):
            result["issues"].append(f"Invalid version format")
    
    # Check for security rules
    if "rules" in data:
        rules_str = str(data["rules"]).lower()
        security_count = sum(1 for rule in REQUIRED_SECURITY_RULES
                           if rule.lower() in rules_str)
        if security_count < 2:
            result["warnings"].append("Insufficient security rules in configuration")
    
    return result


def validate_copilot_config(file_path: Path) -> Dict[str, Any]:
    """Validate GitHub Copilot configuration."""
    result = {
        "file": str(file_path),
        "valid": True,
        "issues": [],
        "warnings": []
    }
    
    if not file_path.exists():
        result["valid"] = False
        result["issues"].append("File does not exist")
        return result
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            
        if data is None:
            result["valid"] = False
            result["issues"].append("Empty YAML file")
            return result
    except yaml.YAMLError as e:
        result["valid"] = False
        result["issues"].append(f"Invalid YAML: {e}")
        return result
    
    # Determine schema based on path
    if file_path.name == "copilot-config.yml":
        schema = BOT_SCHEMAS["copilot-config.yml"]
    else:
        schema = BOT_SCHEMAS[".github/copilot/config.yml"]
    
    # Check required fields
    for field in schema["required_fields"]:
        if field not in data:
            result["issues"].append(f"Missing field: {field}")
            result["valid"] = False
    
    # Check security settings
    if "security" in data:
        security = data["security"]
        if isinstance(security, dict):
            if not security.get("enabled", False):
                result["warnings"].append("Security scanning not enabled")
            if not security.get("alert_on_vulnerabilities", False):
                result["warnings"].append("Vulnerability alerts not enabled")
    else:
        result["issues"].append("No security configuration")
        result["valid"] = False
    
    # Check suggestions settings
    if "suggestions" in data:
        suggestions = data["suggestions"]
        if isinstance(suggestions, dict):
            if suggestions.get("max_length", 0) > 1000:
                result["warnings"].append("Very long suggestion length configured")
    
    return result


def validate_bot_configurations(repo_path: str = ".") -> Dict[str, Any]:
    """Validate all bot configurations in a repository."""
    path = Path(repo_path)
    results = {
        "repo": repo_path,
        "total_configs": 0,
        "valid_configs": 0,
        "invalid_configs": [],
        "warnings": [],
        "details": []
    }
    
    # Check for various bot configuration files
    config_files = [
        ("bot-configs/.cursorrules", validate_cursorrules),
        ("bot-configs/.cursorbot", validate_cursorbot),
        ("bot-configs/copilot-config.yml", validate_copilot_config),
        (".github/copilot/config.yml", validate_copilot_config),
        (".cursorrules", validate_cursorrules),
        (".cursorbot", validate_cursorbot)
    ]
    
    for config_file, validator in config_files:
        file_path = path / config_file
        if file_path.exists():
            results["total_configs"] += 1
            validation = validator(file_path)
            results["details"].append(validation)
            
            if validation["valid"]:
                results["valid_configs"] += 1
            else:
                results["invalid_configs"].append(str(config_file))
            
            results["warnings"].extend(validation.get("warnings", []))
    
    # Calculate compliance score
    if results["total_configs"] > 0:
        results["compliance_score"] = (results["valid_configs"] / results["total_configs"]) * 100
    else:
        results["compliance_score"] = 0
        results["warnings"].append("No bot configuration files found")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Validate bot configurations")
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument("--output", required=True, help="Output file path")
    
    args = parser.parse_args()
    
    # Create output directory
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    
    # Run validation
    results = validate_bot_configurations(args.repo)
    
    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    print(f"\n=== Bot Configuration Validation ===")
    print(f"Total configurations: {results['total_configs']}")
    print(f"Valid: {results['valid_configs']}")
    print(f"Invalid: {len(results['invalid_configs'])}")
    print(f"Compliance score: {results['compliance_score']:.1f}%")
    
    if results['invalid_configs']:
        print(f"\nInvalid configurations:")
        for config in results['invalid_configs']:
            print(f"  - {config}")
    
    if results['warnings']:
        print(f"\nWarnings ({len(results['warnings'])}):")
        for warning in results['warnings'][:5]:
            print(f"  - {warning}")
        if len(results['warnings']) > 5:
            print(f"  ... and {len(results['warnings']) - 5} more")
    
    # Exit with error if compliance is low
    if results["compliance_score"] < 70:
        sys.exit(1)


if __name__ == "__main__":
    main()