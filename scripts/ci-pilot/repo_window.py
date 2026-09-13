"""Owner-scoped two-repository, five-hour window on the existing supervisor."""
import base64
import datetime as dt
import json
import os
from github_client import CHECKS, WORKFLOWS, GUARD, Gap, digest, instant

REPOS = ('merglbot-core/infra', 'merglbot-extractors/denatura-forecast-exporter')
SWITCH = 'CI_DELAY_ENABLED'


def switches(gh, repo):
    return {r['name']: r['value'] for r in gh.pages(
        f'repos/{repo}/actions/variables', 'variables', 30) if r['name'] == SWITCH}


def write_switch(gh, repo, enabled=False):
    if repo not in REPOS:
        raise Gap('window_repository_out_of_scope')
    args = ['/bin/bash', str(GUARD), 'gh', 'variable',
            'set' if enabled else 'delete', SWITCH, '--repo', repo]
    if enabled:
        args += ['--body', 'true']
    gh.command(args, raw=True, env={**os.environ, 'CODEX_GUARD_ALLOW_GITHUB_MUTATION': '1'})


def disable(gh, repos=REPOS, apply=False):
    verified = True
    for repo in repos:
        try:
            present = bool(switches(gh, repo))
        except Exception:
            present = True
        if present and apply:
            try:
                write_switch(gh, repo)
            except Exception:
                pass
        try:
            verified = not switches(gh, repo) and verified
        except Exception:
            verified = False
    return verified


def preflight(gh, repo, expected):
    prefix = f'repos/{repo}'
    main = gh.api(f'{prefix}/git/ref/heads/main')['object']['sha']
    content = gh.api(f'{prefix}/contents/{WORKFLOWS[repo]}?ref={main}')
    workflow = base64.b64decode(content['content']).decode()
    protection = gh.api(f'{prefix}/branches/main/protection')
    rules = gh.pages(f'{prefix}/rules/branches/main')
    if digest(workflow) != expected['workflow_sha256'] or digest(json.dumps(
            {'protection': protection, 'rules': rules}, sort_keys=True)) != expected['protection_sha256']:
        raise Gap('window_contract_changed')
    required = protection['required_status_checks']
    if not required['strict'] or not any(c['context'] == 'Merglbot PR Assistant v6'
            for c in required['checks']) or any(not any(c['context'] == name and c['app_id'] == 15368
            for c in required['checks']) for name in CHECKS[repo]):
        raise Gap('window_required_checks_missing')
    for env, minutes in (('ci-pr-delay', 10), ('ci-immediate', 0)):
        path = f'{prefix}/environments/{env}'
        config = gh.api(path)
        timers = [r['wait_timer'] for r in config['protection_rules'] if r['type'] == 'wait_timer']
        if timers not in ([minutes], [] if minutes == 0 else [minutes]):
            raise Gap('window_timer_changed')
        if (any(r['type'] not in ('branch_policy', 'wait_timer') for r in config['protection_rules'])
                or gh.pages(path + '/secrets', 'secrets')
                or gh.pages(path + '/variables', 'variables', 30)
                or gh.api(path + '/deployment_protection_rules')['total_count']):
            raise Gap('window_environment_not_empty')
        policies = gh.pages(path + '/deployment-branch-policies', 'branch_policies')
        names = {(p['name'], p['type']) for p in policies}
        expected_names = {('refs/pull/*/merge', 'branch')}
        if env == 'ci-immediate' and repo == REPOS[1]:
            expected_names.add(('main', 'branch'))
        if (names != expected_names or config['deployment_branch_policy'] !=
                {'protected_branches': False, 'custom_branch_policies': True}):
            raise Gap('window_branch_policy_changed')
    if gh.api(f'{prefix}/git/ref/heads/main')['object']['sha'] != main:
        raise Gap('window_main_moved')


def observe(gh, window, persist):
    """Retain redacted per-attempt job/timer observations; never infer V6 acceptance."""
    unfinished = gaps = 0
    for repo in REPOS:
        if repo not in window.get('activation_intents', {}):
            continue
        try:
            since = window['activation_intents'][repo].replace('+00:00', 'Z')
            runs = gh.pages(f'repos/{repo}/actions/workflows/{WORKFLOWS[repo].rsplit("/", 1)[1]}/runs'
                            f'?event=pull_request&created=%3E%3D{since}', 'workflow_runs')
            for run in runs:
                stopped = window.get('stopped_at', {}).get(repo)
                if stopped and instant(run['created_at']) >= instant(stopped):
                    continue
                for attempt in range(1, run['run_attempt'] + 1):
                    key = f'{repo}/{run["id"]}/{attempt}'
                    old = window.setdefault('observations', {}).get(key)
                    if old and old['status'] == 'completed':
                        continue
                    prefix = f'repos/{repo}/actions/runs/{run["id"]}'
                    current = gh.api(f'{prefix}/attempts/{attempt}')
                    jobs = gh.pages(f'{prefix}/attempts/{attempt}/jobs', 'jobs')
                    pending = gh.api(f'{prefix}/pending_deployments') if attempt == run['run_attempt'] else []
                    observation = {k: current.get(k) for k in
                                   ('id', 'head_sha', 'created_at', 'run_started_at', 'status', 'conclusion')}
                    observation['prs'] = [p['number'] for p in current['pull_requests']]
                    observation['jobs'] = [{**{k: j.get(k) for k in
                        ('id', 'name', 'runner_id', 'started_at', 'completed_at', 'conclusion')},
                        'steps_count': len(j['steps'])} for j in jobs]
                    timers = [{'name': p['environment']['name'], 'minutes': p.get('wait_timer'),
                               'started_at': p.get('wait_timer_started_at')} for p in pending]
                    observation['timers'] = (old or {}).get('timers', [])
                    for timer in timers:
                        if timer not in observation['timers']:
                            observation['timers'].append(timer)
                    window['observations'][key] = observation
                    persist()
                    unfinished += current['status'] != 'completed'
        except Exception:
            gaps += 1
    return {'unfinished_runs': unfinished, 'data_gaps': gaps}


