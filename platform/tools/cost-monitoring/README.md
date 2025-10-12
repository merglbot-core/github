# Cost Monitoring Tool

Enterprise-grade cost monitoring solution for GitHub Enterprise and Google Cloud Platform (GCP) with automated reporting and alerting capabilities.

## 🎯 Features

### GitHub Enterprise Monitoring
- **Copilot Usage Tracking**: Monitor seats assigned/purchased and calculate monthly costs
- **Enterprise Cloud Seats**: Track organization members and enterprise-wide usage
- **Multi-Org Support**: Aggregate data across multiple GitHub organizations
- **Member Analytics**: Track member counts per organization

### GCP Cost Management
- **BigQuery Billing Export**: Query real-time billing data from BigQuery
- **Project-Level Breakdown**: Detailed costs per project and service
- **Budget Tracking**: Compare against configured budgets
- **Service-Level Analysis**: Identify top-spending services

### Reporting & Alerts
- **Multi-Format Reports**: CSV, Markdown, and JSON output formats
- **Threshold Alerting**: Automatic alerts when costs exceed configured limits
- **Slack Integration**: Real-time notifications with rich formatting
- **GitHub Issues**: Automatic issue creation for threshold breaches
- **Daily Automation**: Scheduled GitHub Actions workflow

## 📋 Prerequisites

- Python 3.11+
- GitHub Enterprise account with appropriate permissions
- GCP project with:
  - BigQuery billing export configured
  - Workload Identity Federation (WIF) setup
  - Service Account with required permissions
- (Optional) Slack webhook for notifications

## 🚀 Installation

### Local Development

```bash
# Clone the repository
git clone https://github.com/merglbot-core/github.git
cd platform/tools/cost-monitoring

# Install the package in editable mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"
```

### Configuration

1. Copy configuration templates:
```bash
cp config/settings.example.yml config/settings.yml
cp config/thresholds.example.yml config/thresholds.yml
```

2. Edit `config/settings.yml` with your settings:
```yaml
github:
  enterprise: "your-enterprise"
  orgs:
    - "org1"
    - "org2"
  pricing:
    copilot_usd_per_seat: 19
    enterprise_cloud_usd_per_seat: 21

gcp:
  billing_account_id: "XXXX-XXXX-XXXX"
  billing_export:
    project_id: "billing-project"
    dataset: "billing_export"
    table_pattern: "gcp_billing_export_v1_*"
```

3. Configure thresholds in `config/thresholds.yml`:
```yaml
github:
  copilot:
    total_monthly_usd: 500
    seats:
      max: 30

gcp:
  defaults:
    total_monthly_usd: 1000
  projects:
    production-project:
      total_monthly_usd: 2000
```

## 🔑 Authentication

### GitHub
Set the `GITHUB_TOKEN` environment variable:
```bash
export GITHUB_TOKEN="ghp_your_personal_access_token"
```

Required scopes:
- `read:org` - Read organization data
- `read:enterprise` - Read enterprise billing data

### GCP
For local development:
```bash
gcloud auth application-default login
```

For CI/CD, use Workload Identity Federation (recommended) or service account key.

### Slack (Optional)
Set the webhook URL:
```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

## 💻 Usage

### CLI Commands

#### Generate Report
```bash
# Generate report for current month
cost-monitor generate

# Generate report for specific month
cost-monitor generate --month 2025-10

# Dry run (no notifications)
cost-monitor generate --dry-run

# Custom output directory
cost-monitor generate --outdir /path/to/reports
```

#### Validate Configuration
```bash
# Validate threshold configuration
cost-monitor validate-thresholds --config config/thresholds.yml

# Print effective configuration (masks secrets)
cost-monitor print-config
```

### GitHub Actions

The tool includes a GitHub Actions workflow for daily automation:

```yaml
# Manual trigger with parameters
gh workflow run cost-monitoring.yml \
  -f month=2025-10 \
  -f dry_run=true
