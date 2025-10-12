"""
GCP billing and cost monitoring via BigQuery export.
"""

from typing import List, Dict, Any, Optional
from google.cloud import bigquery
from google.cloud.billing.budgets_v1 import BudgetServiceClient
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def query_month_costs_by_service(
    bq: bigquery.Client, 
    table_fqn: str, 
    project_ids: List[str]
) -> List[Dict[str, Any]]:
    """Query current month costs grouped by project and service."""
    
    # Build query with parameterized project IDs
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("project_ids", "STRING", project_ids)
        ]
    )
    
    sql = f"""
    SELECT 
        project.id AS project_id, 
        service.description AS service, 
        SUM(cost) AS cost_usd,
        SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits_usd
    FROM `{table_fqn}`
    WHERE usage_start_time >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
      AND project.id IN UNNEST(@project_ids)
    GROUP BY 1, 2 
    ORDER BY 1, 3 DESC
    """
    
    try:
        logger.info(f"Querying BigQuery for {len(project_ids)} projects")
        query_job = bq.query(sql, job_config=job_config)
        rows = query_job.result()
        
        # Group by project
        grouped: Dict[str, List[Dict[str, float]]] = {}
        
        for row in rows:
            project_id = row.project_id
            service = row.service
            cost = float(row.cost_usd) if row.cost_usd else 0.0
            credits = float(row.credits_usd) if row.credits_usd else 0.0
            net_cost = cost - credits
            
            if project_id not in grouped:
                grouped[project_id] = []
            
            grouped[project_id].append({
                "service": service,
                "cost_usd": cost,
                "credits_usd": credits,
                "net_cost_usd": net_cost
            })
        
        # Format results
        results = []
        for project_id, services in grouped.items():
            total_cost = sum(s["cost_usd"] for s in services)
            total_credits = sum(s["credits_usd"] for s in services)
            total_net = sum(s["net_cost_usd"] for s in services)
            
            results.append({
                "project_id": project_id,
                "services": services,
                "total_cost_usd": total_cost,
                "total_credits_usd": total_credits,
                "total_net_usd": total_net
            })
        
        logger.info(f"Retrieved costs for {len(results)} projects")
        return results
        
    except Exception as e:
        logger.error(f"Failed to query BigQuery: {str(e)}")
        return []


def list_budgets(billing_account_id: str) -> List[Dict[str, Any]]:
    """List budgets for the billing account."""
    try:
        client = BudgetServiceClient()
        parent = f"billingAccounts/{billing_account_id}"
        
        budgets = []
        for budget in client.list_budgets(parent=parent):
            budget_amount = None
            if hasattr(budget.amount, "specified_amount"):
                if budget.amount.specified_amount:
                    budget_amount = float(
                        budget.amount.specified_amount.units + 
                        budget.amount.specified_amount.nanos / 1e9
                    )
            
            budgets.append({
                "name": budget.name,
                "display_name": budget.display_name,
                "amount_usd": budget_amount,
                "projects": list(budget.budget_filter.projects) if budget.budget_filter.projects else [],
                "services": list(budget.budget_filter.services) if budget.budget_filter.services else []
            })
        
        logger.info(f"Retrieved {len(budgets)} budgets")
        return budgets
        
    except Exception as e:
        logger.error(f"Failed to list budgets: {str(e)}")
        return []


def collect_gcp(
    config: Dict[str, Any],
    month: Optional[str] = None
) -> Dict[str, Any]:
    """Collect all GCP cost data."""
    
    # Extract configuration
    billing_config = config.get("billing_export", {})
    project_id = billing_config.get("project_id")
    dataset = billing_config.get("dataset")
    table_pattern = billing_config.get("table_pattern", "gcp_billing_export_v1_*")
    billing_account_id = config.get("billing_account_id")
    
    # Get all project IDs
    all_projects = []
    projects_config = config.get("projects", {})
    
    for category, category_data in projects_config.items():
        if isinstance(category_data, list):
            all_projects.extend(category_data)
        elif isinstance(category_data, dict):
            for subcategory, project_list in category_data.items():
                if isinstance(project_list, list):
                    all_projects.extend(project_list)
    
    if not all_projects:
        logger.warning("No projects configured for GCP monitoring")
        return {}
    
    # Initialize BigQuery client
    try:
        bq_client = bigquery.Client(project=project_id)
        
        # Determine current month or use specified
        if month:
            current_month = month
        else:
            current_month = datetime.now().strftime("%Y-%m")
        
        # Build table reference (use wildcard pattern)
        table_fqn = f"{project_id}.{dataset}.{table_pattern}"
        
        # Query costs
        project_costs = query_month_costs_by_service(bq_client, table_fqn, all_projects)
        
        # Get budgets if billing account is configured
        budgets = []
        if billing_account_id and billing_account_id != "XXXX-XXXX-XXXX":
            budgets = list_budgets(billing_account_id)
        
        # Calculate totals
        total_cost = sum(p["total_cost_usd"] for p in project_costs)
        total_credits = sum(p["total_credits_usd"] for p in project_costs)
        total_net = sum(p["total_net_usd"] for p in project_costs)
        
        # Group by category for reporting
        categorized_costs = {}
        for category, category_data in projects_config.items():
            categorized_costs[category] = {}
            
            if isinstance(category_data, list):
                # Direct list of projects
                category_projects = category_data
                category_project_costs = [p for p in project_costs if p["project_id"] in category_projects]
                categorized_costs[category] = {
                    "projects": category_project_costs,
                    "total_cost_usd": sum(p["total_cost_usd"] for p in category_project_costs),
                    "total_net_usd": sum(p["total_net_usd"] for p in category_project_costs)
                }
            elif isinstance(category_data, dict):
                # Nested structure (e.g., clients)
                for subcategory, project_list in category_data.items():
                    if isinstance(project_list, list):
                        sub_project_costs = [p for p in project_costs if p["project_id"] in project_list]
                        categorized_costs[category][subcategory] = {
                            "projects": sub_project_costs,
                            "total_cost_usd": sum(p["total_cost_usd"] for p in sub_project_costs),
                            "total_net_usd": sum(p["total_net_usd"] for p in sub_project_costs)
                        }
        
        return {
            "month": current_month,
            "billing_account": billing_account_id,
            "projects_monitored": len(all_projects),
            "project_costs": project_costs,
            "categorized_costs": categorized_costs,
            "budgets": budgets,
            "total_cost_usd": total_cost,
            "total_credits_usd": total_credits,
            "total_net_usd": total_net
        }
        
    except Exception as e:
        logger.error(f"Failed to collect GCP data: {str(e)}")
        return {
            "error": str(e),
            "projects_monitored": 0,
            "project_costs": [],
            "total_cost_usd": 0
        }


def __init__():
    """Module initialization."""
    pass