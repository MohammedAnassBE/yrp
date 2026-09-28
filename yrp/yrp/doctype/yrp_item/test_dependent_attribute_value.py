"""The reporting field is derived from actual attributes, not naming/display rules."""
import unittest
from unittest.mock import patch

import frappe

from yrp.yrp.doctype.yrp_item.yrp_item import (
	YRPItemMixin,
	get_dependent_attribute_value,
)


class DependentAttributeValueTest(unittest.TestCase):
	def test_actual_value_even_when_display_name_is_empty(self):
		rows = [{"attribute": "Stage", "attribute_value": "Pack", "display_name": "", "display_name_is_empty": 1}]
		self.assertEqual(get_dependent_attribute_value(rows, "Stage"), "Pack")

	def test_template_selects_attribute_not_row_order(self):
		rows = [{"attribute": "Size", "attribute_value": "6"}, {"attribute": "Stage", "attribute_value": "Stitch"}]
		self.assertEqual(get_dependent_attribute_value(rows, "Stage"), "Stitch")

	def test_no_dependent_attribute_or_missing_value_stays_blank(self):
		self.assertIsNone(get_dependent_attribute_value([{"attribute": "Size", "attribute_value": "6"}], None))
		self.assertIsNone(get_dependent_attribute_value([], "Stage"))

	def test_zero_value_is_preserved(self):
		self.assertEqual(get_dependent_attribute_value([{"attribute": "Count", "attribute_value": 0}], "Count"), "0")

	def test_conflicting_values_are_rejected(self):
		rows = [{"attribute": "Stage", "attribute_value": value} for value in ("Pack", "Stitch")]
		with patch("yrp.yrp.doctype.yrp_item.yrp_item._", side_effect=lambda text: text), patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.throw", side_effect=ValueError), self.assertRaises(ValueError):
			get_dependent_attribute_value(rows, "Stage")

	def test_save_replaces_client_value_and_clears_standalone_value(self):
		class Item(YRPItemMixin):
			variant_of = "Template"
			meta = frappe._dict(has_field=lambda _: False)
			item_hash_value = "existing-hash"
			dependent_attribute_value = "Forged"
			attributes = [frappe._dict(attribute="Stage", attribute_value="Pack")]

			def get(self, key):
				return getattr(self, key, None)

		doc = Item()
		with patch("yrp.yrp.doctype.yrp_item.yrp_item.frappe.get_cached_value", return_value="Stage"):
			doc.before_validate()
			self.assertEqual(doc.dependent_attribute_value, "Pack")
			doc.variant_of = None
			doc.before_validate()
			self.assertIsNone(doc.dependent_attribute_value)
