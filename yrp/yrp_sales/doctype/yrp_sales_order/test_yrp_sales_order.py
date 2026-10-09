# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import secrets
import unittest

import frappe
from frappe.utils import add_days, today


class TestYRPSalesOrder(unittest.TestCase):
	def setUp(self):
		point = "yrp_sales_order_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.addCleanup(frappe.db.rollback, save_point=point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert()
		group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		hsn = frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
			"description": "Fictional sales order test"}).insert()
		self.item = frappe.get_doc({"doctype": "Item", "item_code": self.label("Item"), "item_group": group.name,
			"stock_uom": self.uom.name, "is_stock_item": 0, "is_sales_item": 1, "gst_hsn_code": hsn.name}).insert()
		customer_group = frappe.get_doc({"doctype": "Customer Group", "customer_group_name": self.label("Customer Group"),
			"parent_customer_group": frappe.db.get_value("Customer Group", {"lft": 1}, "name"), "is_group": 0}).insert().name
		territory = frappe.get_doc({"doctype": "Territory", "territory_name": self.label("Territory"),
			"parent_territory": frappe.db.get_value("Territory", {"lft": 1}, "name"), "is_group": 0}).insert().name
		self.customer = frappe.get_doc({"doctype": "Customer", "customer_name": self.label("Customer"),
			"customer_type": "Individual", "customer_group": customer_group, "territory": territory}).insert()
		self.company = frappe.db.get_value("Company", {}, "name")
		self.price_list = frappe.get_doc({
			"doctype": "Price List", "price_list_name": self.label("Order Prices"),
			"enabled": 1, "selling": 1, "currency": "INR",
		}).insert()
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name,
			"price_list": self.price_list.name, "price_list_rate": 25, "uom": self.uom.name}).insert()

	def label(self, label):
		return "Test " + label + " " + frappe.generate_hash(length=10)

	def make_order(self, **row):
		return frappe.get_doc({
			"doctype": "YRP Sales Order", "customer": self.customer.name, "company": self.company,
			"delivery_date": add_days(today(), 3), "selling_price_list": self.price_list.name,
			"items": [{"item_code": self.item.name, "uom": self.uom.name, "qty": 4, **row}],
		}).insert()

	def test_price_list_rate_totals_and_stock_quantity(self):
		order = self.make_order()
		row = order.items[0]
		self.assertEqual((row.rate, row.amount, row.stock_qty, row.conversion_factor), (25, 100, 4, 1))
		self.assertEqual((order.total_qty, order.total, order.status), (4, 100, "Draft"))

	def test_zero_rate_needs_a_free_item(self):
		frappe.db.delete("Item Price", {"item_code": self.item.name})
		with self.assertRaisesRegex(frappe.ValidationError, "greater than zero"):
			self.make_order(rate=0)
		frappe.db.set_value("Item", self.item.name, "yrp_is_free_item", 1)
		self.assertEqual(self.make_order(rate=0).items[0].amount, 0)

	def test_rows_need_enabled_concrete_sales_items(self):
		for field in ("disabled", "has_variants"):
			with self.subTest(field=field):
				frappe.db.set_value("Item", self.item.name, {"disabled": 0, "has_variants": 0, "is_sales_item": 1, field: 1})
				with self.assertRaisesRegex(frappe.ValidationError, "enabled, concrete sales Items"):
					self.make_order()
		frappe.db.set_value("Item", self.item.name, {"has_variants": 0, "is_sales_item": 0})
		with self.assertRaisesRegex(frappe.ValidationError, "enabled, concrete sales Items"):
			self.make_order()

	def test_submit_and_cancel_status(self):
		order = self.make_order()
		order.submit()
		self.assertEqual(order.status, "To Deliver")
		order.cancel()
		self.assertEqual(frappe.db.get_value("YRP Sales Order", order.name, "status"), "Cancelled")

	def test_quantity_and_delivery_date_are_validated(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_order(qty=0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "YRP Sales Order", "customer": self.customer.name, "company": self.company,
				"transaction_date": today(), "delivery_date": add_days(today(), -1),
				"items": [{"item_code": self.item.name, "uom": self.uom.name, "qty": 1, "rate": 5}],
			}).insert()

	def test_read_only_roles_cannot_change(self):
		for role in ["Stock User", "Stock Manager", "Accounts User"]:
			user = frappe.get_doc({"doctype": "User", "email": frappe.generate_hash(length=10) + "@example.invalid",
				"first_name": "Fictional Reader", "send_welcome_email": 0, "roles": [{"role": role}]}).insert().name
			self.assertTrue(frappe.has_permission("YRP Sales Order", "read", user=user), role)
			for ptype in ("create", "write", "delete", "submit", "cancel"):
				self.assertFalse(frappe.has_permission("YRP Sales Order", ptype, user=user), (role, ptype))
