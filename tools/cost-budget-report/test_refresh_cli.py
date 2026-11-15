from contextlib import redirect_stdout, redirect_stderr
import io
import json
import unittest
from unittest.mock import Mock

from refresh_cli import main, ENDPOINT


class CliTests(unittest.TestCase):
    def test_disabled_never_executes(self):
        execute=Mock()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([],execute=execute),0)
        execute.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['state'],'disabled')

    def test_enabled_requires_explicit_threshold(self):
        execute=Mock()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--enabled','--root','/tmp/example','--journal','/tmp/state/journal'],execute=execute)
        execute.assert_not_called()

    def test_enabled_forwards_fixed_endpoint_once(self):
        execute=Mock(return_value={'state':'published_verified','request_count':3})
        with redirect_stdout(io.StringIO()):
            rc=main(['--enabled','--root','/tmp/example','--journal','/tmp/state/journal',
                     '--refresh-after-hours','12'],execute=execute)
        self.assertEqual(rc,0)
        self.assertEqual(execute.call_count,1)
        self.assertEqual(execute.call_args.kwargs['endpoint'],ENDPOINT)

    def test_failure_sanitized_without_retry(self):
        execute=Mock(side_effect=ValueError('private fixture text'))
        with redirect_stdout(io.StringIO()) as output:
            rc=main(['--enabled','--root','/tmp/example','--journal','/tmp/state/journal',
                     '--refresh-after-hours','12'],execute=execute)
        self.assertEqual(rc,2)
        self.assertEqual(execute.call_count,1)
        self.assertNotIn('private fixture text',output.getvalue())


if __name__ == '__main__': unittest.main()
