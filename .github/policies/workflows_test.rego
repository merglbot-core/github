package main

# Tests for the warn-mode rules (github-security-auditor#317). Run with
# `conftest verify --policy .github/policies`.

has_warning(msgs, fragment) {
  some m
  msgs[m]
  contains(m, fragment)
}

test_unpinned_reusable_workflow_warns {
  msgs := warn with input as {"permissions": {}, "jobs": {"call": {"uses": "org/repo/.github/workflows/x.yml@main"}}}
  has_warning(msgs, "not SHA-pinned")
}

test_pinned_and_local_reusable_workflows_do_not_warn {
  msgs := warn with input as {"permissions": {}, "jobs": {
    "a": {"uses": "org/repo/.github/workflows/x.yml@3d3c42e5aac5ba805825da76410c181273ba90b1"},
    "b": {"uses": "./.github/workflows/local.yml"}}}
  not has_warning(msgs, "not SHA-pinned")
}

test_unpinned_reusable_workflow_is_not_denied {
  denials := deny with input as {"permissions": {}, "jobs": {"call": {"uses": "org/repo/.github/workflows/x.yml@main"}}}
  count(denials) == 0
}

test_literal_secret_in_workflow_env_warns {
  msgs := warn with input as {"permissions": {}, "env": {"API_TOKEN": "literal"}, "jobs": {}}
  has_warning(msgs, "secret-like workflow env API_TOKEN")
}

test_literal_secret_in_job_env_warns {
  msgs := warn with input as {"permissions": {}, "jobs": {"j": {"timeout-minutes": 5, "env": {"DB_PASSWORD": "literal"}, "steps": []}}}
  has_warning(msgs, "secret-like env DB_PASSWORD")
}

test_literal_secret_in_step_with_warns {
  msgs := warn with input as {"permissions": {}, "jobs": {"j": {"timeout-minutes": 5, "steps": [{"uses": "x/y@3d3c42e5aac5ba805825da76410c181273ba90b1", "with": {"token": "literal"}}]}}}
  has_warning(msgs, "secret-like input token")
}

test_expression_secret_does_not_warn {
  msgs := warn with input as {"permissions": {}, "env": {"API_TOKEN": "${{ secrets.X }}"}, "jobs": {"j": {"timeout-minutes": 5, "steps": [{"uses": "x/y@3d3c42e5aac5ba805825da76410c181273ba90b1", "with": {"token": "${{ secrets.Y }}"}}]}}}
  not has_warning(msgs, "secret-like")
}

test_trivy_without_exit_code_warns {
  msgs := warn with input as {"permissions": {}, "jobs": {"b": {"timeout-minutes": 5, "steps": [
    {"run": "docker build -t img ."},
    {"uses": "aquasecurity/trivy-action@3d3c42e5aac5ba805825da76410c181273ba90b1", "with": {"image-ref": "img"}}]}}}
  has_warning(msgs, "without exit-code '1'")
}

test_trivy_before_build_warns {
  msgs := warn with input as {"permissions": {}, "jobs": {"b": {"timeout-minutes": 5, "steps": [
    {"uses": "aquasecurity/trivy-action@3d3c42e5aac5ba805825da76410c181273ba90b1", "with": {"exit-code": "1"}},
    {"run": "docker build -t img ."}]}}}
  has_warning(msgs, "before any image build")
}

test_trivy_after_build_with_exit_code_is_clean {
  msgs := warn with input as {"permissions": {}, "jobs": {"b": {"timeout-minutes": 5, "steps": [
    {"run": "docker build -t img ."},
    {"uses": "aquasecurity/trivy-action@3d3c42e5aac5ba805825da76410c181273ba90b1", "with": {"exit-code": 1, "image-ref": "img"}}]}}}
  not has_warning(msgs, "Trivy")
}
