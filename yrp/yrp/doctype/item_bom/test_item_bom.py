import frappe
from frappe.tests import IntegrationTestCase


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
