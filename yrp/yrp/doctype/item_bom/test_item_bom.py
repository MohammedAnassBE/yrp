from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.doctype.item_bom.item_bom import validate_bom_item_variant_mapping
from yrp.yrp.utils.ipd_engine import get_consumables


class TestItemBOMMetadata(IntegrationTestCase):
	def test_item_bom_keeps_validated_f16_fields_and_f15_defaults(self):
		meta = frappe.get_meta("Item BOM", cached=False)

		uom = meta.get_field("uom")
		self.assertEqual(uom.fieldtype, "Link")
		self.assertEqual(uom.options, "UOM")
		self.assertEqual(uom.fetch_from, "item.default_unit_of_measure")
		self.assertEqual(uom.reqd, 1)

		self.assertEqual(meta.get_field("qty_of_product").non_negative, 1)
		self.assertEqual(meta.get_field("qty_of_bom_item").non_negative, 1)
		self.assertEqual(meta.get_field("wastage_pct").non_negative, 1)

		# A blank process means the consumable applies to every process. The
		# calculation engine intentionally supports this base-YRP use case.
		self.assertEqual(meta.get_field("process_name").reqd, 0)

	def test_mapping_carries_legacy_migration_context(self):
		meta = frappe.get_meta("Item BOM Attribute Mapping", cached=False)

		ipd = meta.get_field("item_production_detail")
		self.assertEqual(ipd.fieldtype, "Link")
		self.assertEqual(ipd.options, "Item Production Detail")
		self.assertEqual(ipd.hidden, 1)

		bom_uom = meta.get_field("bom_uom")
		self.assertEqual(bom_uom.fieldtype, "Link")
		self.assertEqual(bom_uom.options, "UOM")
		self.assertEqual(bom_uom.fetch_from, "bom_item.default_unit_of_measure")
		self.assertEqual(bom_uom.read_only, 1)

	def test_attribute_item_requires_mapping_before_bom_calculation(self):
		item = frappe._dict(
			attributes=[frappe._dict(attribute="Colour")],
		)
		row = frappe._dict(
			idx=16,
			item="Tag Bullet",
			based_on_attribute_mapping=0,
			attribute_mapping=None,
		)

		with (
			patch.object(frappe, "get_cached_doc", return_value=item),
			self.assertRaisesRegex(
				frappe.ValidationError,
				"Tag Bullet.*Colour.*has no attribute mapping",
			),
		):
			validate_bom_item_variant_mapping(row)

	def test_consumable_calculation_cannot_resolve_legacy_empty_variant(self):
		item = frappe._dict(
			attributes=[frappe._dict(attribute="Colour")],
		)
		row = frappe._dict(
			idx=16,
			item="Tag Bullet",
			process_name="Packing",
			based_on_attribute_mapping=0,
			attribute_mapping=None,
			qty_of_product=1,
			qty_of_bom_item=1,
			wastage_pct=0,
			uom="Nos",
		)
		ipd = frappe._dict(name="TEST-IPD", item_bom=[row])

		with (
			patch.object(frappe, "get_doc", return_value=ipd),
			patch.object(frappe, "get_cached_doc", return_value=item),
			self.assertRaisesRegex(
				frappe.ValidationError,
				"Tag Bullet.*Colour.*has no attribute mapping",
			),
		):
			get_consumables(
				ipd.name,
				15,
				variants=[{"attrs": {"Colour": "Black"}, "qty": 15}],
				process_name="Packing",
			)

	def test_configured_bom_mapping_is_accepted(self):
		item = frappe._dict(
			attributes=[frappe._dict(attribute="Colour")],
		)
		row = frappe._dict(
			idx=16,
			item="Tag Bullet",
			based_on_attribute_mapping=1,
			attribute_mapping="TEST-MAPPING",
		)

		with patch.object(frappe, "get_cached_doc", return_value=item):
			validate_bom_item_variant_mapping(row)
