"""Owner-adapter orchestration; no launchd implementation or CLI authority.

The service port must verify the exact admitted label, loaded program arguments,
no old PID and an observed completed bootstrap invocation. Unknown is an error.
All service mutations belong to the separately reviewed owner entrypoint.
"""


def cleanup_failure(service, journal, original, stopped_phase):
    """Preserve the original failure and record an unconfirmed service state."""
    try:
        service.stop_when_idle_and_verify_absent()
    except BaseException as cleanup_error:
        try:
            journal({'phase': 'recovery_required_service_state_unknown',
                     'original_error_type': type(original).__name__,
                     'cleanup_error_type': type(cleanup_error).__name__})
        except BaseException as journal_error:
            # Journaling failure is not success; retain the triggering failure.
            raise original from journal_error
        raise original from cleanup_error
    try:
        journal({'phase': stopped_phase})
    except BaseException as journal_error:
        raise original from journal_error
    raise original


def install(*, service, lock, guard, state_read, replace_files, journal):
    """Keep flock held until RunAtLoad finishes without consuming an attempt.

The new observer's contention path returns before writing state or launching a
model. Merely seeing no PID immediately after bootstrap is not sufficient:
verify_bootstrap_completed must prove an actual completed invocation or fail.
"""
    guard()
    state = state_read()
    with lock():
        guard()
        service.verify_old_idle()
        if state_read() != state:
            raise ValueError('state_changed_before_stop')
        journal({'phase': 'stop_intent'})
        try:
            service.stop_and_verify_absent()
            journal({'phase': 'service_stopped'})
            guard()
            service.verify_absent()
            if state_read() != state:
                raise ValueError('state_changed_before_replace')
            receipt = replace_files()
            if receipt.get('state') != 'installed_verified':
                journal({'phase': 'partial_service_stopped', 'transaction': receipt})
                return {'state': 'partial_service_stopped', 'transaction': receipt}
            guard()
            if state_read() != state:
                raise ValueError('state_changed_after_replace')
            journal({'phase': 'bootstrap_intent', 'transaction': receipt})
            service.bootstrap_new()
            service.verify_bootstrap_completed()  # bounded, exact new args, exit 0
            if state_read() != state:
                raise ValueError('state_changed_during_bootstrap')
            guard()
            journal({'phase': 'installed_loaded_verified', 'transaction': receipt})
            return {'state': 'installed_loaded_verified', 'transaction': receipt,
                    'financial_acceptance': 'NOT_PROVEN', 'natural_run_verified': False}
        except BaseException as original:
            cleanup_failure(service, journal, original, 'recovery_required_service_stopped')


def recover(*, service, lock, guard, state_read, restore_files, journal):
    """Explicit owner recovery; never an automatic retry of installation.

The caller must bind the recovery receipt and a fresh recovery authorization.
`restore_files` performs the native all-target rollback preflight before writes.
"""
    guard()
    current_state = state_read()
    with lock():
        guard()
        service.verify_current_idle_or_absent()
        if state_read() != current_state:
            raise ValueError('state_changed_before_recovery_stop')
        journal({'phase': 'recovery_stop_intent'})
        try:
            service.stop_and_verify_absent()
            journal({'phase': 'recovery_service_stopped'})
            guard()
            service.verify_absent()
            if state_read() != current_state:
                raise ValueError('state_changed_before_recovery')
            receipt = restore_files()
            if receipt.get('state') != 'rollback_verified':
                journal({'phase': 'partial_recovery_service_stopped', 'transaction': receipt})
                return {'state': 'partial_recovery_service_stopped', 'transaction': receipt}
            guard()
            if state_read() != current_state:
                raise ValueError('state_changed_during_recovery')
            journal({'phase': 'recovery_bootstrap_intent', 'transaction': receipt})
            service.bootstrap_previous()
            service.verify_previous_bootstrap_completed()
            if state_read() != current_state:
                raise ValueError('state_changed_during_recovery_bootstrap')
            guard()
            result = {'state': 'rollback_loaded_verified', 'transaction': receipt,
                      'financial_acceptance': 'NOT_PROVEN'}
            journal(result)
            return result
        except BaseException as original:
            cleanup_failure(service, journal, original, 'recovery_failed_service_stopped')
