from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.stock.stock_ledger import UpdateEntriesAfter
from yrp.stock.valuation import FIFOValuation, MovingAverageValuation


class TestZeroStockValuationRate(TestCase):
	def test_empty_bucket_retains_rate_and_new_receipt_replaces_it(self):
		for valuation in (FIFOValuation, MovingAverageValuation):
			with self.subTest(method=valuation.__name__):
				engine = UpdateEntriesAfter.__new__(UpdateEntriesAfter)
				engine.stock_value = 0
				engine.valuation_rate = 0
				engine.qty_by_dims = {(): 0}
				valuator = valuation([])
				sle = frappe._dict(name="TEST", posting_date="2026-09-24", posting_time="12:00:00")
				with patch("yrp.stock.stock_ledger.frappe.db.set_value") as write:
					for qty, rate, expected_qty, expected_value, expected_rate in (
						(3, 58.575, 3, 175.725, 58.575),
						(-3, 0, 0, 0, 58.575),
						(2, 70, 2, 140, 70),
						(-2, 0, 0, 0, 70),
					):
						if qty > 0:
							valuator.add_stock(qty, rate)
						else:
							valuator.remove_stock(-qty, 0)
						engine.qty_by_dims[()] = expected_qty
						engine._update_valuation_and_write(sle, valuator, ())
						values = write.call_args.args[2]
						self.assertEqual(values["qty_after_transaction"], expected_qty)
						self.assertAlmostEqual(values["stock_value"], expected_value)
						self.assertAlmostEqual(values["valuation_rate"], expected_rate)
