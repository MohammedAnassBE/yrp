from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_work_order.yrp_work_order import WorkOrder


class TestWorkOrderProcessCost(FrappeTestCase):
	def test_finished_item_attribute_rate_is_spread_over_receivable_units(self):
		work_order = _work_order(
			calculated_items=[("GARMENT-BLACK", 10)],
			receivables=[("PANEL-FRONT", 10), ("PANEL-SLEEVE", 20)],
		)
		process_cost = _process_cost("Colour", [("Black", 6)])
		attributes = {
			"GARMENT-BLACK": {"Colour": "Black"},
			"PANEL-FRONT": {"Colour": "Black", "Panel": "Front"},
			"PANEL-SLEEVE": {"Colour": "Black", "Panel": "Sleeve"},
		}

		with patch(
			"yrp.yrp.doctype.yrp_work_order.yrp_work_order.get_variant_attributes",
			side_effect=lambda item_variant: attributes[item_variant],
		):
			work_order.apply_receivable_process_costs(process_cost)

		self.assertEqual([row.cost for row in work_order.receivables], [2, 2])
		self.assertEqual([row.total_cost for row in work_order.receivables], [20, 40])
		self.assertEqual(sum(row.total_cost for row in work_order.receivables), 60)

	def test_output_only_attribute_keeps_direct_receivable_rate(self):
		work_order = _work_order(
			calculated_items=[("GARMENT-BLACK", 10)],
			receivables=[("PANEL-FRONT", 10), ("PANEL-SLEEVE", 20)],
		)
		process_cost = _process_cost("Panel", [("Front", 1), ("Sleeve", 0.5)])
		attributes = {
			"GARMENT-BLACK": {"Colour": "Black"},
			"PANEL-FRONT": {"Colour": "Black", "Panel": "Front"},
			"PANEL-SLEEVE": {"Colour": "Black", "Panel": "Sleeve"},
		}

		with patch(
			"yrp.yrp.doctype.yrp_work_order.yrp_work_order.get_variant_attributes",
			side_effect=lambda item_variant: attributes[item_variant],
		):
			work_order.apply_receivable_process_costs(process_cost)

		self.assertEqual([row.cost for row in work_order.receivables], [1, 0.5])
		self.assertEqual([row.total_cost for row in work_order.receivables], [10, 10])


def _work_order(calculated_items, receivables):
	work_order = WorkOrder({"doctype": 'YRP Work Order'})
	for item_variant, quantity in calculated_items:
		work_order.append(
			"work_order_calculated_items",
			{"item_variant": item_variant, "quantity": quantity, "set_combination": "{}"},
		)
	for item_variant, quantity in receivables:
		work_order.append(
			"receivables",
			{"item_variant": item_variant, "qty": quantity, "set_combination": "{}"},
		)
	return work_order


def _process_cost(attribute, values):
	return frappe._dict(
		name="TEST-PROCESS-COST",
		depends_on_attribute=1,
		attribute=attribute,
		process_cost_values=[
			frappe._dict(attribute_value=value, min_order_qty=0, price=price) for value, price in values
		],
	)
