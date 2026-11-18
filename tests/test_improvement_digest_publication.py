"""Recover a partly published digest through the normal workflow entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/pr-assistant/publish-improvement-digest.py'
DATE = '2026-11-16'
BRANCH = 'chore/pr-assistant-improvement-digest-' + DATE
DIR = Path('docs/pr-assistant/improvement-digest')
FAKE_GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
kind = args[:2]
assert args[args.index('--repo')+1] == 'example/github'
assert args[args.index('--head')+1] == os.environ['BRANCH']
assert args[args.index('--base')+1] == 'main'
state = Path(os.environ['STATE'])
log = Path(os.environ['TRACE'])
with log.open('a') as f: print(' '.join(kind), file=f)
mode = os.environ['MODE']
if kind == ['pr', 'list']:
    if mode == 'lookup_failed': sys.exit(2)
    print(state.read_text() if state.exists() else '[]')
elif kind == ['pr', 'create']:
    if mode == 'create_denied': sys.exit(1)
    assert not state.exists() or json.loads(state.read_text()) == []
    head = subprocess.check_output(['git','ls-remote','origin','refs/heads/'+os.environ['BRANCH']], text=True).split()[0]
    state.write_text(json.dumps([dict(state='OPEN',number=24,baseRefName='main',headRefName=os.environ['BRANCH'],
        isCrossRepository=False,headRefOid=head if mode!='wrong_readback' else '1'*40)]))
    sys.exit(1 if mode=='ambiguous_create' else 0)
else:
    sys.exit(3)
"""


class DigestPublication(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.origin = self.root / 'origin.git'
        tool = self.root / 'gh'
        tool.write_text(FAKE_GH)
        tool.chmod(0o755)
        self.state = self.root / 'state.json'
        self.trace = self.root / 'trace.txt'
        self.trace.touch()
        self.env = dict(PATH=os.pathsep.join([str(self.root), str(Path(sys.executable).parent),
                                              str(Path(shutil.which('git')).parent), '/usr/bin', '/bin']),
                        GIT_CONFIG_NOSYSTEM='1', HOME=str(self.root), GIT_TERMINAL_PROMPT='0',
                        GIT_ALLOW_PROTOCOL='file', LC_ALL='C', GITHUB_REPOSITORY='example/github',
                        DAYS_BACK='7', DATE=DATE, BRANCH=BRANCH, TRACE=str(self.trace), STATE=str(self.state))
        self.git('init', '--bare', '--initial-branch=main', str(self.origin))
        seed = self.root / 'seed'
        self.git('clone', str(self.origin), str(seed))
        (seed / DIR).mkdir(parents=True)
        (seed / DIR / 'LATEST.md').write_text('previous week')
        (seed / 'unrelated').write_text('preserve')
        self.git('add', '.', cwd=seed)
        self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','seed',cwd=seed)
        self.git('push','origin','main',cwd=seed)
        self.runs = 0

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *args], cwd=cwd or self.root, env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def run_workflow(self, mode='ok', digest='report'):
        self.runs += 1
        runner = self.root / str(self.runs)
        self.git('clone', str(self.origin), str(runner))
        (runner / DIR / (DATE+'.md')).write_text(digest)
        (runner / DIR / 'LATEST.md').write_text(DATE)
        self.trace.write_text('')
        return subprocess.run([sys.executable,str(SCRIPT)],cwd=runner,env=dict(self.env,MODE=mode),
                              text=True,capture_output=True)

    def test_retry_after_denied_create_and_same_content(self):
        self.assertNotEqual(self.run_workflow('create_denied').returncode, 0)
        head = self.git('rev-parse', BRANCH, cwd=self.origin)
        retry = self.run_workflow()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.trace.read_text().count('pr create'), 1)
        self.assertEqual(self.git('rev-parse', BRANCH, cwd=self.origin), head)
        self.assertEqual(json.loads(self.state.read_text())[0]['headRefOid'], head)
        self.assertEqual(self.run_workflow().returncode, 0)
        self.assertNotIn('pr create', self.trace.read_text())
        self.assertEqual(self.git('show', BRANCH+':unrelated',cwd=self.origin), 'preserve')


    def test_retry_preserves_commits_made_after_partial_publication(self):
        self.assertNotEqual(self.run_workflow('create_denied').returncode, 0)
        other = self.root / 'other'
        self.git('clone', '--branch', BRANCH, str(self.origin), str(other))
        (other / 'unrelated').write_text('updated independently')
        self.git('add', 'unrelated', cwd=other)
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-m', 'Preserve concurrent work', cwd=other)
        self.git('push', 'origin', BRANCH, cwd=other)
        before = self.git('rev-parse', BRANCH, cwd=self.origin)
        result = self.run_workflow(digest='updated report')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('rev-parse', BRANCH+'^', cwd=self.origin), before)
        self.assertEqual(self.git('show', BRANCH+':unrelated', cwd=self.origin), 'updated independently')
        self.assertEqual(self.git('show', BRANCH+':'+str(DIR / (DATE+'.md')), cwd=self.origin), 'updated report')

    def test_unknown_create_result_reconciled_once(self):
        self.assertEqual(self.run_workflow('ambiguous_create').returncode, 0)
        self.assertEqual(self.trace.read_text().count('pr create'), 1)

    def test_failed_lookup_stops_before_push(self):
        self.assertNotEqual(self.run_workflow('lookup_failed').returncode, 0)
        self.assertEqual(self.git('ls-remote',str(self.origin),'refs/heads/'+BRANCH), '')
        self.assertNotIn('pr create', self.trace.read_text())

    def test_wrong_readback_and_terminal_pr_fail_closed(self):
        self.assertNotEqual(self.run_workflow('wrong_readback').returncode, 0)
        head = self.git('rev-parse', BRANCH,cwd=self.origin)
        rows = json.loads(self.state.read_text())
        rows[0].update(state='CLOSED',headRefOid=head)
        self.state.write_text(json.dumps(rows))
        self.assertNotEqual(self.run_workflow(digest='changed').returncode, 0)
        self.assertEqual(self.git('rev-parse', BRANCH,cwd=self.origin), head)
        self.assertNotIn('pr create', self.trace.read_text())
