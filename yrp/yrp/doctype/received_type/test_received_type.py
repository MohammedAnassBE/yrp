# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestReceivedType(FrappeTestCase):
	def test_only_one_default(self):
		a = frappe.get_doc(
			{"doctype": "Received Type", "received_type_name": "_Test RT A", "is_default": 1}
		).insert()
		b = frappe.get_doc(
			{"doctype": "Received Type", "received_type_name": "_Test RT B", "is_default": 1}
		).insert()
		self.assertEqual(frappe.db.get_value("Received Type", a.name, "is_default"), 0)
		self.assertEqual(frappe.db.get_value("Received Type", b.name, "is_default"), 1)

	def test_cannot_delete_when_set_as_settings_default(self):
		rt = frappe.get_doc(
			{"doctype": "Received Type", "received_type_name": "_Test RT Lock", "is_default": 0}
		).insert()
		settings = frappe.get_single("YRP Stock Settings")
		original = settings.get("default_received_type")
		frappe.db.set_single_value("YRP Stock Settings", "default_received_type", rt.name)
		try:
			with self.assertRaises(frappe.ValidationError):
				rt.delete()
		finally:
			frappe.db.set_single_value("YRP Stock Settings", "default_received_type", original)
			rt.reload()
			rt.delete()

	def test_cannot_delete_when_set_as_settings_rejected(self):
		rt = frappe.get_doc(
			{"doctype": "Received Type", "received_type_name": "_Test RT Reject Lock"}
		).insert()
		original = frappe.db.get_single_value("YRP Stock Settings", "default_rejected_received_type")
		frappe.db.set_single_value("YRP Stock Settings", "default_rejected_received_type", rt.name)
		try:
			with self.assertRaises(frappe.ValidationError):
				rt.delete()
		finally:
			frappe.db.set_single_value("YRP Stock Settings", "default_rejected_received_type", original)
			rt.reload()
			rt.delete()
