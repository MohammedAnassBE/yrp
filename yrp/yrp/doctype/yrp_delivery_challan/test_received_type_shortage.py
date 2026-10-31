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


class TestDeliveryChallanShortageList(UnitTestCase):
	def _challan(self):
		rows = [
			{"item_variant": "FAB-NAVY", "qty": 16.95, "lot": "F0426-86", "received_type": "Accepted"},
			{"item_variant": "FAB-RED", "qty": 5, "lot": "F0426-86", "received_type": "Accepted"},
			{"item_variant": "FAB-BLUE", "qty": 3, "lot": "F0426-86", "received_type": "Accepted"},
		]
		return frappe.get_doc(
			{
				"doctype": "YRP Delivery Challan",
				"from_warehouse": "S-0163",
				"work_order": "WO-1",
				"posting_date": "2026-10-31",
				"posting_time": "10:00:00",
				"items": rows,
			}
		)

	def _validate(self, balances):
		balance = lambda item, *args, **kwargs: balances[item]  # noqa: E731
		with (
			patch.object(dc, "_is_rework_work_order", return_value=False),
			patch.object(dc, "get_other_received_type_stock", return_value={}),
			patch("yrp.stock.utils.get_stock_balance", side_effect=balance),
		):
			self._challan().validate_stock_available()

	def test_float_drift_in_the_balance_is_not_a_shortage(self):
		self._validate({"FAB-NAVY": 16.949999, "FAB-RED": 5, "FAB-BLUE": 3})

	def test_every_short_item_is_listed_together(self):
		with self.assertRaises(frappe.ValidationError) as raised:
			self._validate({"FAB-NAVY": 16.9, "FAB-RED": 2, "FAB-BLUE": 3})
		message = str(raised.exception)
		self.assertIn("Insufficient stock for 2 items", message)
		self.assertIn("FAB-NAVY", message)
		self.assertIn("balance 16.9, requested 16.95, short 0.05", message)
		self.assertIn("FAB-RED", message)
		self.assertIn("balance 2.0, requested 5.0, short 3.0", message)
		self.assertNotIn("FAB-BLUE", message)
