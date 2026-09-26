import csv
import unittest
from pathlib import Path


INVENTORY = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "guardrails"
    / "forecast_pipelines.csv"
)

COUNTRIES = ("cz", "sk", "hu", "pl", "ro")
CANONICAL_PROJECT = "merglbot-proteinaco-main"


def proteinaco_rows() -> dict[str, dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as handle:
        lines = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
    rows = csv.DictReader(lines)
    return {row["country"]: row for row in rows if row["tenant"] == "proteinaco"}


class ForecastD1ProteinacoCanonicalTargetsTest(unittest.TestCase):
    """business-analytics#511: the Proteinaco guardrail reads canonical/ABRA twins only.

    The legacy proteinaco-main 13_/14_ tables are being paused; the guardrail
    must not keep them alive as a reader.
    """

    def test_every_country_has_exactly_one_row(self):
        self.assertEqual(sorted(proteinaco_rows()), sorted(COUNTRIES))

    def test_rows_point_at_canonical_tables_in_the_canonical_job_project(self):
        for cc, row in proteinaco_rows().items():
            with self.subTest(country=cc):
                self.assertEqual(row["project_id"], CANONICAL_PROJECT)
                self.assertEqual(
                    row["bq_table_13"],
                    f"{CANONICAL_PROJECT}.visualisation_final_{cc}_canonical."
                    f"13_tran_db_ga4_join_all_channel_cost_plan_final_with_forecasts_proteinaco_{cc}",
                )
                self.assertEqual(
                    row["bq_table_14"],
                    f"{CANONICAL_PROJECT}.analytics."
                    "mkt_abra_14_join_all_channel_cost_final_proteinaco_all_countries",
                )

    def test_no_proteinaco_row_references_the_legacy_project(self):
        for cc, row in proteinaco_rows().items():
            with self.subTest(country=cc):
                for column, value in row.items():
                    self.assertNotIn("proteinaco-main.", value.replace(CANONICAL_PROJECT + ".", ""), column)
                    self.assertFalse(value.startswith("proteinaco-main"), column)


if __name__ == "__main__":
    unittest.main()
