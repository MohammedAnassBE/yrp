# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.stock.dimensions import get_stock_dimensions


class TestWorkOrderStockDimensions(FrappeTestCase):
	def test_configured_dimensions_are_generated_on_both_work_order_child_tables(self):
		dimensions = get_stock_dimensions()
		self.assertTrue(dimensions)

		for doctype in ("Work Order Deliverables", "Work Order Receivables"):
			meta = frappe.get_meta(doctype, cached=False)
			for dimension in dimensions:
				field = meta.get_field(dimension["fieldname"])
				self.assertIsNotNone(field, f"{doctype} is missing {dimension['fieldname']}")
				self.assertEqual(field.fieldtype, "Link")
				self.assertEqual(field.options, dimension["dimension_doctype"])
				self.assertEqual(field.reqd, 1)
				custom_field = frappe.db.get_value(
					"Custom Field",
					{"dt": doctype, "fieldname": dimension["fieldname"]},
					["name", "module"],
					as_dict=True,
				)
				self.assertTrue(
					custom_field,
					f"{doctype}.{dimension['fieldname']} must be generated from YRP Stock Settings",
				)
				self.assertEqual(custom_field.module, "YRP")

	def test_work_order_children_receive_values_through_the_dimension_model(self):
		work_order = frappe.new_doc("Work Order")
		expected = {}
		for dimension in get_stock_dimensions():
			fieldname = dimension["fieldname"]
			expected[fieldname] = f"TEST-DIMENSION-{fieldname}"
			work_order.set(fieldname, expected[fieldname])
		deliverable = work_order.append("deliverables", {})
		receivable = work_order.append("receivables", {})

		work_order.set_stock_dimension_values()

		for row in (deliverable, receivable):
			for fieldname, value in expected.items():
				self.assertEqual(row.get(fieldname), value)
