"""Retail source accounting tests with fictional native records and rollback."""
import unittest
from unittest.mock import patch

import frappe
from frappe.utils import add_days, getdate, today

from yrp.yrp_retail import api, sales_sources
from yrp.yrp_retail.test_retail_flow import TestRetailFlow


class TestSourceQuantityPolicy(unittest.TestCase):
	def test_capacity_finite_positive_and_float_noise(self):
		with patch.object(sales_sources.frappe, "throw", side_effect=ValueError), patch("yrp.yrp_retail.logic._", side_effect=lambda value: value):
			for qty in (0, -1, float("nan"), float("inf"), 5.01):
				with self.subTest(qty=qty), self.assertRaises(ValueError):
					sales_sources._check_quantity(qty, 10, 5)
			self.assertEqual(sales_sources._check_quantity(5, 10, 5), 5)
			self.assertEqual(sales_sources._check_quantity(0.1 + 0.2, 0.3, 0), 0.1 + 0.2)

	def test_source_factor_matches_current_native_item_conversion(self):
		item = frappe._dict(stock_uom="Unit", uoms=[frappe._dict(uom="Case", conversion_factor=12)])
		source = frappe._dict(doctype="YRP Retail Order")
		row = frappe._dict(item_code="Fictional", uom="Case", conversion_factor=12)
		with patch.object(sales_sources.frappe, "get_doc", return_value=item), patch.object(sales_sources.frappe, "throw", side_effect=ValueError), patch("yrp.yrp_retail.logic._", side_effect=lambda value: value):
			self.assertEqual(sales_sources._factor(source, row), 12)
			row.conversion_factor = 6
			with self.assertRaises(ValueError):
				sales_sources._factor(source, row)
			row.uom = "Unknown"
			with self.assertRaises(ValueError):
				sales_sources._factor(source, row)


