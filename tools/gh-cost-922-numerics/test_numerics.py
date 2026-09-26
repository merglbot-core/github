import copy
import importlib.util
import math
from pathlib import Path
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("numeric_runtime", HERE / "runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class NumericTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(product="actions", sku="Actions Linux", grossAmount=2.0, quantity=333.0,
                        organizationName="org", repositoryName="repo", date="2026-09-27")

    def test_null_or_missing_numeric_fields_are_not_zero(self):
        for field in ("grossAmount", "quantity"):
            for value in (None, "0", False, math.nan, math.inf):
                row = {**self.row, field: value}
                with self.subTest(field=field, value=value):
                    self.assertIsNone(runtime.billing_usage_items({"usageItems": [row]}))
            row = copy.deepcopy(self.row)
            row.pop(field)
            self.assertIsNone(runtime.billing_usage_items({"usageItems": [row]}))

    def test_genuine_zero_and_empty_complete_usage_are_valid(self):
        row = {**self.row, "grossAmount": 0.0, "quantity": 0}
        self.assertEqual(runtime.billing_usage_items({"usageItems": [row]}), [row])
        self.assertEqual(runtime.billing_usage_items({"usageItems": []}), [])
        signed = {**self.row, "grossAmount": -1.0, "quantity": -2.0}
        self.assertEqual(runtime.billing_usage_items({"usageItems": [signed]}), [signed])

    def test_invalid_response_shape_blocks(self):
        for data in (None, {}, {"usageItems": None}, {"usageItems": {}}, {"usageItems": [None]}):
            self.assertIsNone(runtime.billing_usage_items(data))

    def test_missing_dimension_or_invalid_date_blocks(self):
        for field in ("organizationName", "repositoryName", "date", "sku", "product"):
            for value in (None, ""):
                self.assertIsNone(runtime.billing_usage_items({"usageItems": [{**self.row, field: value}]}))
        self.assertIsNone(runtime.billing_usage_items({"usageItems": [{**self.row, "date": "2026-02-30"}]}))

    def test_other_products_and_storage_do_not_enter_compute_validation(self):
        rows = [{"product": "copilot"}, {"product": "actions", "sku": "Actions Storage"}]
        self.assertEqual(runtime.billing_usage_items({"usageItems": rows}), rows)


if __name__ == "__main__":
    unittest.main()
