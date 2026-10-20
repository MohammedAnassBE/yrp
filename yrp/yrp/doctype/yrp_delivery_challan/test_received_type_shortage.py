from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from yrp.yrp.doctype.yrp_delivery_challan import yrp_delivery_challan as dc

BUCKET = {"item": "TB-OLIVE-S", "qty": 2, "dims": {"lot": "LOT-1", "received_type": "Accepted"}}


class TestReceivedTypeShortageHint(UnitTestCase):
	def test_other_received_types_are_read_from_bins_of_the_same_dimensions(self):
		bins = [frappe._dict(received_type="Printing Mistake", actual_qty=125)]
		with patch.object(dc.frappe, "get_all", return_value=bins) as get_all:
			other = dc.get_other_received_type_stock("TB-OLIVE-S", "S-0170", BUCKET["dims"])
		self.assertEqual(other, {"Printing Mistake": 125})
		self.assertEqual(
			get_all.call_args.kwargs["filters"],
			{
				"item_code": "TB-OLIVE-S",
				"warehouse": "S-0170",
				"actual_qty": [">", 0],
				"received_type": ["!=", "Accepted"],
				"lot": "LOT-1",
			},
		)

	def test_hint_names_the_held_received_type_and_the_rework_route(self):
		with patch.object(dc, "get_other_received_type_stock", return_value={"Printing Mistake": 125}):
			hint = dc._received_type_hint(BUCKET, "S-0170", is_rework=False)
			rework_hint = dc._received_type_hint(BUCKET, "S-0170", is_rework=True)
		self.assertIn("held as Printing Mistake 125.0", hint)
		self.assertIn("Rework Work Order", hint)
		self.assertNotIn("Rework Work Order", rework_hint)

	def test_no_hint_without_other_received_type_stock(self):
		with patch.object(dc, "get_other_received_type_stock", return_value={}):
			self.assertEqual(dc._received_type_hint(BUCKET, "S-0170", is_rework=False), "")


class TestShortageDimensionText(UnitTestCase):
	def test_shortage_names_the_lot_and_received_type(self):
		dimensions = [
			{"fieldname": "lot", "label": "Lot"},
			{"fieldname": "received_type", "label": "Received Type"},
		]
		with patch("yrp.stock.dimensions.get_stock_dimensions", return_value=dimensions):
			text = dc.get_dimension_text(BUCKET["dims"])
			empty = dc.get_dimension_text({"lot": None, "received_type": ""})
		self.assertEqual(text, " (Lot LOT-1, Received Type Accepted)")
		self.assertEqual(empty, "")
