import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_purchase_order import yrp_purchase_order as purchase_order


class TestPurchaseOrderProcurementDetails(FrappeTestCase):
	def test_generic_procurement_fields_are_native_to_yrp(self):
		meta = frappe.get_meta('YRP Purchase Order')
		expected = {
			"supplier_address": ("Link", "Address"),
			"supplier_address_display": ("Small Text", None),
			"contact_person": ("Link", "Contact"),
			"contact_display": ("Small Text", None),
			"contact_mobile": ("Small Text", None),
			"deliver_to_supplier": ("Check", None),
			"default_delivery_location": ("Link", 'YRP Supplier'),
			"delivery_address": ("Link", "Address"),
			"delivery_address_display": ("Small Text", None),
			"show_delivery_details": ("Check", None),
		}
		for fieldname, (fieldtype, options) in expected.items():
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, fieldname)
			self.assertEqual(field.fieldtype, fieldtype)
			if options:
				self.assertEqual(field.options, options)

		for fieldname in ("items", "approved_by", "status", "open_status"):
			self.assertEqual(meta.get_field(fieldname).hidden, 1, fieldname)
		self.assertEqual(meta.get_field("items").reqd, 0)

		schema = json.loads(
			open(
				frappe.get_app_path(
					"yrp", "yrp", "doctype", "yrp_purchase_order", "yrp_purchase_order.json"
				)
			).read()
		)
		standard_fields = {row["fieldname"] for row in schema["fields"]}
		self.assertNotIn("default_lot", standard_fields)
		self.assertNotIn("sd_lot", standard_fields)
		if "essdee_yrp" not in frappe.get_installed_apps():
			self.assertIsNone(meta.get_field("default_lot"))
			self.assertIsNone(meta.get_field("sd_lot"))
			self.assertFalse(
				any(
					field.options in {'SD YRP Lot', "Purchase Order Lot", 'SD YRP Lot MultiSelect'}
					for field in meta.fields
					if field.fieldtype in {"Link", "Table", "Table MultiSelect"}
				)
			)

	def test_hidden_items_requirement_is_server_enforced(self):
		doc = frappe.get_doc(
			{
				"doctype": 'YRP Purchase Order',
				"supplier": "SUPPLIER-A",
				"delivery_warehouse": "WAREHOUSE-A",
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "At least one item is required"):
			doc.validate_items()

	def test_item_rows_receive_configured_stock_dimensions(self):
		meta = frappe.get_meta('YRP Purchase Order Item', cached=False)
		self.assertFalse(meta.autoname)
		child_row = frappe.new_doc('YRP Purchase Order Item')
		child_row.set_new_name()
		self.assertTrue(child_row.name)
		from yrp.stock.dimensions import get_stock_dimensions

		for dimension in get_stock_dimensions():
			fieldname = dimension["fieldname"]
			options = dimension["dimension_doctype"]
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, fieldname)
			self.assertEqual(field.fieldtype, "Link")
			self.assertEqual(field.options, options)
			self.assertEqual(field.reqd, 0)
			self.assertEqual(field.insert_after, "item_variant")
			self.assertIn("Managed by YRP Stock Dimension", field.description or "")

	def test_desk_editor_exposes_dimensions_on_purchase_order_rows(self):
		from pathlib import Path

		source = (
			Path(frappe.get_app_path("yrp"))
			/ "yrp/doctype/yrp_purchase_order/yrp_purchase_order.js"
		).read_text(encoding="utf-8")
		self.assertIn("showDimensions: true", source)

	def test_item_delivery_and_migration_fields_are_native_to_yrp(self):
		meta = frappe.get_meta('YRP Purchase Order Item', cached=False)
		expected = {
			"delivery_location": ("Link", 'YRP Supplier'),
			"expected_delivery_date": ("Date", None),
			"additional_parameters": ("Text", None),
		}
		for fieldname, (fieldtype, options) in expected.items():
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, fieldname)
			self.assertEqual(field.fieldtype, fieldtype)
			if options:
				self.assertEqual(field.options, options)

	def test_item_delivery_defaults_preserve_original_expected_date(self):
		doc = frappe.get_doc(
			{
				"doctype": 'YRP Purchase Order',
				"default_delivery_location": "LOCATION-A",
				"expected_delivery_date": "2026-08-20",
				"items": [{"item_variant": "ITEM-V", "qty": 2}],
			}
		)

		with (
			patch.object(frappe, "get_cached_value", return_value="ITEM-A"),
			patch.object(
				frappe,
				"get_cached_doc",
				return_value=frappe._dict(
					default_unit_of_measure="Nos",
					dependent_attribute=None,
					secondary_unit_of_measure=None,
				),
			),
		):
			doc.set_item_defaults()

		row = doc.items[0]
		self.assertEqual(row.delivery_location, "LOCATION-A")
		self.assertEqual(str(row.delivery_date), "2026-08-20")
		self.assertEqual(str(row.expected_delivery_date), "2026-08-20")

	def test_party_details_are_stored_as_snapshots(self):
		doc = frappe.get_doc(
			{
				"doctype": 'YRP Purchase Order',
				"supplier": "SUPPLIER-A",
				"default_delivery_location": "LOCATION-A",
				"contact_person": "CONTACT-A",
			}
		)
		contact = frappe._dict(full_name="Anand", mobile_no="9000000000")
		with (
			patch.object(
				purchase_order,
				"_resolve_party_address",
				side_effect=[("ADDR-S", "Supplier Address"), ("ADDR-D", "Delivery Address")],
			),
			patch.object(purchase_order, "_is_party_link", return_value=True),
			patch.object(frappe, "get_cached_doc", return_value=contact),
		):
			doc.set_party_details()

		self.assertEqual(doc.supplier_address, "ADDR-S")
		self.assertEqual(doc.supplier_address_display, "Supplier Address")
		self.assertEqual(doc.delivery_address, "ADDR-D")
		self.assertEqual(doc.delivery_address_display, "Delivery Address")
		self.assertEqual(doc.contact_display, "Anand")
		self.assertEqual(doc.contact_mobile, "9000000000")

	def test_delivery_destination_type_is_server_validated(self):
		doc = frappe.get_doc(
			{
				"doctype": 'YRP Purchase Order',
				"default_delivery_location": "EXTERNAL-SUPPLIER",
				"deliver_to_supplier": 0,
			}
		)
		with (
			patch.object(frappe.db, "get_value", return_value=0),
			self.assertRaisesRegex(frappe.ValidationError, "company location"),
		):
			doc.validate_delivery_destination()

		doc.deliver_to_supplier = 1
		with patch.object(frappe.db, "get_value", return_value=0):
			doc.validate_delivery_destination()
