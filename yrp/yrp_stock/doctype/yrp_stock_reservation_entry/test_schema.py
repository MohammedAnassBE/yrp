import json

import frappe
from frappe.tests import IntegrationTestCase


class TestStockReservationEntrySchema(IntegrationTestCase):
	def test_voucher_reference_fields_are_consumer_extensible(self):
		path = frappe.get_app_path(
			"yrp",
			"yrp_stock",
			"doctype",
			"yrp_stock_reservation_entry",
			"yrp_stock_reservation_entry.json",
		)
		with open(path) as source:
			meta = json.load(source)

		fields = {row.get("fieldname"): row for row in meta["fields"]}
		self.assertEqual(fields["voucher_type"]["fieldtype"], "Select")
		self.assertNotIn("options", fields["voucher_type"])
		self.assertEqual(fields["voucher_no"]["fieldtype"], "Data")
		self.assertNotIn("options", fields["voucher_no"])
