package main

# GitHub Actions Workflow Policy
# Enforces security and compliance best practices for CI/CD workflows

# Deny: Actions must be SHA-pinned (not tag-based)
deny[msg] {
  input.jobs[_].steps[step_idx].uses
  action := input.jobs[_].steps[step_idx].uses
  not regex.match(`@[a-f0-9]{40}$`, action)
  not startswith(action, "./")
  not startswith(action, "./.github/")
  msg := sprintf("Action must be SHA-pinned (found tag/version): %v", [action])
}

# Deny: Workflows must have concurrency control
deny[msg] {
  not input.concurrency
  input.on.push
  msg = "Workflow with 'push' trigger must have concurrency control to prevent redundant runs"
}

# Deny: Jobs must have timeout-minutes
deny[msg] {
  input.jobs[job_name]
  not input.jobs[job_name]["timeout-minutes"]
  msg := sprintf("Job '%v' must have timeout-minutes to prevent hung workflows", [job_name])
}

# Deny: No hardcoded secrets in workflow files
deny[msg] {
  input.jobs[_].steps[_].env[key]
  regex.match(`(?i)(password|token|key|secret|api_key)`, key)
  value := input.jobs[_].steps[_].env[key]
  not startswith(value, "${{")
  msg := sprintf("Hardcoded secret detected in env var: %v (use ${{secrets.*}} instead)", [key])
}

# Deny: No hardcoded secrets in run commands
deny[msg] {
  input.jobs[_].steps[_].run
  run_cmd := input.jobs[_].steps[_].run
  regex.match(`(?i)(password|token|key|secret|api_key)\s*=\s*["'][^$]`, run_cmd)
  not regex.match(`\$\{\{`, run_cmd)
  msg = "Potential hardcoded secret in run command (use ${{secrets.*}} instead)"
}

# SOC2 Compliance: Production deploys must have required reviewers
deny[msg] {
  input.jobs[job_name].environment.name == "production"
  not input.jobs[job_name].environment.required_reviewers
  not input.jobs[job_name].environment.reviewers
  msg := sprintf("Production deploy in job '%v' must have required reviewers (SOC2 control)", [job_name])
}

# SOC2 Compliance: Workflows must have explicit permissions (least privilege)
deny[msg] {
  input.jobs[_]
  not input.permissions
  not input.jobs[_].permissions
  msg = "Workflow or job must have explicit permissions block (least privilege principle)"
}

# SOC2 Compliance: Detect potential data leakage in logs
deny[msg] {
  input.jobs[_].steps[_].run
  run_cmd := input.jobs[_].steps[_].run
  regex.match(`(?i)(customer|user|email|phone|ssn|credit_card).*echo`, run_cmd)
  msg = "Potential data leakage: Do not echo sensitive data (customer/user/email/phone) in logs"
}

# Security: Prevent use of pull_request_target without proper guards
deny[msg] {
  input.on.pull_request_target
  not input.jobs[_].if
  msg = "pull_request_target trigger must have 'if' condition to prevent code injection from forks"
}

# Security: Require GITHUB_TOKEN permissions to be explicit
deny[msg] {
  input.permissions
  input.permissions == "write-all"
  msg = "Do not use 'write-all' permissions - specify explicit permissions (least privilege)"
}

# Compliance: Container images must be scanned
deny[msg] {
  input.jobs[_].steps[_].run
  run_cmd := input.jobs[_].steps[_].run
  regex.match(`docker (build|push)`, run_cmd)
  not has_trivy_scan
  msg = "Workflows that build Docker images must include Trivy vulnerability scanning"
}

has_trivy_scan {
  input.jobs[_].steps[_].uses
  action := input.jobs[_].steps[_].uses
  contains(action, "trivy-action")
}

# Best Practice: Reusable workflows should use workflow_call
warn[msg] {
  input.name
  contains(input.name, "Reusable")
  not input.on.workflow_call
  msg = "Reusable workflows should use 'workflow_call' trigger"
}

# Best Practice: Deploy workflows should use environments
warn[msg] {
  input.name
  regex.match(`(?i)(deploy|release)`, input.name)
  not input.jobs[_].environment
  msg = "Deploy/Release workflows should use GitHub environments for protection rules"
}

