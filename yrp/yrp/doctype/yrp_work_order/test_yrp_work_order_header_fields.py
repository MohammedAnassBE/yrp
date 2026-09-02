import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestWorkOrderHeaderFields(FrappeTestCase):
	def test_address_links_are_filtered_by_their_selected_supplier(self):
		client_script = Path(
			frappe.get_app_path(
				"yrp", "yrp", "doctype", "yrp_work_order", "yrp_work_order.js"
			)
		).read_text()
		self.assertIn(
			'frm.set_query("supplier_address", () =>\n\t\t\twork_order_party_address_query(frm, "supplier")',
			client_script,
		)
		self.assertIn(
			'frm.set_query("delivery_address", () =>\n\t\t\twork_order_party_address_query(frm, "delivery_location")',
			client_script,
		)
		self.assertIn(
			'query: "frappe.contacts.doctype.address.address.address_query"',
			client_script,
		)
		self.assertIn('link_doctype: "YRP Supplier"', client_script)

	def test_generic_work_order_source_flags_are_owned_by_base_yrp(self):
		work_order_meta = frappe.get_meta('YRP Work Order', cached=False)
		expected_date = work_order_meta.get_field("expected_delivery_date")
		self.assertEqual(expected_date.reqd, 1)
		self.assertEqual(expected_date.read_only, 0)

		expected = {
			"is_internal_unit": ("supplier.is_company_location", 0),
			"is_manual_entry": ("process_name.is_manual_entry_in_grn", 0),
		}
		for fieldname, (fetch_from, read_only) in expected.items():
			field = work_order_meta.get_field(fieldname)
			self.assertEqual(field.fetch_from, fetch_from)
			self.assertEqual(field.read_only, read_only)

		terms = work_order_meta.get_field("terms_and_condition")
		self.assertEqual(terms.fetch_from, "supplier.terms_and_condition")
		self.assertEqual(terms.fetch_if_empty, 1)
		self.assertEqual(terms.allow_on_submit, 1)

		process_meta = frappe.get_meta('YRP Process', cached=False)
		self.assertIsNotNone(process_meta.get_field("is_manual_entry_in_grn"))
		process_schema = json.loads(
			open(
				frappe.get_app_path(
					"yrp", "yrp", "doctype", "yrp_process", "yrp_process.json"
				)
			).read()
		)
		work_order_schema = json.loads(
			open(
				frappe.get_app_path(
					"yrp", "yrp", "doctype", "yrp_work_order", "yrp_work_order.json"
				)
			).read()
		)
		process_standard_fields = {row["fieldname"] for row in process_schema["fields"]}
		work_order_standard_fields = {row["fieldname"] for row in work_order_schema["fields"]}
		self.assertNotIn("includes_packing", process_standard_fields)
		self.assertNotIn("includes_packing", work_order_standard_fields)
		if "essdee_yrp" not in frappe.get_installed_apps():
			self.assertIsNone(process_meta.get_field("includes_packing"))
			self.assertIsNone(work_order_meta.get_field("includes_packing"))

		supplier_meta = frappe.get_meta('YRP Supplier', cached=False)
		self.assertIsNotNone(supplier_meta.get_field("is_company_location"))
		self.assertIsNotNone(supplier_meta.get_field("terms_and_condition"))
		self.assertFalse(
			frappe.db.exists(
				"Custom Field",
				{"dt": 'YRP Supplier', "fieldname": "terms_and_condition"},
			)
		)

	def test_process_and_supplier_flags_are_stored_server_side(self):
		work_order = frappe.new_doc('YRP Work Order')
		work_order.supplier = "TEST-SUPPLIER"
		work_order.process_name = "TEST-PROCESS"

		def get_value(doctype, name, fields, **kwargs):
			if doctype == 'YRP Supplier':
				return 1
			if doctype == 'YRP Process':
				self.assertEqual(fields, "is_manual_entry_in_grn")
				return 1
			raise AssertionError(f"Unexpected lookup: {doctype}.{fields}")

		with patch.object(frappe.db, "get_value", side_effect=get_value):
			work_order.set_linked_process_and_supplier_flags()

		self.assertEqual(work_order.is_internal_unit, 1)
		self.assertEqual(work_order.is_manual_entry, 1)
