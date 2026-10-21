"""Include the observer's offline regressions in the existing repository CI."""
import importlib.util
from pathlib import Path
import unittest


TOOL = Path(__file__).resolve().parents[1]/'tools/gh-cost-934-observer'


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for name in ('test_duration_budget', 'test_existing_acceptance_semantics'):
        spec = importlib.util.spec_from_file_location('financial934_'+name, TOOL/(name+'.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
