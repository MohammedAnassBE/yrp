from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.work_order.work_order import (
	_enrich_dimension_metadata,
	_inspection_rework_sources,
)


class TestReworkInspectionAggregation(FrappeTestCase):
	def test_configured_dimension_labels_are_returned_to_the_popup(self):
		rows = [{"dimensions": {"production_group": "GROUP-1"}}]
		with patch(
			"yrp.stock.dimensions.get_stock_dimensions",
			return_value=[{"fieldname": "production_group", "label": "Production Group"}],
		):
			_enrich_dimension_metadata(rows)
		self.assertEqual(
			rows[0]["dimension_labels"], {"production_group": "Production Group"}
		)

	def test_multiple_inspections_aggregate_by_grn_item_and_bucket(self):
		aggregated_rows = [
			frappe._dict(
				source_grn_item="GRN-ITEM-1",
				source_grn="GRN-1",
				table_index=0,
				row_index="0",
				set_combination=None,
				item_variant="ITEM-VARIANT-1",
				warehouse="WH-1",
				converted_qty=5,
				target_received_type="Oil Mark",
				production_group="GROUP-1",
			),
		]

		with (
			patch("frappe.db.sql", return_value=aggregated_rows) as sql,
			patch(
				"yrp.stock.dimensions.get_dimension_fieldnames",
				return_value=["production_group", "received_type"],
			),
			patch(
				"yrp.yrp.doctype.work_order.work_order._eligible_rt_context",
				return_value=("Accepted", "Rejected"),
			),
			patch(
				"yrp.yrp.doctype.work_order.work_order._prior_rework_consumed",
				return_value=0,
			),
			patch(
				"yrp.yrp.doctype.work_order.work_order._item_uom",
				return_value="Kg",
			),
		):
			rows = _inspection_rework_sources({"GRN-1": frappe._dict(name="GRN-1")})

		self.assertIn("SUM(i.qty) AS converted_qty", sql.call_args.args[0])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["available_qty"], 5)
		self.assertEqual(rows[0]["source_grn"], "GRN-1")
		self.assertEqual(rows[0]["source_grn_item"], "GRN-ITEM-1")
		self.assertNotIn("source_inspection_entry_item", rows[0])
