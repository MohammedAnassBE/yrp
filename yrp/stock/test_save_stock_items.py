from contextlib import ExitStack
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from yrp.stock.save_stock_items import (
	PARENT_CHILD_MAP,
	_empty_value_fields,
	_get_entry_fields,
	group_items_for_ui,
	ungroup_items_from_ui,
)

NEEDLE_VARIANTS = {
	"NEEDLE-OVER-LOCK": {"Name": "Over Lock", "Type": "DC X 27"},
	"NEEDLE-KAJA": {"Name": "Kaja", "Type": "DPx5"},
	"NEEDLE-FLAT-LOCK": {"Name": "Flat Lock", "Type": "UY 128"},
	"SHIRT-RED-S": {"Colour": "Red", "Size": "S"},
	"SHIRT-RED-M": {"Colour": "Red", "Size": "M"},
	"SHIRT-BLUE-S": {"Colour": "Blue", "Size": "S"},
}
NEEDLE_DETAILS = {
	"attributes": ["Name", "Type"],
	"primary_attribute": "",
	"primary_attribute_values": [],
	"dependent_attribute": "",
	"dependent_attribute_details": {},
	"default_uom": "Pack",
}
SHIRT_DETAILS = {
	"attributes": ["Colour"],
	"primary_attribute": "Size",
	"primary_attribute_values": ["S", "M"],
	"dependent_attribute": "",
	"dependent_attribute_details": {},
	"default_uom": "Nos",
}


def _variant_reader(names):
	def read(name):
		return [
			frappe._dict(attribute=attribute, attribute_value=value)
			for attribute, value in NEEDLE_VARIANTS[name].items()
		]

	return read


def _variant_name(parent_item, attributes):
	return next(
		name
		for name, values in NEEDLE_VARIANTS.items()
		if name.startswith(parent_item)
		and values == {key: value for key, value in attributes.items() if value}
	)


def _patched_item_master():
	return (
		patch(
			"yrp.yrp.doctype.yrp_item.yrp_item.get_parent_item",
			side_effect=lambda name: name.split("-", 1)[0],
		),
		patch(
			"yrp.yrp.doctype.yrp_item.yrp_item.get_attribute_details",
			side_effect=lambda parent: NEEDLE_DETAILS if parent == "NEEDLE" else SHIRT_DETAILS,
		),
		patch("yrp.stock.save_stock_items._stock_variant_reader", side_effect=_variant_reader),
		patch("yrp.stock.save_stock_items._resolve_or_create_variant", side_effect=_variant_name),
		patch("yrp.stock.save_stock_items.get_dimension_fieldnames", return_value=["lot"]),
	)


def _needle_entry(name, kind, qty, **saved_indexes):
	return {
		"name": "NEEDLE",
		"dimensions": {"lot": "Open Lot"},
		"attributes": {"Name": name, "Type": kind},
		"primary_attribute": "",
		"values": {"default": {"qty": qty}},
		"default_uom": "Pack",
		**saved_indexes,
	}


def _purchase_order_rows(item_details):
	with ExitStack() as stack:
		for patcher in _patched_item_master():
			stack.enter_context(patcher)
		return ungroup_items_from_ui(item_details, "Purchase Order")


def _group_purchase_order_rows(rows):
	with ExitStack() as stack:
		for patcher in _patched_item_master():
			stack.enter_context(patcher)
		return group_items_for_ui(rows, "Purchase Order")


class TestStockItemEntryFieldExtensions(UnitTestCase):
	def test_padded_secondary_uom_is_blank_not_numeric_zero(self):
		defaults = _empty_value_fields(["secondary_qty", "secondary_uom"], "YRP Delivery Challan Item")
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
		config = PARENT_CHILD_MAP["YRP Work Order Deliverables"]

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
			fields = _get_entry_fields("YRP Work Order Deliverables", config)

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
			patch("yrp.yrp.doctype.yrp_item.yrp_item.get_parent_item", return_value="Test Garment"),
			patch.object(frappe, "get_doc", return_value=variant),
			patch(
				"yrp.yrp.doctype.yrp_item.yrp_item.get_attribute_details",
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
			grouped = group_items_for_ui(rows, "YRP Goods Received Note")

		items = grouped[0]["items"]
		self.assertEqual(len(items), 2)
		by_type = {item["dimensions"]["received_type"]: item for item in items}
		self.assertEqual(by_type["Accepted"]["values"]["45 cm"]["qty"], 11)
		self.assertEqual(by_type["Adas"]["values"]["45 cm"]["qty"], 1)


class TestStockItemRowIndexes(UnitTestCase):
	def test_purchase_order_save_ignores_stale_indexes_and_reload_keeps_every_row(self):
		# Loaded entries carry indexes with a gap (r1 missing); the new entry has none.
		item_details = [
			{
				**NEEDLE_DETAILS,
				"items": [
					_needle_entry("Over Lock", "DC X 27", 20, table_index=2, row_index=4),
					_needle_entry("Kaja", "DPx5", 5, table_index=2, row_index=5),
					_needle_entry("Flat Lock", "UY 128", 25),
				],
			}
		]

		rows = _purchase_order_rows(item_details)

		self.assertEqual([row["row_index"] for row in rows], [0, 1, 2])
		self.assertEqual({row["table_index"] for row in rows}, {0})
		grouped = _group_purchase_order_rows(rows)
		self.assertEqual(
			[(item["attributes"]["Name"], item["values"]["default"]["qty"]) for item in grouped[0]["items"]],
			[("Over Lock", 20), ("Kaja", 5), ("Flat Lock", 25)],
		)

	def test_saved_duplicate_row_index_loads_every_row(self):
		rows = [
			{"item_code": "NEEDLE-KAJA", "qty": 5, "lot": "Open Lot", "table_index": 2, "row_index": "5"},
			{
				"item_code": "NEEDLE-FLAT-LOCK",
				"qty": 20,
				"lot": "Open Lot",
				"table_index": 2,
				"row_index": "5",
			},
			{
				"item_code": "NEEDLE-OVER-LOCK",
				"qty": 3,
				"lot": "Open Lot",
				"table_index": 2,
				"row_index": "6",
			},
			{"item_code": "SHIRT-RED-S", "qty": 7, "lot": "Open Lot", "table_index": 3, "row_index": "6"},
		]

		grouped = _group_purchase_order_rows(rows)

		needles = [
			item["attributes"]["Name"]
			for group in grouped
			for item in group["items"]
			if item["name"] == "NEEDLE"
		]
		self.assertEqual(needles, ["Kaja", "Flat Lock", "Over Lock"])
		shirts = [item for group in grouped for item in group["items"] if item["name"] == "SHIRT"]
		self.assertEqual(len(shirts), 1)
		self.assertEqual(shirts[0]["values"]["S"]["qty"], 7)

	def test_shared_row_index_keeps_sizes_together_and_colours_apart(self):
		rows = [
			{"item_code": "SHIRT-RED-S", "qty": 1, "lot": "Open Lot", "table_index": 0, "row_index": "0"},
			{"item_code": "SHIRT-RED-M", "qty": 2, "lot": "Open Lot", "table_index": 0, "row_index": "0"},
			{"item_code": "SHIRT-BLUE-S", "qty": 3, "lot": "Open Lot", "table_index": 0, "row_index": "0"},
		]

		items = _group_purchase_order_rows(rows)[0]["items"]

		self.assertEqual(
			[
				(item["attributes"]["Colour"], item["values"]["S"]["qty"], item["values"]["M"]["qty"])
				for item in items
			],
			[("Red", 1, 2), ("Blue", 3, 0)],
		)
