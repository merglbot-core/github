# frozen_string_literal: true

# Project only executable workflow structure; never execute the YAML.
require 'json'
require 'yaml'

def probe(body)
  workflow = YAML.safe_load(body, permitted_classes: [], permitted_symbols: [], aliases: false)
  raise 'invalid_workflow' unless workflow.is_a?(Hash)

  # Psych follows YAML 1.1 and reads the unquoted GitHub key `on` as true.
  on = workflow.key?('on') ? workflow['on'] : workflow[true]
  triggers = case on
             when String then [on]
             when Array then on
             when Hash then on.keys
             else raise 'invalid_workflow'
             end
  raise 'invalid_workflow' if triggers.empty? || triggers.any? { |value| !value.is_a?(String) || value.empty? }

  jobs = workflow['jobs']
  raise 'invalid_workflow' unless jobs.is_a?(Hash) && !jobs.empty?

  step_uses = []
  job_uses = []
  jobs.each_value do |job|
    raise 'invalid_workflow' unless job.is_a?(Hash)

    if job.key?('uses')
      raise 'invalid_workflow' unless job['uses'].is_a?(String)

      job_uses << job['uses']
    end
    steps = job.fetch('steps', [])
    raise 'invalid_workflow' unless steps.is_a?(Array)

    steps.each do |step|
      raise 'invalid_workflow' unless step.is_a?(Hash)
      next unless step.key?('uses')

      raise 'invalid_workflow' unless step['uses'].is_a?(String)

      step_uses << step['uses']
    end
  end
  { triggers: triggers, step_uses: step_uses, job_uses: job_uses }
end

begin
  body = $stdin.read(1_000_001)
  raise 'workflow_too_large' if body.bytesize > 1_000_000

  puts JSON.generate(probe(body))
rescue StandardError
  puts JSON.generate(error: 'workflow_unparseable')
  exit 1
end
