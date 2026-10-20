import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from yrp.yrp_stock.report.yrp_stock_balance import stock_balance_parity
from yrp.yrp_stock.report.yrp_stock_balance.stock_balance_parity import QUANTITIES, compare_rows, quantities


class TestStockBalanceParityQuantities(unittest.TestCase):
	def test_missing_zero_stock_rows_are_zero_quantities(self):
		expected = {"A | Accepted": dict.fromkeys(QUANTITIES, "0.000")}
		self.assertEqual(quantities([], ["A"], ["Accepted"]), expected)
		self.assertEqual(
			quantities([{"lot": "A", "received_type": "Accepted"}], ["A"], ["Accepted"]), expected
		)

	def test_sums_rows_without_hiding_three_decimal_differences(self):
		rows = [{"lot": "A", "received_type": "Accepted", "bal_qty": value} for value in ("1.001", "2.003")]
		self.assertEqual(quantities(rows, ["A"], ["Accepted"])["A | Accepted"]["bal_qty"], "3.004")

	def test_row_comparison_catches_offsetting_item_differences_and_values(self):
		mrp = [{"item": "A", "bal_qty": 1, "bal_val": 10}, {"item": "B", "bal_qty": 2}]
		yrp = [{"item": "A", "bal_qty": 2, "bal_val": 12}, {"item": "B", "bal_qty": 1}]
		result = compare_rows(mrp, yrp)
		self.assertEqual(result["compared_rows"], 2)
		self.assertEqual(result["matching_at_0_01"], 0)
		self.assertEqual(result["max_difference"], "2")

	def test_row_comparison_ignores_missing_all_zero_rows(self):
		self.assertEqual(compare_rows([{"item": "A"}], [])["matching_at_0_01"], 1)


class TestStockBalanceParityCommand(unittest.TestCase):
	def _compare(self, mrp_rows, yrp_rows):
		with tempfile.TemporaryDirectory() as directory:
			output = Path(directory)
			for side, rows in (("mrp", mrp_rows), ("yrp", yrp_rows)):
				case = {
					"filters": {"lot": ["L"], "received_type": ["Accepted"]},
					"rows": [{"lot": "L", "received_type": "Accepted", **row} for row in rows],
				}
				(output / f"{side}.json").write_text(json.dumps({"case": case}))
			result = subprocess.run(
				[sys.executable, stock_balance_parity.__file__, "--compare-only", "--output", directory],
				capture_output=True,
				text=True,
				check=False,
			)
			self.assertEqual(result.stderr, "")
			return result.returncode, json.loads((output / "comparison.json").read_text())["case"]

	def test_value_only_error_fails_comparison_and_command(self):
		exit_code, case = self._compare(
			[{"item": "A", "bal_qty": 1, "bal_val": 10}],
			[{"item": "A", "bal_qty": 1, "bal_val": 999}],
		)
		self.assertEqual(case["mrp"], case["yrp"])
		self.assertFalse(case["match"])
		self.assertEqual(exit_code, 1)

	def test_offsetting_item_quantities_fail_comparison_and_command(self):
		exit_code, case = self._compare(
			[{"item": "A", "bal_qty": 1}, {"item": "B", "bal_qty": 2}],
			[{"item": "A", "bal_qty": 2}, {"item": "B", "bal_qty": 1}],
		)
		self.assertEqual(case["mrp"], case["yrp"])
		self.assertFalse(case["match"])
		self.assertEqual(exit_code, 1)

	def test_offsetting_item_values_fail_comparison_and_command(self):
		exit_code, case = self._compare(
			[{"item": "A", "bal_val": 10}, {"item": "B", "bal_val": 20}],
			[{"item": "A", "bal_val": 20}, {"item": "B", "bal_val": 10}],
		)
		self.assertFalse(case["match"])
		self.assertEqual(exit_code, 1)

	def test_row_values_within_tolerance_pass_comparison_and_command(self):
		exit_code, case = self._compare(
			[{"item": "A", "bal_qty": 1, "bal_val": "10.00"}],
			[{"item": "A", "bal_qty": 1, "bal_val": "10.01"}],
		)
		self.assertTrue(case["match"])
		self.assertEqual(exit_code, 0)