class TestSalesSources(TestRetailFlow):
	def setUp(self):
		super().setUp()
		self.primary = api.create_retail_order(self.visit(secondary=False), self.items(10))["name"]
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=8)
		self.company = frappe.get_doc({
			"doctype": "Company", "company_name": "Test Source Company " + suffix,
			"abbr": "SC" + suffix[:3], "country": "India", "default_currency": "INR",
			"chart_of_accounts": "Standard", "enable_perpetual_inventory": 0,
		}).insert()
		year = getdate(today()).year
		frappe.get_doc({"doctype": "Fiscal Year", "year": self.label("Source Fiscal Year"),
			"year_start_date": f"{year}-01-01", "year_end_date": f"{year}-12-31",
			"companies": [{"company": self.company.name}]}).insert()
		self.price_list = frappe.get_doc({
			"doctype": "Price List", "price_list_name": self.label("Source Prices"),
			"enabled": 1, "selling": 1, "currency": "INR",
		}).insert()
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name,
			"price_list": self.price_list.name, "price_list_rate": 20, "uom": self.uom.name}).insert()

	def make_so(self, qty=None, source=None, doctype="YRP Retail Order"):
		source = source or self.primary
		doc = frappe.get_doc(doctype, source)
		selection = [{"source_row": doc.items[0].name, "qty": qty}] if qty is not None else None
		result = sales_sources.make_sales_order(doctype, source, self.company.name,
			add_days(today(), 7), items=selection, selling_price_list=self.price_list.name)
		return frappe.get_doc("Sales Order", result["name"])

	def source_progress(self):
		return frappe.get_doc("YRP Retail Order", self.primary)

	def test_drafts_reserve_remaining_quantity_and_delete_releases(self):
		first = self.make_so(qty=4)
		self.assertEqual(first.docstatus, 0)
		self.assertEqual(first.items[0].rate, 20)
		self.assertEqual(first.yrp_retail_order, self.primary)
		source = self.source_progress()
		self.assertEqual(source.items[0].ordered_qty, 4)
		self.assertEqual(source.ordered_qty, 4)
		self.assertEqual(source.per_ordered, 40)
		with self.assertRaises(frappe.ValidationError):
			self.make_so(qty=7)
		second = self.make_so()
		self.assertEqual(second.items[0].qty, 6)
		self.assertEqual(self.source_progress().per_ordered, 100)
		first.delete()
		self.assertEqual(self.source_progress().ordered_qty, 6)
		self.assertEqual(self.make_so().items[0].qty, 4)

	def test_edit_excludes_self_and_enforces_combined_capacity(self):
		first = self.make_so(qty=3)
		self.make_so(qty=4)
		first.items[0].qty = 6
		first.save()
		self.assertEqual(self.source_progress().ordered_qty, 10)
		first.items[0].qty = 7
		with self.assertRaises(frappe.ValidationError):
			first.save()

	def test_submit_cancel_releases_capacity(self):
		order = self.make_so(qty=10)
		order.submit()
		self.assertEqual(self.source_progress().ordered_qty, 10)
		order.cancel()
		self.assertEqual(self.source_progress().ordered_qty, 0)
		self.assertEqual(self.make_so().items[0].qty, 10)

	def test_source_row_item_uom_and_header_tampering_rejected(self):
		order = self.make_so(qty=2)
		for field, value in (("yrp_retail_order_item", None), ("yrp_retail_summary_item", "forged"), ("conversion_factor", 2), ("qty", -1)):
			order.reload()
			order.items[0].set(field, value)
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				sales_sources.validate_sales_order(order)
		order.reload()
		order.customer = "Wrong Customer"
		with self.assertRaises(frappe.ValidationError):
			sales_sources.validate_sales_order(order)
		order.reload()
		order.yrp_retail_order = None
		with self.assertRaises(frappe.ValidationError):
			sales_sources.validate_sales_order(order)

	def test_used_primary_source_edits_blocked_then_unlocked(self):
		order = self.make_so(qty=2)
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.ValidationError):
			api.update_retail_order(self.primary, self.items(11))
		frappe.set_user("Administrator")
		order.delete()
		frappe.set_user(self.user.name)
		api.update_retail_order(self.primary, self.items(11))

	def test_partner_has_no_native_sales_order_write_bypass(self):
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			sales_sources.make_sales_order("YRP Retail Order", self.primary, self.company.name,
				add_days(today(), 7), selling_price_list=self.price_list.name)

	def test_secondary_requires_submitted_summary_and_uses_company_quantity(self):
		frappe.set_user(self.user.name)
		secondary = self.order(10)
		summary = api.create_summary([secondary], [{"item_code": self.item.name,
			"uom": self.uom.name, "customer_stock_qty": 3}])["name"]
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			self.make_so(source=secondary)
		with self.assertRaises(frappe.ValidationError):
			self.make_so(source=summary, doctype="YRP Retail Order Summary")
		frappe.set_user(self.user.name)
		api.submit_summary(summary)
		frappe.set_user("Administrator")
		order = self.make_so(source=summary, doctype="YRP Retail Order Summary")
		self.assertEqual(order.items[0].qty, 7)
		self.assertTrue(order.items[0].yrp_retail_summary_item)
		self.assertEqual(frappe.db.get_value("YRP Retail Order Summary", summary, "ordered_qty"), 7)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("YRP Retail Order Summary", summary).cancel()
		order.delete()
		frappe.get_doc("YRP Retail Order Summary", summary).cancel()

	def test_mapping_selection_rejects_forged_duplicate_and_extra_fields(self):
		row = self.source_progress().items[0].name
		for selected in ([{"source_row": "forged", "qty": 1}],
			[{"source_row": row, "qty": 1}, {"source_row": row, "qty": 1}],
			[{"source_row": row, "qty": 1, "rate": 0}]):
			with self.subTest(selected=selected), self.assertRaises(frappe.ValidationError):
				sales_sources.make_sales_order("YRP Retail Order", self.primary,
					self.company.name, add_days(today(), 7), items=selected,
					selling_price_list=self.price_list.name)

	def test_source_progress_is_server_computed(self):
		self.make_so(qty=3)
		source = self.source_progress()
		source.ordered_qty = 999
		source.per_ordered = 999
		source.items[0].ordered_qty = 999
		source.save()
		source.reload()
		self.assertEqual(source.ordered_qty, 3)
		self.assertEqual(source.per_ordered, 30)
		self.assertEqual(source.items[0].ordered_qty, 3)

	def test_mapping_failure_after_insert_rolls_back_draft_and_progress(self):
		from frappe.model.document import Document
		original = Document.insert
		def fail_after_insert(doc, *args, **kwargs):
			result = original(doc, *args, **kwargs)
			if doc.doctype == "Sales Order":
				raise RuntimeError("Synthetic failure after native insert")
			return result
		with patch.object(Document, "insert", new=fail_after_insert), self.assertRaises(RuntimeError):
			self.make_so(qty=4)
		self.assertEqual(frappe.db.count("Sales Order", {"yrp_retail_order": self.primary}), 0)
		self.assertEqual(self.source_progress().ordered_qty, 0)

	def test_native_update_items_on_submitted_order_preserves_source_capacity(self):
		from erpnext.controllers.accounts_controller import update_child_qty_rate

		order = self.make_so(qty=6)
		order.submit()
		self.make_so(qty=4)
		self.assertEqual(self.source_progress().ordered_qty, 10)
		row = order.items[0]

		def update_qty(qty):
			payload = [{"docname": row.name, "item_code": row.item_code,
				"qty": qty, "rate": row.rate, "uom": row.uom,
				"conversion_factor": row.conversion_factor,
				"delivery_date": row.delivery_date, "description": row.description}]
			update_child_qty_rate("Sales Order", frappe.as_json(payload), order.name)

		# Native Update Items writes the child before saving its parent. Mimic
		# HTTP failure rollback when catching the expected error in this test.
		point = "source_update_items_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "remaining retail source quantity"):
				update_qty(7)
		finally:
			frappe.db.rollback(save_point=point)
		order.reload()
		self.assertEqual(order.items[0].qty, 6)
		self.assertEqual(self.source_progress().ordered_qty, 10)

		update_qty(5)
		order.reload()
		self.assertEqual(order.docstatus, 1)
		self.assertEqual(order.items[0].qty, 5)
		self.assertEqual(order.items[0].yrp_retail_order_item, row.yrp_retail_order_item)
		source = self.source_progress()
		self.assertEqual(source.items[0].ordered_qty, 9)
		self.assertEqual(source.ordered_qty, 9)
		self.assertEqual(source.per_ordered, 90)

	def test_default_company_currency_does_not_leak_into_selected_company(self):
		from unittest.mock import patch
		original = frappe.new_doc
		def inherited_defaults(doctype, *args, **kwargs):
			doc = original(doctype, *args, **kwargs)
			if doctype == "Sales Order":
				doc.update({"currency": "USD", "conversion_rate": 0.5,
					"price_list_currency": "USD", "plc_conversion_rate": 0.5})
			return doc
		with patch.object(frappe, "new_doc", side_effect=inherited_defaults):
			order = self.make_so()
		self.assertEqual(order.currency, self.company.default_currency)
		self.assertEqual(order.conversion_rate, 1)
		self.assertEqual(order.items[0].rate, 20)
