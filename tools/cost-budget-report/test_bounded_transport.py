from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bounded_transport as transport


class TransportTests(unittest.TestCase):
    def run_command(self, source, timeout=3):
        with tempfile.TemporaryDirectory() as directory:
            return transport.read_json_command([sys.executable, '-I', '-c', source],
                                               timeout=timeout, cwd=Path(directory))

    def test_valid_json(self):
        self.assertEqual(self.run_command('print(\'{"ok":true}\')'), {'ok':True})

    def test_nonzero_and_invalid_json(self):
        for source in ('raise SystemExit(2)', 'print("not JSON")', 'print("[]")'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.run_command(source)

    def test_output_limit(self):
        with patch.object(transport, 'MAX_OUTPUT', 100), self.assertRaises(ValueError):
            self.run_command('print("x"*101)')

    def test_timeout(self):
        with self.assertRaises(TimeoutError):
            self.run_command('import time; time.sleep(10)', timeout=0.1)

    def test_foreign_url_refused_before_spawn(self):
        with patch.object(transport.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError): transport.gh_get('https://foreign.invalid', timeout=1)
            spawn.assert_not_called()

    def test_gh_is_get_only_and_no_retry(self):
        with patch.object(transport, 'read_json_command', return_value={}) as run:
            transport.gh_get('https://api.github.com/rate_limit', timeout=1)
        self.assertEqual(run.call_count, 1)
        argv=run.call_args.args[0]
        self.assertEqual(argv[argv.index('--method')+1], 'GET')
        self.assertEqual(argv[0:2], ['/bin/bash', transport.WRAPPER])


if __name__ == '__main__': unittest.main()