def tick(gh, state, now, apply=False, receipt=None, hold=False, persist=lambda s: None,
         clock=None, hold_check=lambda: False, supervisor_ready=lambda: False):
    from controller import cleanup
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    window = state.get('repo_window')
    def save():
        persist(state)
    try:
        if receipt and receipt.get('prepare_window'):
            if window is not None or state.get('receipt') or state.get('experiment', {}).get('active'):
                raise Gap('window_already_prepared_or_old_selection_active')
            contracts = receipt['contracts']
            if set(contracts) != set(REPOS):
                raise Gap('window_contract_repositories')
            for contract in contracts.values():
                if set(contract) != {'workflow_sha256', 'protection_sha256'} or any(
                    not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
                    for v in contract.values()):
                    raise Gap('window_invalid_contract')
            if not cleanup(gh, False) or not disable(gh):
                raise Gap('window_existing_selectors')
            if not apply:
                return {'action': 'window_prepare_available', 'status': 'readonly'}
            window = {'phase': 'prepared', 'contracts': contracts, 'disabled': [], 'activation_intents': {}}
            state['repo_window'] = window
            save()
        if not isinstance(window, dict) or window.get('phase') not in (
                'prepared', 'activating', 'active', 'stopping', 'stopped'):
            raise Gap('window_invalid_state')
        if window.get('started_at'):
            start, end = instant(window['started_at']), instant(window['expires_at'])
            if end - start != dt.timedelta(hours=5) or start > now:
                raise Gap('window_invalid_deadline')
        if receipt and receipt.get('start_window'):
            if window['phase'] != 'prepared' or hold or hold_check() or not supervisor_ready():
                raise Gap('window_start_not_ready')
            for repo in REPOS:
                preflight(gh, repo, window['contracts'][repo])
            if not cleanup(gh, False) or not disable(gh):
                raise Gap('window_existing_selectors')
            if not apply:
                return {'action': 'window_start_available', 'status': 'readonly'}
            start = clock()
            window.update(phase='activating', started_at=start.isoformat(),
                          expires_at=(start + dt.timedelta(hours=5)).isoformat())
            save()
            for repo in REPOS:
                if hold_check() or clock() >= instant(window['expires_at']):
                    raise Gap('window_start_interrupted')
                window['activation_intents'][repo] = clock().isoformat()
                save()
                write_switch(gh, repo, True)
                if switches(gh, repo) != {SWITCH: 'true'}:
                    raise Gap('window_activation_readback_failed')
            window['phase'] = 'active'
            save()
        if window['phase'] == 'activating':
            raise Gap('window_incomplete_activation')
        targets = (receipt or {}).get('stop_repos', [])
        if any(repo not in REPOS for repo in targets):
            raise Gap('window_stop_out_of_scope')
        if hold or hold_check() or (window.get('expires_at') and clock() >= instant(window['expires_at'])):
            targets = list(REPOS)
        if targets and apply:
            window['disabled'] = sorted(set(window['disabled']) | set(targets))
            save()  # A restart must retry removals, never enable again.
        if window['phase'] in ('stopping', 'stopped'):
            window['disabled'] = list(REPOS)
        if window['disabled']:
            if not disable(gh, window['disabled'], apply):
                raise Gap('window_cleanup_readback_failed')
            if apply:
                for repo in window['disabled']:
                    window.setdefault('stopped_at', {}).setdefault(repo, clock().isoformat())
                save()
        if window['phase'] == 'prepared':
            if not disable(gh):
                raise Gap('window_untracked_switch')
            return {'action': 'window_prepared', 'status': 'inactive',
                    'history': {'unfinished_runs': 0, 'data_gaps': 0}}
        if len(window['disabled']) == len(REPOS):
            window['phase'] = 'stopping'
        for repo in REPOS:
            if repo not in window['disabled']:
                if switches(gh, repo) != {SWITCH: 'true'}:
                    raise Gap('window_switch_changed')
                preflight(gh, repo, window['contracts'][repo])
        if not cleanup(gh, False):
            raise Gap('window_legacy_selector_conflict')
        # Recheck expiry after API work, before any observation workload.
        if window.get('expires_at') and clock() >= instant(window['expires_at']):
            window['disabled'] = list(REPOS)
            window['phase'] = 'stopping'
            save()
            if not disable(gh, apply=apply):
                raise Gap('window_cleanup_readback_failed')
            if apply:
                for repo in REPOS:
                    window.setdefault('stopped_at', {}).setdefault(repo, clock().isoformat())
                save()
        history = observe(gh, window, save)
        if window['phase'] == 'stopping' and not any(history.values()):
            window['phase'] = 'stopped'
        save()
        return {'action': 'repo_window_complete' if window['phase'] == 'stopped' else 'observe_window',
                'status': 'inactive' if window['phase'] == 'stopped' else 'active', 'history': history,
                'expires_at': window.get('expires_at'), 'disabled': window['disabled']}
    except Exception as error:
        if isinstance(window, dict) and apply:
            window.update(phase='stopping', disabled=list(REPOS))
            save()
        clean = disable(gh, apply=apply)
        return {'action': 'window_cleanup_required', 'status': 'unverified', 'cleanup_verified': clean,
                'reason': str(error) if isinstance(error, Gap) else 'window_data_gap'}
