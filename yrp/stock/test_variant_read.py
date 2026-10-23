from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase


class TestVariantRead(UnitTestCase):
	def test_raw_children_preserve_idx_duplicates_and_skip_unused_invalid_links(self):
		from yrp.stock.save_stock_items import _stock_variant_reader

		rows = [
			frappe._dict(parent="V", attribute="Size", attribute_value="L", idx=2),
			frappe._dict(parent="V", attribute="Unused", attribute_value="IAV-missing", idx=3),
			frappe._dict(parent="V", attribute="Size", attribute_value="S", idx=10),
		]
		queries = []
		def get_all(doctype, **kwargs):
			queries.append((doctype, kwargs))
			if doctype == "Item":
				return ["V"]
			self.assertEqual(kwargs["filters"]["parenttype"], "Item")
			self.assertEqual(kwargs["filters"]["parentfield"], "attributes")
			self.assertEqual(kwargs["order_by"], "idx asc")
			return rows

		with patch.object(frappe, "get_all", side_effect=get_all):
			read = _stock_variant_reader(["V", "V", "missing-unused"])
			self.assertEqual([r.attribute_value for r in read("V") if r.attribute == "Size"], ["L", "S"])
			self.assertEqual(read("V")[1].attribute_value, "IAV-missing")
			self.assertEqual(len(queries), 2)
			with patch.object(frappe, "get_doc", side_effect=frappe.DoesNotExistError):
				with self.assertRaises(frappe.DoesNotExistError):
					read("missing-unused")

	def test_equal_child_indexes_keep_original_per_item_read_order(self):
		from yrp.stock.save_stock_items import _stock_variant_reader

		batched = [
			frappe._dict(parent="V", attribute="Size", attribute_value="S", idx=1),
			frappe._dict(parent="V", attribute="Size", attribute_value="L", idx=1),
		]
		original = list(reversed(batched))
		with (
			patch.object(frappe, "get_all", side_effect=[["V"], batched]),
			patch.object(frappe, "get_doc", return_value=frappe._dict(attributes=original)),
		):
			self.assertEqual([row.attribute_value for row in _stock_variant_reader(["V"])("V")], ["L", "S"])

	def test_grouping_keeps_natural_order_zero_auto_rows_and_duplicate_size_rows(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		rows = [
			frappe._dict(item="V", qty=qty, row_index=index, uom="Pieces")
			for index, qty in [("10", 10), ("2", 2), ("fc-10", 110), ("fc-2", 102), (0, 0), (None, 7), ("", 8), ("2", 99)]
		]
		children = [
			frappe._dict(parent="V", attribute="Colour", attribute_value="Red", idx=1),
			frappe._dict(parent="V", attribute="Size", attribute_value="S", idx=2),
			frappe._dict(parent="V", attribute="Unused", attribute_value="IAV-missing", idx=3),
		]
		get_all = frappe.get_all
		def read_rows(doctype, *args, **kwargs):
			if doctype == "Item":
				return ["V"]
			if doctype == "Item Variant Attribute":
				return children
			return get_all(doctype, *args, **kwargs)

		with (
			patch.object(frappe, "get_all", side_effect=read_rows),
			patch("yrp.yrp.doctype.yrp_item.yrp_item.get_parent_item", return_value="T"),
			patch("yrp.yrp.doctype.yrp_item.yrp_item.get_attribute_details", return_value={
				"attributes": ["Colour"], "primary_attribute": "Size", "primary_attribute_values": ["S", "M"],
			}),
			patch("yrp.stock.save_stock_items.get_dimension_fieldnames", return_value=[]),
			patch("yrp.stock.save_stock_items._get_entry_fields", return_value=[]),
		):
			result = group_items_for_ui(rows, "YRP Stock Entry")
		self.assertEqual(len(result), 1)
		items = result[0]["items"]
		# Two saved rows for the same size must not overwrite each other.
		self.assertEqual([item["values"]["S"]["qty"] for item in items], [0, 2, 99, 10, 102, 110, 7, 8])
		self.assertEqual(items[0]["attributes"], {"Colour": "Red"})
		self.assertEqual(items[0]["values"]["M"]["qty"], 0)

	def test_parent_identity_preserves_blank_missing_variant_and_standalone_without_documents(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import get_parent_item

		items = {"V": frappe._dict(name="V", variant_of="T"), "T": frappe._dict(name="T"), "S": frappe._dict(name="S")}
		with (
			patch.object(frappe.db, "get_value", side_effect=lambda dt, name, *a, **k: items.get(name)),
			patch.object(frappe, "get_cached_doc", side_effect=AssertionError("Unexpected Item document")),
		):
			self.assertEqual([get_parent_item(name) for name in ["V", "T", "S", "", "missing"]], ["T", "T", "S", None, None])
