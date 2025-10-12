#!/usr/bin/env python3
"""
Generate consolidated security audit report.
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any


def generate_html_report(data: Dict[str, Any], audit_id: str, quarter: str) -> str:
    """Generate HTML report from audit data."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Security Audit Report - {quarter} - {audit_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; line-height: 1.6; }}
        h1 {{ color: #333; border-bottom: 3px solid #0366d6; padding-bottom: 10px; }}
        h2 {{ color: #0366d6; margin-top: 30px; }}
        .summary {{ background: #f6f8fa; padding: 15px; border-radius: 6px; margin: 20px 0; }}
        .critical {{ color: #d73a49; font-weight: bold; }}
        .warning {{ color: #e36209; }}
        .success {{ color: #28a745; }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .metric-value {{ font-size: 24px; font-weight: bold; }}
        .metric-label {{ color: #586069; font-size: 14px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th {{ background: #f6f8fa; padding: 10px; text-align: left; border: 1px solid #e1e4e8; }}
        td {{ padding: 10px; border: 1px solid #e1e4e8; }}
        .issue-list {{ list-style-type: none; padding: 0; }}
        .issue-item {{ background: #fff3cd; padding: 10px; margin: 5px 0; border-left: 4px solid #e36209; }}
    </style>
</head>
<body>
    <h1>🔒 Security Audit Report</h1>
    <div class="summary">
        <p><strong>Audit ID:</strong> {audit_id}</p>
        <p><strong>Quarter:</strong> {quarter}</p>
        <p><strong>Generated:</strong> {datetime.now().isoformat()}</p>
    </div>
"""

    # Add metrics summary
    html += "<h2>📊 Summary Metrics</h2><div class='summary'>"
    
    # Gitignore compliance
    if "gitignore" in data:
        git_data = data["gitignore"]
        compliance_class = "success" if git_data.get("compliance_rate", 0) >= 80 else "critical"
        html += f"""
        <div class="metric">
            <div class="metric-label">Gitignore Compliance</div>
            <div class="metric-value {compliance_class}">{git_data.get('compliance_rate', 0):.1f}%</div>
        </div>
        """
    
    # Secrets found
    if "secrets" in data:
        secrets_data = data["secrets"]
        secrets_found = len(secrets_data.get("findings", []))
        secrets_class = "critical" if secrets_found > 0 else "success"
        html += f"""
        <div class="metric">
            <div class="metric-label">Secrets Detected</div>
            <div class="metric-value {secrets_class}">{secrets_found}</div>
        </div>
        """
    
    # Bot config compliance
    if "bot_config" in data:
        bot_data = data["bot_config"]
        bot_class = "success" if bot_data.get("compliance_score", 0) >= 70 else "warning"
        html += f"""
        <div class="metric">
            <div class="metric-label">Bot Config Score</div>
            <div class="metric-value {bot_class}">{bot_data.get('compliance_score', 0):.1f}%</div>
        </div>
        """
    
    # Vulnerabilities
    if "dependencies" in data:
        deps_data = data["dependencies"]
        vulns = deps_data.get("vulnerabilities", {})
        critical_count = vulns.get("critical", 0)
        high_count = vulns.get("high", 0)
        vuln_class = "critical" if critical_count > 0 or high_count > 0 else "success"
        html += f"""
        <div class="metric">
            <div class="metric-label">Critical/High Vulnerabilities</div>
            <div class="metric-value {vuln_class}">{critical_count + high_count}</div>
        </div>
        """
    
    html += "</div>"
    
    # Detailed sections
    if "gitignore" in data and "details" in data["gitignore"]:
        html += "<h2>🔍 Gitignore Compliance Details</h2>"
        html += "<table><tr><th>Repository</th><th>Compliance Score</th><th>Issues</th></tr>"
        for repo in data["gitignore"]["details"]:
            score = repo.get("compliance_score", 0)
            score_class = "success" if score >= 80 else "critical"
            issues = ", ".join(repo.get("issues", [])) or "None"
            html += f"""<tr>
                <td>{repo.get('repo', 'Unknown')}</td>
                <td class='{score_class}'>{score:.1f}%</td>
                <td>{issues}</td>
            </tr>"""
        html += "</table>"
    
    # Action items
    html += "<h2>⚠️ Action Items</h2><ul class='issue-list'>"
    
    if "secrets" in data and data["secrets"].get("findings"):
        html += "<li class='issue-item'>🔴 <strong>Critical:</strong> Rotate detected secrets immediately</li>"
    
    if "gitignore" in data and data["gitignore"].get("repos_without_gitignore", 0) > 0:
        html += f"<li class='issue-item'>🟠 Add .gitignore files to {data['gitignore']['repos_without_gitignore']} repositories</li>"
    
    if "dependencies" in data and data["dependencies"].get("vulnerabilities", {}).get("critical", 0) > 0:
        html += "<li class='issue-item'>🔴 <strong>Critical:</strong> Update dependencies with critical vulnerabilities</li>"
    
    html += "</ul>"
    
    html += """
</body>
</html>
"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Generate consolidated security audit report")
    parser.add_argument("--input-dir", required=True, help="Directory containing audit artifacts")
    parser.add_argument("--output", required=True, help="Output HTML file path")
    parser.add_argument("--audit-id", required=True, help="Audit ID")
    parser.add_argument("--quarter", default="Manual", help="Quarter identifier")
    
    args = parser.parse_args()
    
    # Collect all audit data
    audit_data = {}
    input_path = Path(args.input_dir)
    
    # Read gitignore audit
    gitignore_files = list(input_path.glob("gitignore-audit-*/gitignore-audit.json"))
    if gitignore_files:
        with open(gitignore_files[0], "r") as f:
            audit_data["gitignore"] = json.load(f)
    
    # Read secrets audit
    secrets_files = list(input_path.glob("secrets-audit-*/secrets-audit.json"))
    if secrets_files:
        try:
            with open(secrets_files[0], "r") as f:
                audit_data["secrets"] = json.load(f)
        except json.JSONDecodeError:
            # Handle empty or invalid secrets file
            audit_data["secrets"] = {"findings": []}
    
    # Read bot config audit
    bot_files = list(input_path.glob("bot-config-audit-*/bot-config-audit.json"))
    if bot_files:
        with open(bot_files[0], "r") as f:
            audit_data["bot_config"] = json.load(f)
    
    # Read dependency audit
    dep_files = list(input_path.glob("dependency-audit-*/dependency-audit.json"))
    if dep_files:
        with open(dep_files[0], "r") as f:
            dep_data = json.load(f)
            # Process vulnerability counts
            critical = 0
            high = 0
            medium = 0
            
            if "Results" in dep_data:
                for result in dep_data["Results"]:
                    if "Vulnerabilities" in result:
                        for vuln in result["Vulnerabilities"]:
                            severity = vuln.get("Severity", "").upper()
                            if severity == "CRITICAL":
                                critical += 1
                            elif severity == "HIGH":
                                high += 1
                            elif severity == "MEDIUM":
                                medium += 1
            
            audit_data["dependencies"] = {
                "vulnerabilities": {
                    "critical": critical,
                    "high": high,
                    "medium": medium
                }
            }
    
    # Generate HTML report
    html_report = generate_html_report(audit_data, args.audit_id, args.quarter)
    
    # Save report
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html_report)
    
    print(f"Report generated: {args.output}")


if __name__ == "__main__":
    main()