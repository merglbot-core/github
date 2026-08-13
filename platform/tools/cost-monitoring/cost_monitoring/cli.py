"""
Cost Monitoring CLI - Main entry point.
"""

import datetime as dt
import logging
import os
import pathlib
import sys
from typing import Any, Dict

import click
import yaml
from github import Github
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Import all local modules at module level
from .alerting.notifiers import send_cost_report_to_slack
from .alerting.thresholds import evaluate_all_thresholds, format_alert_message
from .monitor.gcp_monitor import collect_gcp
from .monitor.github_monitor import collect_github
from .report.writers import write_all_reports
from .realized_savings import RealizedSavingsError, verify as verify_realized_savings, write_receipt

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

console = Console()


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
def cli(debug):
    """Cost Monitoring Tool for Merglbot Enterprise."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command()
@click.option("--month", help="YYYY-MM. Default current.", default=None)
@click.option("--config", default="config/settings.yml", help="Path to settings file")
@click.option("--thresholds", default="config/thresholds.yml", help="Path to thresholds file")
@click.option("--outdir", default="reports", help="Output directory for reports")
@click.option("--formats", default="csv,md,json", help="Report formats to generate")
@click.option("--dry-run", is_flag=True, default=False, help="Don't send notifications")
@click.option("--soft-fail", is_flag=True, default=False, help="Exit 0 even if thresholds exceeded")
def generate(month, config, thresholds, outdir, formats, dry_run, soft_fail):
    """Generate cost reports and evaluate thresholds."""

    try:
        console.print(Panel.fit("🚀 [bold cyan]Starting Cost Monitoring[/bold cyan]"))

        # 1. Load configurations
        console.print("[bold]Loading configuration...[/bold]")
        config_data = load_config(config)
        threshold_data = load_config(thresholds)

        # 2. Determine month
        if not month:
            month = dt.datetime.now().strftime("%Y-%m")
        console.print(f"📅 Monitoring period: [bold green]{month}[/bold green]")

        # 3. Collect GitHub data
        console.print("\n[bold]Collecting GitHub data...[/bold]")
        github_data = collect_github(
            config_data["github"]["enterprise"], config_data["github"]["orgs"], config_data["github"]["pricing"]
        )
        console.print("✅ GitHub data collected successfully")
        display_github_summary(github_data)

        # 4. Collect GCP data
        console.print("\n[bold]Collecting GCP data...[/bold]")
        gcp_data = collect_gcp(config_data["gcp"], month)
        console.print("✅ GCP data collected successfully")
        display_gcp_summary(gcp_data)

        # 5. Evaluate thresholds
        console.print("\n[bold]Evaluating thresholds...[/bold]")
        threshold_result = evaluate_all_thresholds(github_data, gcp_data, threshold_data)

        if threshold_result["threshold_exceeded"]:
            console.print(f"[red]⚠️ {len(threshold_result['alerts'])} threshold(s) exceeded![/red]")
            display_alerts(threshold_result["alerts"])
        else:
            console.print("[green]✅ All costs within thresholds[/green]")

        # 6. Write reports
        console.print("\n[bold]Generating reports...[/bold]")

        # Prepare combined data
        combined_data = {"month": month, "github": github_data, "gcp": gcp_data, "alerts": threshold_result["alerts"]}

        # Write reports
        report_paths = write_all_reports(outdir, combined_data, month)

        console.print("[green]✅ Reports generated:[/green]")
        for format_name, path in report_paths.items():
            console.print(f"   • {format_name.upper()}: {path}")

        # 7. Send notifications if not dry-run and thresholds exceeded
        if not dry_run and threshold_result["threshold_exceeded"]:
            console.print("\n[bold]Sending notifications...[/bold]")

            # Send Slack notification
            if send_cost_report_to_slack(github_data, gcp_data, threshold_result["alerts"], month):
                console.print("[green]✅ Slack notification sent[/green]")
            else:
                console.print("[yellow]⚠️ Slack notification skipped or failed[/yellow]")

            # Create GitHub issue
            if create_github_issue_for_alerts(threshold_result["alerts"], month, combined_data):
                console.print("[green]✅ GitHub issue created[/green]")
            else:
                console.print("[yellow]⚠️ GitHub issue creation skipped or failed[/yellow]")

        elif dry_run:
            console.print("\n[yellow]ℹ️ Dry-run mode: No notifications sent[/yellow]")

        # 8. Print final summary
        console.print("\n" + "=" * 60)
        display_final_summary(github_data, gcp_data, threshold_result)

        # 9. Exit code
        if threshold_result["threshold_exceeded"] and not soft_fail:
            console.print("\n[red]❌ Exiting with code 2 (thresholds exceeded)[/red]")
            sys.exit(2)
        else:
            console.print("\n[green]✅ Complete![/green]")
            sys.exit(0)

    except Exception as e:
        # Provider exceptions can contain request details; emit only the class.
        error_class = type(e).__name__
        console.print(f"\n[red]Fatal cost-monitoring error ({error_class})[/red]")
        logger.error("Fatal cost-monitoring error (%s)", error_class)
        sys.exit(1)


@cli.command("realized-savings")
@click.option("--config", default="config/realized-savings.yml", help="Path to the action measurement contract")
@click.option("--out", default="reports/realized-savings.json", help="Sanitized aggregate receipt path")
@click.option("--as-of", default=None, help="UTC ISO-8601 evaluation time (tests/manual replay only)")
@click.option("--query-dry-run", is_flag=True, default=False, help="Estimate bytes without reading billing rows")
def realized_savings(config, out, as_of, query_dry_run):
    """Verify equal 30-day pre/post FinOps billing windows."""

    try:
        evaluation_time = None
        if as_of:
            evaluation_time = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if evaluation_time.tzinfo is None:
                raise ValueError("--as-of must include a timezone")
        receipt = verify_realized_savings(
            config,
            as_of=evaluation_time,
            query_dry_run=query_dry_run,
        )
        write_receipt(out, receipt)
        console.print(receipt["verdict"])
        if receipt["verdict"] in {"DATA_GAP", "MISMATCH"}:
            raise click.exceptions.Exit(1)
    except click.exceptions.Exit:
        raise
    except (OSError, ValueError, RealizedSavingsError) as exc:
        console.print(f"[red]Realized-savings verifier failed closed ({type(exc).__name__})[/red]")
        raise click.exceptions.Exit(1) from None


@cli.command()
@click.option("--config", default="config/thresholds.yml", help="Path to thresholds file")
def validate_thresholds(config):
    """Validate threshold configuration file."""
    try:
        console.print("[bold]Validating threshold configuration...[/bold]")
        data = load_config(config)

        # Validate structure
        errors = []

        if "github" not in data:
            errors.append("Missing 'github' section")
        else:
            if "copilot" not in data["github"]:
                errors.append("Missing 'github.copilot' section")

        if "gcp" not in data:
            errors.append("Missing 'gcp' section")
        else:
            if "currency" not in data["gcp"]:
                errors.append("Missing 'gcp.currency'")
            if "defaults" not in data["gcp"]:
                errors.append("Missing 'gcp.defaults' section")

        if errors:
            console.print("[red]❌ Validation errors:[/red]")
            for error in errors:
                console.print(f"   • {error}")
            sys.exit(1)
        else:
            console.print("[green]✅ Configuration valid[/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option("--config", default="config/settings.yml", help="Path to settings file")
def print_config(config):
    """Print effective configuration (without secrets)."""
    try:
        data = load_config(config)

        # Mask sensitive values
        if "gcp" in data and "billing_account_id" in data["gcp"]:
            if data["gcp"]["billing_account_id"] != "XXXX-XXXX-XXXX":
                data["gcp"]["billing_account_id"] = "****-****-****"

        console.print(Panel.fit("[bold]Effective Configuration[/bold]"))
        console.print_json(data=data)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def load_config(path: str) -> Dict[str, Any]:
    """Load YAML configuration file."""
    config_path = pathlib.Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def display_github_summary(data: Dict[str, Any]):
    """Display GitHub cost summary table."""
    table = Table(title="GitHub Costs", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    copilot = data.get("copilot", {})
    ec = data.get("enterprise_cloud", {})

    table.add_row("Copilot Seats", str(copilot.get("seats_assigned", 0)))
    table.add_row("Copilot Cost", f"${copilot.get('monthly_cost_usd', 0):,.2f}")
    table.add_row("Enterprise Seats", str(ec.get("seats", 0)))
    table.add_row("Enterprise Cost", f"${ec.get('monthly_cost_usd', 0):,.2f}")
    total_members = data.get("total_members")
    table.add_row("Total Members", str(total_members) if total_members is not None else "Not collected")
    table.add_row("[bold]Total Cost[/bold]", f"[bold]${data.get('total_monthly_cost_usd', 0):,.2f}[/bold]")

    console.print(table)


def display_gcp_summary(data: Dict[str, Any]):
    """Display GCP cost summary table."""
    table = Table(title="GCP Costs", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    currency = data.get("currency", "")
    table.add_row("Projects Monitored", str(data.get("projects_monitored", 0)))
    table.add_row("Total Cost", f"{data.get('total_cost', 0):,.2f} {currency}")
    table.add_row("Total Credits", f"{data.get('total_credits', 0):,.2f} {currency}")
    table.add_row("[bold]Net Cost[/bold]", f"[bold]{data.get('total_net', 0):,.2f} {currency}[/bold]")

    console.print(table)


def display_alerts(alerts: list):
    """Display alerts table."""
    if not alerts:
        return

    table = Table(title="Threshold Alerts", show_header=True)
    table.add_column("Severity", style="yellow")
    table.add_column("Scope", style="cyan")
    table.add_column("Item", style="white")
    table.add_column("Value", justify="right", style="red")
    table.add_column("Threshold", justify="right", style="green")

    for alert in alerts[:10]:  # Show top 10
        severity = alert.get("severity", "medium")
        scope = alert.get("scope", "")

        if scope == "github":
            item = f"{alert.get('item', '')} - {alert.get('type', '')}"
        else:  # gcp
            item = f"{alert.get('project', '')} - {alert.get('service', alert.get('type', ''))}"

        value = alert.get("value", 0)
        threshold = alert.get("threshold", 0)

        currency = alert.get("currency")
        if currency:
            value_str = f"{value:,.2f} {currency}"
            threshold_str = f"{threshold:,.2f} {currency}"
        elif "usd" in alert.get("type", ""):
            value_str = f"${value:,.2f}"
            threshold_str = f"${threshold:,.2f}"
        else:
            value_str = str(value)
            threshold_str = str(threshold)

        table.add_row(severity.upper(), scope.upper(), item, value_str, threshold_str)

    console.print(table)

    if len(alerts) > 10:
        console.print(f"[yellow]... and {len(alerts) - 10} more alerts[/yellow]")


def display_final_summary(github_data, gcp_data, threshold_result):
    """Display final summary panel."""
    github_total = github_data.get("total_monthly_cost_usd", 0)
    gcp_total = gcp_data.get("total_net", 0)
    gcp_currency = gcp_data.get("currency", "")

    summary = f"""