```

## 📊 Output Formats

### CSV Report
Structured data with columns:
- `source`: github/gcp
- `scope`: enterprise/project/service
- `project`: Project or org name
- `service`: Service name
- `metric`: Metric type
- `value`: Numeric value
- `currency`: USD/count
- `month`: Report month

### Markdown Report
Human-readable report with:
- Executive summary
- GitHub Enterprise breakdown
- GCP project costs
- Top services by cost
- Threshold alerts
- Budget status

### JSON Report
Complete data structure for programmatic processing:
```json
{
  "month": "2025-10",
  "github": {...},
  "gcp": {...},
  "alerts": [...],
  "generated_at": "2025-10-12T..."
}
```

## 🔔 Alerting

### Threshold Configuration

Thresholds support multiple levels:
- **GitHub**: Total costs, seat limits
- **GCP**: Project-level and service-level limits
- **Severity**: High (>150% threshold), Medium (>100% threshold)

### Notification Channels

1. **Slack**: Rich formatted messages with cost breakdowns
2. **GitHub Issues**: Detailed alerts with action items
3. **Email**: Via GitHub Actions notifications

## 🏗️ Architecture

```
cost-monitoring/
├── cost_monitoring/
│   ├── monitor/          # Data collection modules
│   │   ├── github_monitor.py
│   │   └── gcp_monitor.py
│   ├── alerting/         # Threshold evaluation & notifications
│   │   ├── thresholds.py
│   │   └── notifiers.py
│   ├── report/           # Report generation
│   │   └── writers.py
│   └── cli.py           # CLI interface
├── config/              # Configuration files
├── reports/             # Generated reports
└── .github/workflows/   # GitHub Actions
```

## 🔒 Security

### IAM Requirements

#### GCP Service Account Permissions
- `roles/bigquery.jobUser` on billing export project
- `roles/bigquery.dataViewer` on billing dataset
- `roles/billing.viewer` on billing account (for budgets)

#### GitHub Permissions
- Organization member read access
- Enterprise billing read access
- Issue write access (for alerts)

### Best Practices
- Use Workload Identity Federation (WIF) instead of service account keys
- Store secrets in GitHub Secrets or Secret Manager
- Mask sensitive values in logs
- Use minimal required permissions

## 🧪 Testing

```bash
# Run unit tests
pytest tests/

# Run with coverage
pytest --cov=cost_monitoring tests/

# Run linter
ruff check cost_monitoring/

# Format code
black cost_monitoring/
```

## 📈 Metrics & KPIs

The tool tracks:
- **Cost Trends**: Month-over-month changes
- **Budget Adherence**: Actual vs. budgeted costs
- **Service Distribution**: Cost breakdown by service
- **Alert Frequency**: Number of threshold breaches
- **Resource Efficiency**: Credits and discounts applied

## 🚦 Troubleshooting

### Common Issues

1. **"Missing GITHUB_TOKEN"**
   - Ensure the token is set in environment
   - Verify token has required scopes

2. **"Failed to query BigQuery"**
   - Check GCP authentication
   - Verify billing export is configured
   - Ensure service account has permissions

3. **"Slack notification failed"**
   - Verify webhook URL is correct
   - Check network connectivity

4. **No data returned**
   - Verify billing export table exists
   - Check date range in query
   - Ensure projects are in billing account

## 🤝 Contributing

1. Create a feature branch
2. Make changes with tests
3. Run linters and tests
4. Submit PR with description

## 📝 License

MIT License - see LICENSE file for details.

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/merglbot-core/github/issues)
- **Slack**: #platform channel
- **Email**: platform@merglbot.ai

## 🎯 Roadmap

- [ ] Multi-currency support
- [ ] Predictive cost modeling
- [ ] Custom report templates
- [ ] Cost optimization recommendations
- [ ] Integration with cloud cost management tools
- [ ] Historical trend analysis
- [ ] Department-level cost allocation
