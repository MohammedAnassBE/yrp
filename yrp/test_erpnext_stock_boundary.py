from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from yrp.erpnext_stock_guard import reject_yrp_stock_items
from yrp.yrp.doctype.yrp_item.yrp_item import YRPItemMixin, update_yrp_variants


class TestERPNextStockBoundary(UnitTestCase):
	def test_new_stock_item_is_blocked_before_first_yrp_stock_row(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			items=[frappe._dict(item_code="ITEM-NEW")],
		)
		with patch("yrp.erpnext_stock_guard.frappe.get_cached_value", return_value=1):
			with self.assertRaisesRegex(frappe.ValidationError, "maintained by YRP stock"):
				reject_yrp_stock_items(doc)

	def test_non_stock_item_does_not_trigger_the_erpnext_guard(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			items=[frappe._dict(item_code="SERVICE-ITEM")],
		)
		with patch("yrp.erpnext_stock_guard.frappe.get_cached_value", return_value=0):
			reject_yrp_stock_items(doc)

	def test_non_stock_updating_invoice_is_unchanged(self):
		doc = frappe._dict(
			doctype="Purchase Invoice",
			update_stock=0,
			items=[frappe._dict(item_code="ITEM-NEW")],
		)
		with patch("yrp.erpnext_stock_guard.frappe.get_cached_value") as get_value:
			reject_yrp_stock_items(doc)
		get_value.assert_not_called()

	def test_stock_bundle_component_cannot_bypass_through_packed_items(self):
		doc = frappe._dict(
			doctype="Delivery Note",
			items=[frappe._dict(item_code="NON-STOCK-BUNDLE")],
			packed_items=[frappe._dict(item_code="STOCK-COMPONENT")],
		)
		stock_state = {"NON-STOCK-BUNDLE": 0, "STOCK-COMPONENT": 1}
		with patch(
			"yrp.erpnext_stock_guard.frappe.get_cached_value",
			side_effect=lambda _doctype, item_code, _field: stock_state[item_code],
		):
			with self.assertRaisesRegex(frappe.ValidationError, "STOCK-COMPONENT"):
				reject_yrp_stock_items(doc)

	def test_subcontract_raw_material_cannot_bypass_through_supplied_items(self):
		doc = frappe._dict(
			doctype="Subcontracting Receipt",
			items=[frappe._dict(item_code="NON-STOCK-OUTPUT")],
			supplied_items=[frappe._dict(rm_item_code="STOCK-RAW-MATERIAL")],
		)
		stock_state = {"NON-STOCK-OUTPUT": 0, "STOCK-RAW-MATERIAL": 1}
		with patch(
			"yrp.erpnext_stock_guard.frappe.get_cached_value",
			side_effect=lambda _doctype, item_code, _field: stock_state[item_code],
		):
			with self.assertRaisesRegex(frappe.ValidationError, "STOCK-RAW-MATERIAL"):
				reject_yrp_stock_items(doc)

	def test_template_fields_are_locked_when_a_variant_has_yrp_stock(self):
		before = frappe._dict(
			stock_uom="Nos",
			is_stock_item=1,
			variant_of=None,
			has_variants=1,
			primary_attribute="Size",
			dependent_attribute=None,
			dependent_attribute_mapping=None,
			item_tuple_attribute=None,
			attributes=[
				frappe._dict(attribute="Size", attribute_value=None, mapping="MAP-1")
			],
		)
		current = frappe._dict(before.copy())
		current.name = "ITEM-TEMPLATE"
		current.primary_attribute = "Colour"
		current.is_new = lambda: False
		current.get_doc_before_save = lambda: before

		with patch(
			"yrp.yrp.doctype.yrp_item.yrp_item._item_family_has_yrp_stock",
			return_value=True,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "primary_attribute"):
				YRPItemMixin._validate_yrp_stock_mutation(current)

	def test_physical_variant_tuple_is_locked_after_yrp_stock(self):
		before = frappe._dict(
			stock_uom="Nos",
			is_stock_item=1,
			variant_of="ITEM-TEMPLATE",
			has_variants=0,
			primary_attribute=None,
			dependent_attribute=None,
			dependent_attribute_mapping=None,
			item_tuple_attribute="(('Size', 'S'),)",
			attributes=[
				frappe._dict(attribute="Size", attribute_value="S", mapping=None)
			],
		)
		current = frappe._dict(before.copy())
		current.name = "ITEM-S"
		current.item_tuple_attribute = "(('Size', 'M'),)"
		current.is_new = lambda: False
		current.get_doc_before_save = lambda: before

		with patch(
			"yrp.yrp.doctype.yrp_item.yrp_item._item_family_has_yrp_stock",
			return_value=True,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "item_tuple_attribute"):
				YRPItemMixin._validate_yrp_stock_mutation(current)

	def test_template_propagation_preserves_physical_variant_identity(self):
		state = {
			"attributes": [frappe._dict(attribute="Size", attribute_value="S")],
			"primary_attribute": None,
			"dependent_attribute": None,
			"dependent_attribute_mapping": None,
			"item_hash_value": "VARIANT-HASH",
			"item_tuple_attribute": "(('Size', 'S'),)",
			"stock_uom": "Nos",
		}
		variant = Mock()
		variant.get.side_effect = state.get
		variant.set.side_effect = state.__setitem__

		def simulate_erpnext_copy(_template, target):
			target.set("attributes", [])
			target.set("primary_attribute", "Size")
			target.set("item_hash_value", "TEMPLATE-HASH")
			target.set("item_tuple_attribute", None)
			target.set("stock_uom", "Meter")

		with (
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.get_doc", return_value=variant),
			patch(
				"erpnext.controllers.item_variant.copy_attributes_to_variant",
				side_effect=simulate_erpnext_copy,
			),
		):
			update_yrp_variants([frappe._dict(item_code="ITEM-S")], Mock(), publish_progress=False)

		self.assertEqual(state["item_hash_value"], "VARIANT-HASH")
		self.assertEqual(state["item_tuple_attribute"], "(('Size', 'S'),)")
		self.assertIsNone(state["primary_attribute"])
		self.assertEqual(state["attributes"][0].attribute_value, "S")
		self.assertEqual(state["stock_uom"], "Meter")
		variant.save.assert_called_once_with()