[bold cyan]Cost Monitoring Summary[/bold cyan]
━━━━━━━━━━━━━━━━━━━━━━━
• GitHub Costs: ${github_total:,.2f}
• GCP Costs: {gcp_total:,.2f} {gcp_currency}
• Cross-currency grand total: not calculated

• Alerts: {len(threshold_result['alerts'])}
  - High: {threshold_result['high_count']}
  - Medium: {threshold_result['medium_count']}

Status: {"⚠️ THRESHOLDS EXCEEDED" if threshold_result['threshold_exceeded'] else "✅ WITHIN BUDGET"}
"""

    console.print(Panel(summary, title="Final Summary", border_style="bold"))


def create_github_issue_for_alerts(alerts: list, month: str, data: Dict[str, Any]) -> bool:
    """Create GitHub issue for threshold alerts."""
    try:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            logger.warning("GITHUB_TOKEN not set, skipping issue creation")
            return False

        # Get repository from environment
        repo_name = os.environ.get("GITHUB_REPOSITORY")
        if not repo_name:
            logger.warning("GITHUB_REPOSITORY not set, skipping issue creation")
            return False

        # Validate repo_name format (should be owner/repo)
        if not repo_name or "/" not in repo_name or not all(part for part in repo_name.split("/", 1)):
            logger.error("Invalid repository name format for GITHUB_REPOSITORY environment variable")
            return False

        g = Github(token)
        repo = g.get_repo(repo_name)

        # Format issue body
        body = f"""## Cost Threshold Alert - {month}

### Summary
- **Total Alerts**: {len(alerts)}
- **GitHub Costs**: ${data['github'].get('total_monthly_cost_usd', 0):,.2f}
- **GCP Costs**: {data['gcp'].get('total_net', 0):,.2f} {data['gcp'].get('currency', '')}

### Alerts

"""

        # Add alert details
        for alert in alerts[:20]:  # Limit to 20 in issue
            body += f"- {format_alert_message(alert)}\n"

        if len(alerts) > 20:
            body += f"\n*... and {len(alerts) - 20} more alerts*\n"

        body += "\n### Action Required\n"
        body += "Please review the cost reports and take appropriate action.\n\n"
        body += "---\n"
        body += "*This issue was automatically created by the Cost Monitoring tool.*"

        # Create issue
        issue = repo.create_issue(
            title=f"Cost Threshold Exceeded - {month}", body=body, labels=["cost-monitoring", "alert", "automated"]
        )

        logger.info(f"Created GitHub issue #{issue.number}")
        return True

    except Exception as e:
        logger.error(f"Failed to create GitHub issue: {e}")
        return False


if __name__ == "__main__":
    cli()
