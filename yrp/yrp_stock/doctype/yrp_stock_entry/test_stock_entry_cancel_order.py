from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.yrp_stock.doctype.yrp_stock_entry.yrp_stock_entry import YRPStockEntry


class TestTransitCancelOrder(TestCase):
	def entry(self):
		return SimpleNamespace(name="STE-SEND", purpose="Send to Warehouse")

	def test_send_entry_with_submitted_receipt_names_the_receipt(self):
		with (
			patch.object(frappe, "get_all", return_value=["STE-RECEIVE"]),
			self.assertRaisesRegex(frappe.ValidationError, "Cancel Receive at Warehouse STE-RECEIVE first"),
		):
			YRPStockEntry.validate_no_submitted_transit_receipt(self.entry())

	def test_send_entry_without_receipt_can_cancel(self):
		with patch.object(frappe, "get_all", return_value=[]) as get_all:
			YRPStockEntry.validate_no_submitted_transit_receipt(self.entry())
		self.assertEqual(
			get_all.call_args.kwargs["filters"],
			{"outgoing_stock_entry": "STE-SEND", "purpose": "Receive at Warehouse", "docstatus": 1},
		)
