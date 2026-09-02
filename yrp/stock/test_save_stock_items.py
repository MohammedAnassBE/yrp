from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from yrp.stock.save_stock_items import (
	PARENT_CHILD_MAP,
	_empty_value_fields,
	_get_entry_fields,
	group_items_for_ui,
)


class TestStockItemEntryFieldExtensions(UnitTestCase):
	def test_padded_secondary_uom_is_blank_not_numeric_zero(self):
		defaults = _empty_value_fields(
			["secondary_qty", "secondary_uom"], "Delivery Challan Item"
		)
		self.assertEqual(defaults["secondary_qty"], 0)
		self.assertEqual(defaults["secondary_uom"], "")

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

	def test_same_row_index_keeps_received_type_splits_separate(self):
		variant = frappe._dict(
			item="Test Garment",
			attributes=[
				frappe._dict(attribute="Colour", attribute_value="Red"),
				frappe._dict(attribute="Size", attribute_value="45 cm"),
			],
		)
		attribute_details = {
			"attributes": ["Colour"],
			"primary_attribute": "Size",
			"primary_attribute_values": ["45 cm"],
			"dependent_attribute": "",
			"dependent_attribute_details": {},
			"default_uom": "Pieces",
		}
		rows = [
			frappe._dict(
				item_variant="GARMENT-RED-45",
				quantity=11,
				received_type="Accepted",
				row_index=0,
				table_index=0,
			),
			frappe._dict(
				item_variant="GARMENT-RED-45",
				quantity=1,
				received_type="Adas",
				row_index=0,
				table_index=0,
			),
		]

		with (
			patch.object(frappe.db, "get_value", return_value="Test Garment"),
			patch.object(frappe, "get_doc", return_value=variant),
			patch(
				"yrp.yrp.doctype.item.item.get_attribute_details",
				return_value=attribute_details,
			),
			patch(
				"yrp.stock.save_stock_items.get_dimension_fieldnames",
				return_value=["received_type"],
			),
			patch(
				"yrp.stock.save_stock_items._child_has_field",
				return_value=True,
			),
			patch(
				"yrp.stock.save_stock_items._copy_supported_fields",
				return_value={},
			),
			# This test isolates Received Type grouping. Type-correct padding has
			# its own metadata-backed regression above.
			patch(
				"yrp.stock.save_stock_items._empty_value_fields",
				return_value={},
			),
		):
			grouped = group_items_for_ui(rows, "Goods Received Note")

		items = grouped[0]["items"]
		self.assertEqual(len(items), 2)
		by_type = {item["dimensions"]["received_type"]: item for item in items}
		self.assertEqual(by_type["Accepted"]["values"]["45 cm"]["qty"], 11)
		self.assertEqual(by_type["Adas"]["values"]["45 cm"]["qty"], 1)
