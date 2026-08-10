from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.purchase_order import purchase_order


class TestPurchaseOrderPriceValidation(FrappeTestCase):
	def test_missing_price_returns_draft_warning_without_throwing(self):
		rows = [frappe._dict(item_variant="YARN-NAVY", qty=500)]

		with (
			patch.object(purchase_order, "_get_parent_item", return_value="60's GL Yarn"),
			patch(
				"yrp.yrp.doctype.item_price.item_price.get_active_price",
				return_value=None,
			) as get_active_price,
		):
			warnings = purchase_order.validate_price_details(
				rows,
				supplier="S-0010",
				strict=False,
			)

		self.assertEqual(len(warnings), 1)
		self.assertIn("60's GL Yarn", warnings[0])
		self.assertIn("cannot be submitted", warnings[0])
		get_active_price.assert_called_once_with("60's GL Yarn", "S-0010", raise_error=False)

	def test_strict_submit_lookup_remains_authoritative(self):
		rows = [frappe._dict(item_variant="YARN-NAVY", qty=500)]

		with (
			patch.object(purchase_order, "_get_parent_item", return_value="60's GL Yarn"),
			patch(
				"yrp.yrp.doctype.item_price.item_price.get_active_price",
				side_effect=frappe.ValidationError("Active Item Price is required"),
			) as get_active_price,
			self.assertRaisesRegex(frappe.ValidationError, "Active Item Price is required"),
		):
			purchase_order.validate_price_details(
				rows,
				supplier="S-0010",
				strict=True,
			)

		get_active_price.assert_called_once_with("60's GL Yarn", "S-0010", raise_error=True)
