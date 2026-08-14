from unittest.mock import patch

from frappe.tests import UnitTestCase

from yrp.stock.save_stock_items import PARENT_CHILD_MAP, _get_entry_fields


class TestStockItemEntryFieldExtensions(UnitTestCase):
	def test_base_map_has_no_essdee_or_obsolete_fields(self):
		all_fields = {
			fieldname
			for config in PARENT_CHILD_MAP.values()
			for fieldname in config.get("entry_fields") or []
		}
		self.assertFalse(
			{
				"fabric_reference_variant",
				"fabric_reference_allocations",
				"item_type",
			}
			& all_fields
		)

	def test_consumer_fields_are_merged_without_duplicates(self):
		config = PARENT_CHILD_MAP["Work Order Deliverables"]

		def provider(**kwargs):
			return ["fabric_reference_variant", "comments"]

		with (
			patch(
				"yrp.stock.save_stock_items.frappe.get_hooks",
				return_value=["consumer.get_entry_fields"],
			),
			patch(
				"yrp.stock.save_stock_items.frappe.get_attr",
				return_value=provider,
			),
		):
			fields = _get_entry_fields("Work Order Deliverables", config)

		self.assertIn("fabric_reference_variant", fields)
		self.assertEqual(fields.count("comments"), 1)
