from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp_stock.doctype.yrp_stock_update.yrp_stock_update import get_stock_update_rates


class TestStockUpdateRateValidation(FrappeTestCase):
	def _stock_update(self, update_type, rate):
		doc = frappe.new_doc('YRP Stock Update')
		doc.update_type = update_type
		doc.append(
			"stock_update_details",
			{
				"item_variant": "TEST-STOCK-UPDATE-ITEM",
				"update_diff_qty": 1,
				"rate": rate,
			},
		)
		return doc

	def test_add_rejects_zero_rate_after_submit_time_refresh(self):
		doc = self._stock_update("Add", 10)

		def set_zero_rate():
			doc.stock_update_details[0].rate = 0

		with (
			patch.object(doc, "set_rate_from_last_sle", side_effect=set_zero_rate),
			self.assertRaises(frappe.ValidationError) as raised,
		):
			doc.before_submit()

		self.assertIn("Zero-rate stock can only be reduced", str(raised.exception))

	def test_add_accepts_positive_refreshed_rate(self):
		doc = self._stock_update("Add", 0)

		def set_positive_rate():
			doc.stock_update_details[0].rate = 12.5

		with patch.object(doc, "set_rate_from_last_sle", side_effect=set_positive_rate):
			doc.before_submit()

	def test_reduce_accepts_zero_rate(self):
		doc = self._stock_update("Reduce", 10)

		def set_zero_rate():
			doc.stock_update_details[0].rate = 0

		with patch.object(doc, "set_rate_from_last_sle", side_effect=set_zero_rate):
			doc.before_submit()

	def test_editor_rate_lookup_is_bucket_aware(self):
		with (
			patch(
				"yrp.yrp_stock.doctype.yrp_stock_update.yrp_stock_update.frappe.has_permission"
			) as has_permission,
			patch(
				"yrp.yrp.doctype.yrp_item.yrp_item.get_variant",
				return_value="TEST-VARIANT-M",
			) as get_variant,
			patch(
				"yrp.stock.utils.get_last_sle_rate",
				return_value=(17.25, True),
			) as get_last_sle_rate,
		):
			rates = get_stock_update_rates(
				item="TEST-ITEM",
				attributes={"Colour": "Blue"},
				primary_attribute="Size",
				value_keys=["M"],
				warehouse="TEST-WAREHOUSE",
				dimensions={"received_type": "Accepted", "unknown": "ignored"},
			)

		self.assertEqual(rates, {"M": 17.25})
		get_variant.assert_called_once_with(
			"TEST-ITEM", {"Colour": "Blue", "Size": "M"}
		)
		args, kwargs = get_last_sle_rate.call_args
		self.assertEqual(args, ("TEST-VARIANT-M",))
		self.assertEqual(kwargs["warehouse"], "TEST-WAREHOUSE")
		self.assertNotIn("unknown", kwargs)
		has_permission.assert_any_call('YRP Stock Update', "create", throw=True)
		has_permission.assert_any_call(
			'YRP Item', "read", doc="TEST-ITEM", throw=True
		)
		has_permission.assert_any_call(
			'YRP Warehouse', "read", doc="TEST-WAREHOUSE", throw=True
		)

	def test_editor_rate_lookup_rejects_unbounded_value_lists(self):
		with (
			patch(
				"yrp.yrp_stock.doctype.yrp_stock_update.yrp_stock_update.frappe.has_permission"
			),
			self.assertRaisesRegex(frappe.ValidationError, "maximum of 200"),
		):
			get_stock_update_rates(
				item="TEST-ITEM",
				value_keys=[str(index) for index in range(201)],
				warehouse="TEST-WAREHOUSE",
			)
