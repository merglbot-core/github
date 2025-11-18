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
  is_push_trigger
  not input.concurrency
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
  is_string(value)
  not regex.match(`\$\{\{`, value)
  msg := sprintf("Hardcoded secret detected in env var: %v (use ${{secrets.*}} instead)", [key])
}

# Deny: No hardcoded secrets in run commands
deny[msg] {
  run_cmd := input.jobs[_].steps[_].run
  line := split(run_cmd, "\n")[_]
  regex.match(`(?i)(password|token|key|secret|api_key)\s*=\s*['"]?[^'"\s]+['"]?$`, line)
  not contains(line, "${{")
  msg = "Potential hardcoded secret in run command (use ${{secrets.*}} instead)"
}

# SOC2 Compliance: Workflows must have explicit permissions (least privilege)
deny[msg] {
  job := input.jobs[job_name]
  not input.permissions
  not job.permissions
  msg := sprintf("Job '%v' must have an explicit permissions block, or the workflow must have one (least privilege principle)", [job_name])
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
  is_pr_target_trigger
  job := input.jobs[job_name]
  not job.if
  msg := sprintf("Job '%v' must have an 'if' condition when using 'pull_request_target' to prevent code injection from forks", [job_name])
}

# Security: Require GITHUB_TOKEN permissions to be explicit
deny[msg] {
  input.permissions
  input.permissions == "write-all"
  msg = "Do not use 'write-all' permissions - specify explicit permissions (least privilege)"
}

# Compliance: Container images must be scanned
deny[msg] {
  job := input.jobs[job_name]
  job_builds_docker(job)
  not job_has_trivy_scan(job)
  msg := sprintf("Job '%v' builds a Docker image but does not include a Trivy scan step in the same job", [job_name])
}

job_builds_docker(job) {
  regex.match(`docker (build|push)`, job.steps[_].run)
}

job_has_trivy_scan(job) {
  contains(job.steps[_].uses, "trivy-action")
}

# Best Practice: Reusable workflows should use workflow_call
warn[msg] {
  contains(input.name, "Reusable")
  not is_workflow_call_trigger
  msg = "Reusable workflows should use 'workflow_call' trigger"
}

# Best Practice: Deploy workflows should use environments
warn[msg] {
  regex.match(`(?i)(deploy|release)`, input.name)
  job := input.jobs[job_name]
  not job.environment
  msg := sprintf("Job '%v' in deploy/release workflow should use GitHub environments for protection rules", [job_name])
}

# Helpers

is_push_trigger {
  input.on == "push"
}

is_push_trigger {
  is_array(input.on)
  input.on[_] == "push"
}

is_push_trigger {
  is_object(input.on)
  input.on.push
}

is_pr_target_trigger {
  input.on == "pull_request_target"
}

is_pr_target_trigger {
  is_array(input.on)
  input.on[_] == "pull_request_target"
}

is_pr_target_trigger {
  is_object(input.on)
  input.on.pull_request_target
}

is_workflow_call_trigger {
  input.on == "workflow_call"
}

is_workflow_call_trigger {
  is_array(input.on)
  input.on[_] == "workflow_call"
}

is_workflow_call_trigger {
  is_object(input.on)
  input.on.workflow_call
}
