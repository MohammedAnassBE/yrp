"""Deterministic policy tests; no stock/accounting documents or database writes."""

import unittest
from unittest.mock import Mock, patch

import frappe

from yrp.yrp_retail import pricing


class Row(frappe._dict):
	def set(self, key, value):
		self[key] = value


class TestRetailPricing(unittest.TestCase):
	def setUp(self):
		mutex = patch.object(pricing, "lock_pricing_policy")
		mutex.start()
		self.addCleanup(mutex.stop)
		self.errors = patch.object(pricing.frappe, "throw", side_effect=ValueError)
		self.errors.start()
		self.translate = patch.object(pricing, "_", side_effect=lambda value: value)
		self.translate.start()
		self.addCleanup(self.errors.stop)
		self.addCleanup(self.translate.stop)

	def document(self, doctype="Sales Order", code="MANAGED", rate=10, **kwargs):
		row = Row(item_code=code, qty=1, **{field: rate for field in pricing.EFFECTIVE_RATES})
		row.update(kwargs)
		return Row(doctype=doctype, items=[row])

	def test_rate_policy_matrix(self):
		for free in (False, True):
			for value in (None, "bad", float("nan"), float("inf"), -float("inf"), -1, 0, 0.00001, 10):
				with self.subTest(free=free, value=value):
					valid = (value == 0) if free else isinstance(value, (int, float)) and 0 < value < float("inf")
					if valid:
						pricing.check_rate(value, free, "test")
					else:
						with self.assertRaises(ValueError):
							pricing.check_rate(value, free, "test")

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": True})
	def test_free_rows_clear_every_pricing_input(self, managed):
		for doctype in pricing.SALES_DOCTYPES:
			doc = self.document(doctype, **{field: 42 for field in pricing.FREE_ZERO_FIELDS if field not in pricing.EFFECTIVE_RATES})
			pricing.prepare_free_rows(doc)
			for field in pricing.FREE_ZERO_FIELDS:
				self.assertEqual(doc.get("items")[0][field], 0, field)
			self.assertEqual(doc.get("items")[0].is_free_item, 1)
			pricing.validate_sales_document(doc)

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": False})
	def test_nonfree_every_effective_rate_checked(self, managed):
		for field in pricing.EFFECTIVE_RATES:
			for invalid in (0, -1, float("nan")):
				with self.subTest(field=field, invalid=invalid):
					doc = self.document()
					doc.get("items")[0][field] = invalid
					with self.assertRaises(ValueError):
						pricing.validate_sales_document(doc)

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": False})
	def test_positive_rate_negative_quantity_return_allowed(self, managed):
		doc = self.document(qty=-2)
		doc.is_return = 1
		pricing.validate_sales_document(doc)

	@patch.object(pricing, "_managed_items", return_value={})
	def test_unmanaged_zero_rate_unchanged(self, managed):
		doc = self.document(rate=0)
		before = dict(doc.get("items")[0])
		pricing.prepare_free_rows(doc)
		pricing.validate_sales_document(doc)
		self.assertEqual(dict(doc.get("items")[0]), before)

	@patch.object(pricing, "_managed_items")
	def test_purchase_documents_untouched(self, managed):
		doc = self.document("Purchase Invoice", rate=-1)
		pricing.prepare_free_rows(doc)
		pricing.validate_sales_document(doc)
		managed.assert_not_called()

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": True})
	def test_mixin_wraps_native_recalculation(self, managed):
		class Native:
			def calculate_taxes_and_totals(self):
				self.seen_native_rate = self.get("items")[0].rate
				self.get("items")[0].net_rate = self.get("items")[0].rate
				return "native-result"

		class Combined(pricing.RetailSalesPricingMixin, Native, Row):
			pass

		doc = Combined(self.document(rate=99))
		self.assertEqual(doc.calculate_taxes_and_totals(), "native-result")
		self.assertEqual(doc.seen_native_rate, 0)

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": False})
	def test_mixin_rejects_native_discount_to_zero(self, managed):
		class Native:
			def calculate_taxes_and_totals(self):
				self.get("items")[0].net_rate = 0

		class Combined(pricing.RetailSalesPricingMixin, Native, Row):
			pass

		with self.assertRaises(ValueError):
			Combined(self.document()).calculate_taxes_and_totals()

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": True})
	def test_final_guard_rejects_later_free_rate_mutation(self, managed):
		doc = self.document(rate=0)
		doc.get("items")[0].base_net_rate = 1
		with self.assertRaises(ValueError):
			pricing.validate_sales_document(doc)

	@patch.object(pricing, "_managed_items", return_value={"MANAGED": False})
	def test_item_price_uses_master_selling_flag(self, managed):
		doc = Row(price_list="Prices", item_code="MANAGED", price_list_rate=0, selling=0)
		with patch.object(pricing.frappe, "db", Mock(get_value=Mock(return_value=1))):
			with self.assertRaises(ValueError):
				pricing.validate_item_price(doc)
		with patch.object(pricing.frappe, "db", Mock(get_value=Mock(return_value=0))):
			pricing.validate_item_price(doc)

	@patch.object(pricing, "_validate_existing_prices")
	def test_item_flag_validates_existing_prices(self, validate):
		pricing.validate_item_free_flag(Row(name="A", yrp_item_master_template="T", yrp_is_free_item=1))
		validate.assert_called_once_with(["A"], True)

	@patch.object(pricing, "_validate_existing_prices")
	def test_unmanaged_item_flag_does_not_check_prices(self, validate):
		pricing.validate_item_free_flag(Row(name="A", yrp_is_free_item=1))
		validate.assert_not_called()

	def test_existing_prices_respect_selling_master_and_free_flag(self):
		prices = [Row(name="P", item_code="A", price_list="Selling", price_list_rate=5), Row(name="B", item_code="A", price_list="Buying", price_list_rate=-10)]
		with patch.object(pricing, "_current_rows", side_effect=[prices, ["Selling"]]):
			pricing._validate_existing_prices(["A"], False)
		with patch.object(pricing, "_current_rows", side_effect=[prices, ["Selling"]]):
			with self.assertRaises(ValueError):
				pricing._validate_existing_prices(["A"], True)

	def test_template_flag_checks_every_linked_item(self):
		doc = Row(name="T", is_free_item=1, is_new=lambda: False)
		with patch.object(pricing, "_current_rows", return_value=["A", "B"]), patch.object(pricing, "_validate_existing_prices") as validate:
			pricing.validate_template_free_flag(doc)
			validate.assert_called_once_with(["A", "B"], True)

	def test_price_list_conversion_rejects_invalid_existing_prices(self):
		doc = Row(name="Buying", selling=1, is_new=lambda: False)
		with patch.object(pricing, "_current_rows", return_value=[Row(name="P", item_code="A", price_list_rate=0)]), patch.object(pricing, "_managed_items", return_value={"A": False}):
			with self.assertRaises(ValueError):
				pricing.validate_price_list(doc)

	@patch.object(pricing, "_managed_items", return_value={"FREE": True, "PAID": False})
	def test_mixed_rows_preserve_paid_and_legacy_pricing(self, managed):
		doc = self.document(code="FREE", rate=75)
		paid = self.document(code="PAID", rate=12).get("items")[0]
		legacy = self.document(code="OLD", rate=0).get("items")[0]
		doc.get("items").extend([paid, legacy])
		pricing.prepare_free_rows(doc)
		pricing.validate_sales_document(doc)
		self.assertEqual(paid.rate, 12)
		self.assertEqual(legacy.rate, 0)
		self.assertEqual(doc.get("items")[0].rate, 0)

	def test_managed_item_lookup_is_scoped_and_deduplicated(self):
		with patch.object(pricing, "_current_rows", return_value=[Row(name="A", yrp_is_free_item=1)]) as query:
			self.assertEqual(pricing._managed_items(["A", "A", None]), {"A": True})
			self.assertEqual(query.call_args.kwargs["filters"], {"name": ["in", ["A"]], "yrp_item_master_template": ["is", "set"]})

	def test_policy_reads_request_current_locked_rows(self):
		db = Mock()
		db.get_values.return_value = []
		with patch.object(pricing.frappe, "db", db):
			pricing._current_rows("Item", filters={"name": "A"}, fields=["name"])
		self.assertTrue(db.get_values.call_args.kwargs["for_update"])
		self.assertEqual(db.get_values.call_args.kwargs["order_by"], "name")
