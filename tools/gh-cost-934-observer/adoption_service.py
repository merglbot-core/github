"""Exact-service launchd port for the separately authorized owner entrypoint.

Importing this module performs no subprocess or service operation. The caller
supplies a bounded runner and must enforce owner admission before mutation.
"""
import re
import time

LABEL = 'ai.merglbot.gh-cost-wave3-acceptance'
FIELDS = ('path', 'state', 'program', 'runs', 'pid', 'last exit code')


def parse_print(text, domain):
    lines = text.splitlines()
    if not lines or lines[0] != domain + '/' + LABEL + ' = {':
        raise ValueError('foreign_launchd_service')
    result = {}
    arguments = None
    for line in lines[1:]:
        if line == '\targuments = {':
            if arguments is not None or 'arguments' in result:
                raise ValueError('duplicate_arguments')
            arguments = []
        elif arguments is not None:
            if line == '\t}':
                result['arguments'] = arguments
                arguments = None
            elif line.startswith('\t\t') and not line.startswith('\t\t\t'):
                arguments.append(line[2:])
            else:
                raise ValueError('unexpected_argument_format')
        else:
            match = re.fullmatch(r'\t([^=]+) = (.*)', line)
            if match and match[1] in FIELDS:
                key = match[1]
                if key in result:
                    raise ValueError('duplicate_launchd_field')
                result[key] = match[2]
    if arguments is not None or not all(k in result for k in ('path', 'state', 'program', 'arguments')):
        raise ValueError('incomplete_launchd_snapshot')
    for key in ('runs', 'pid', 'last exit code'):
        if key in result:
            if key == 'last exit code' and result[key] == '(never exited)':
                result[key] = None
                continue
            if not re.fullmatch(r'-?\d+', result[key]):
                raise ValueError('unparseable_launchd_number')
            result[key] = int(result[key])
    return result


class Service:
    def __init__(self, uid, plist, old_args, new_args, *, run,
                 monotonic=time.monotonic, sleep=time.sleep):
        if type(uid) is not int or uid < 1:
            raise ValueError('user_domain_required')
        self.domain = 'gui/' + str(uid)
        self.target = self.domain + '/' + LABEL
        self.plist = str(plist)
        self.old_args, self.new_args = list(old_args), list(new_args)
        self.stop_args = self.old_args
        self.run, self.monotonic, self.sleep = run, monotonic, sleep

    def read(self):
        result = self.run(['/bin/launchctl', 'print', self.target], timeout=5)
        if result.returncode == 0:
            return parse_print(result.stdout, self.domain)
        missing = f'Could not find service "{LABEL}" in domain for user gui: {self.domain.split("/")[1]}'
        if result.returncode == 113 and missing in result.stderr.splitlines() and not result.stdout:
            return None
        raise ValueError('launchd_read_failed')

    def check_args(self, row, arguments):
        if row is None or row['path'] != self.plist or row['arguments'] != arguments or row['program'] != arguments[0]:
            raise ValueError('loaded_caller_mismatch')

    def verify_old_idle(self):
        row = self.read()
        self.check_args(row, self.old_args)
        if row['state'] != 'not running' or 'pid' in row:
            raise ValueError('old_service_not_idle')

    def verify_current_idle_or_absent(self):
        row = self.read()
        if row is None:
            self.stop_args = self.new_args
            return
        self.check_args(row, self.new_args)
        if row['state'] != 'not running' or 'pid' in row:
            raise ValueError('current_service_not_idle')
        self.stop_args = self.new_args

    def verify_absent(self):
        if self.read() is not None:
            raise ValueError('service_still_loaded')

    def stop_and_verify_absent(self):
        row = self.read()
        if row is not None:
            # This fresh snapshot authorizes bootout, not an earlier idle check.
            self.check_args(row, self.stop_args)
            if row['state'] != 'not running' or 'pid' in row:
                raise ValueError('bootout_service_not_idle')
            result = self.run(['/bin/launchctl', 'bootout', self.target], timeout=10)
            if result.returncode != 0:
                raise ValueError('bootout_failed')
        self.verify_absent()

    def bootstrap_new(self):
        self._bootstrap(self.new_args)

    def _bootstrap(self, arguments):
        self.verify_absent()
        self.stop_args = arguments
        result = self.run(['/bin/launchctl', 'bootstrap', self.domain, self.plist], timeout=10)
        if result.returncode != 0:
            raise ValueError('bootstrap_failed')

    def bootstrap_previous(self):
        # The native rollback has restored the same exact plist path.
        self._bootstrap(self.old_args)

    def verify_bootstrap_completed(self):
        self._verify_bootstrap_completed(self.new_args)

    def verify_previous_bootstrap_completed(self):
        self._verify_bootstrap_completed(self.old_args)

    def _verify_bootstrap_completed(self, expected_args):
        deadline = self.monotonic() + 30
        for _ in range(31):
            row = self.read()
            self.check_args(row, expected_args)
            if row.get('runs', 0) > 1:
                raise ValueError('unexpected_multiple_bootstrap_runs')
            if (row['state'] == 'not running' and 'pid' not in row
                    and row.get('runs') == 1 and row.get('last exit code') is not None):
                if row.get('last exit code') != 0:
                    raise ValueError('bootstrap_exit_not_zero')
                return
            if self.monotonic() >= deadline:
                break
            self.sleep(1)
        raise ValueError('bootstrap_completion_unproven')
