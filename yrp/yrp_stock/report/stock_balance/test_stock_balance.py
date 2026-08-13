from datetime import date

from frappe.tests.utils import FrappeTestCase

from yrp.yrp_stock.report.stock_balance.stock_balance import get_inward_date_breakdown


class TestStockBalanceInwardDates(FrappeTestCase):
	def test_groups_remaining_fifo_slots_by_date_in_oldest_first_order(self):
		result = get_inward_date_breakdown(
			[
				[5, date(2026, 8, 11)],
				[10, date(2026, 8, 7)],
				[2.5, date(2026, 8, 11)],
			]
		)
		self.assertEqual(
			result,
			[
				{"date": "07-08-2026", "qty": 10.0},
				{"date": "11-08-2026", "qty": 7.5},
			],
		)

	def test_ignores_empty_slots(self):
		self.assertEqual(get_inward_date_breakdown([[0, date(2026, 8, 11)], [3, None]]), [])
