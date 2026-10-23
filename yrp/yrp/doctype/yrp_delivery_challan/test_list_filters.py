# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestTransferListFilters(FrappeTestCase):
	def test_transfer_complete_is_a_standard_filter_on_dc_and_grn(self):
		for doctype in ("YRP Delivery Challan", "YRP Goods Received Note"):
			field = frappe.get_meta(doctype, cached=False).get_field("transfer_complete")
			self.assertEqual(field.fieldtype, "Check", doctype)
			self.assertEqual(field.in_standard_filter, 1, doctype)
