"""Work Order costing conserves value when the receipt UOM is a box."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp.doctype.yrp_work_order.yrp_work_order import (
	WorkOrder,
	get_receivable_stock_quantity,
	set_receivable_process_cost,
)

MODULE = "yrp.yrp.doctype.yrp_work_order.yrp_work_order"


class ProcessCostStockUnitsTest(unittest.TestCase):
	def test_box_receipts_do_not_multiply_the_piece_process_cost(self):
		output = frappe._dict(item_variant="PACKED-M", qty=60, uom="Box")
		wo = frappe._dict(
			receivables=[output],
			work_order_calculated_items=[frappe._dict(item_variant="GARMENT-M", quantity=300)],
		)
		pc = frappe._dict(name="PC", depends_on_attribute=1, attribute="Size")
		with (
			patch(MODULE + ".get_variant_attributes", return_value={"Size": "M"}),
			patch(MODULE + ".get_process_cost_rate", return_value=5),
			patch("yrp.stock.utils.get_conversion_factor", return_value={"conversion_factor": 5}),
		):
			self.assertTrue(WorkOrder._apply_calculated_item_process_costs(wo, pc))
		self.assertEqual(output.cost, 5)
		self.assertEqual(output.total_cost, 1500)
		# A 15-box GRN must add 75 pieces * Rs 5, not 75 * Rs 25.
		self.assertEqual(15 * 5 * output.cost, 375)

	def test_direct_stock_rate_keeps_total_value_for_boxes(self):
		output = frappe._dict(item_variant="PACKED-M", qty=12, uom="Box")
		with patch("yrp.stock.utils.get_conversion_factor", return_value={"conversion_factor": 5}):
			set_receivable_process_cost(output, "PC", 5)
			self.assertEqual(get_receivable_stock_quantity(output), 60)
		self.assertEqual(output.total_cost, 300)
