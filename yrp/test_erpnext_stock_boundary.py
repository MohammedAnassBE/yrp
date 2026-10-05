from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from yrp.erpnext_stock_guard import reject_yrp_stock_items
from yrp.yrp.doctype.yrp_item.yrp_item import YRPItemMixin, update_variants, update_yrp_variants
from yrp.yrp.doctype.yrp_item_variant.yrp_item_variant import rename_item_variant


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
			attributes=[frappe._dict(attribute="Size", attribute_value=None, mapping="MAP-1")],
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
			attributes=[frappe._dict(attribute="Size", attribute_value="S", mapping=None)],
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

	def test_missing_item_hash_can_be_initialized_after_yrp_stock(self):
		before = frappe._dict(
			stock_uom="Nos",
			is_stock_item=1,
			variant_of="ITEM-TEMPLATE",
			has_variants=0,
			primary_attribute=None,
			dependent_attribute=None,
			dependent_attribute_mapping=None,
			item_hash_value=None,
			item_tuple_attribute="(('Size', 'S'),)",
			attributes=[frappe._dict(attribute="Size", attribute_value="S", mapping=None)],
		)
		current = frappe._dict(before.copy())
		current.name = "ITEM-S"
		current.item_hash_value = "GENERATED-HASH"
		current.is_new = lambda: False
		current.get_doc_before_save = lambda: before

		with patch(
			"yrp.yrp.doctype.yrp_item.yrp_item._item_family_has_yrp_stock",
			return_value=True,
		):
			try:
				YRPItemMixin._validate_yrp_stock_mutation(current)
			except frappe.ValidationError as exc:
				self.fail(f"One-time hash initialization was rejected: {exc}")

	def test_existing_item_hash_cannot_be_replaced_after_yrp_stock(self):
		before = frappe._dict(
			stock_uom="Nos",
			is_stock_item=1,
			variant_of="ITEM-TEMPLATE",
			has_variants=0,
			primary_attribute=None,
			dependent_attribute=None,
			dependent_attribute_mapping=None,
			item_hash_value="ORIGINAL-HASH",
			item_tuple_attribute="(('Size', 'S'),)",
			attributes=[frappe._dict(attribute="Size", attribute_value="S", mapping=None)],
		)
		current = frappe._dict(before.copy())
		current.name = "ITEM-S"
		current.item_hash_value = "REPLACEMENT-HASH"
		current.is_new = lambda: False
		current.get_doc_before_save = lambda: before

		with patch(
			"yrp.yrp.doctype.yrp_item.yrp_item._item_family_has_yrp_stock",
			return_value=True,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "item_hash_value"):
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

	def test_variant_rename_uses_template_and_attribute_display_names(self):
		variant = frappe._dict(
			name="OLD-VARIANT",
			variant_of="ITEM-TEMPLATE",
			attributes=[
				frappe._dict(
					attribute="Colour",
					attribute_value="Dark Grey",
					display_name="D.Grey",
					display_name_is_empty=0,
				),
				frappe._dict(
					attribute="Internal Stage",
					attribute_value="Finished",
					display_name=None,
					display_name_is_empty=1,
				),
			],
		)
		variant.check_permission = Mock()
		template = frappe._dict(name="ITEM-TEMPLATE")
		renames = []

		def record_rename(doctype, old, new, **kwargs):
			renames.append((doctype, old, new, kwargs))
			return new

		with (
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.get_doc", return_value=variant),
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.get_cached_doc", return_value=template),
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.rename_doc", side_effect=record_rename),
		):
			result = update_variants([variant.name])

		self.assertEqual(result, ["ITEM-TEMPLATE-D.Grey"])
		self.assertEqual(renames[0][:3], ("Item", "OLD-VARIANT", "ITEM-TEMPLATE-D.Grey"))
		self.assertFalse(renames[0][3]["rebuild_search"])

	def test_manual_variant_rename_uses_standard_item_rename(self):
		variant = frappe._dict(
			name="OLD-VARIANT",
			variant_of="ITEM-TEMPLATE",
			attributes=[
				frappe._dict(
					attribute="Size",
					attribute_value="80 cm",
					display_name="80 cm",
					display_name_is_empty=0,
				)
			],
		)
		variant.check_permission = Mock()
		template = frappe._dict(name="ITEM-TEMPLATE")
		renames = []

		def record_rename(doctype, old, new, **kwargs):
			renames.append((doctype, old, new, kwargs))
			return new

		with (
			patch("frappe.get_doc", return_value=variant),
			patch("frappe.get_cached_doc", return_value=template),
			patch("frappe.rename_doc", side_effect=record_rename),
		):
			result = rename_item_variant(variant.name)

		self.assertEqual(result, "ITEM-TEMPLATE-80 cm")
		self.assertEqual(renames[0][:3], ("Item", "OLD-VARIANT", "ITEM-TEMPLATE-80 cm"))
		self.assertTrue(renames[0][3]["rebuild_search"])
		variant.check_permission.assert_called_once_with("write")

	def test_template_rename_enqueues_variant_renames_in_batches(self):
		class ItemController:
			def after_rename(self, old, new, merge):
				self.super_rename = (old, new, merge)

		class ExtendedItem(YRPItemMixin, ItemController):
			has_variants = 1
			variant_of = None

		doc = ExtendedItem()
		variants = [f"VARIANT-{index}" for index in range(120)]
		jobs = []

		def record_job(method, **kwargs):
			jobs.append((method, kwargs))

		with (
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.get_all", return_value=variants),
			patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.enqueue", side_effect=record_job),
		):
			YRPItemMixin.after_rename(doc, "OLD-TEMPLATE", "NEW-TEMPLATE", False)

		self.assertEqual(doc.super_rename, ("OLD-TEMPLATE", "NEW-TEMPLATE", False))
		self.assertEqual(len(jobs), 4)
		self.assertEqual([len(job[1]["variants"]) for job in jobs[:3]], [50, 50, 20])
		self.assertTrue(all(job[1]["enqueue_after_commit"] for job in jobs))
		self.assertEqual(jobs[3][0], "frappe.utils.global_search.rebuild_for_doctype")
		self.assertEqual(jobs[3][1]["doctype"], "Item")
