"""Template-scoped native attribute lookup preserves multi-value matching."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp.doctype.yrp_item.yrp_item import _get_variants_by_attributes


class VariantAttributeLookupTest(unittest.TestCase):
	def test_intersects_attributes_within_template_and_multiple_values(self):
		with patch.object(frappe.db, "sql", side_effect=[["A", "B"], ["A"]]) as query:
			result = _get_variants_by_attributes({"Colour": ["Wine", "Black"], "Size": "S"}, "Template")
		self.assertEqual(result, ["A"])
		self.assertEqual(query.call_count, 2)
		for call in query.call_args_list:
			self.assertIn("JOIN `tabItem`", str(call.args[0]))
			self.assertNotIn("FOR UPDATE", str(call.args[0]))
			self.assertIn("Template", call.args[1].values())
		self.assertIn("A", query.call_args_list[1].args[1].values())
		self.assertIn("B", query.call_args_list[1].args[1].values())

	def test_empty_template_does_not_scan_global_attributes(self):
		with patch.object(frappe.db, "sql", return_value=[]) as query:
			self.assertEqual(_get_variants_by_attributes({"Stage": "Cut"}, "Empty"), [])
		self.assertEqual(query.call_count, 1)
		self.assertIn("JOIN `tabItem`", str(query.call_args.args[0]))

	def test_no_matching_attribute_stops_early(self):
		with patch.object(frappe.db, "sql", return_value=[]) as query:
			self.assertEqual(_get_variants_by_attributes({"Colour": "Missing", "Size": "S"}, "Template"), [])
		self.assertEqual(query.call_count, 1)

	def test_unscoped_search_keeps_intersection_semantics(self):
		with patch.object(frappe.db, "sql", side_effect=[["A", "B"], ["B", "C"]]) as query:
			self.assertEqual(_get_variants_by_attributes({"Colour": "Wine", "Size": "S"}), ["B"])
		for call in query.call_args_list:
			self.assertNotIn("JOIN `tabItem`", str(call.args[0]))
