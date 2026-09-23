"""Fictional end-to-end retail fixtures, rolled back after each test."""
import json
import secrets
import unittest

import frappe
from frappe.utils import now_datetime

from yrp.yrp_retail import api
from yrp.yrp_retail.access import allow_write, salesperson
from yrp.yrp_retail.tests.fixtures import sales_partner


class TestRetailFlow(unittest.TestCase):
	def setUp(self):
		self.point = "retail_flow_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert()
		frappe.db.set_single_value("YRP Retail Settings", "stock_uom", self.uom.name)
		group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		hsn = frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000)+10_000_000), "description": "Fictional retail test"}).insert()
		self.item = frappe.get_doc({"doctype": "Item", "item_code": self.label("Item"), "item_name": "Fictional Sale Item",
			"item_group": group.name, "stock_uom": self.uom.name, "is_stock_item": 0, "is_sales_item": 1, "gst_hsn_code": hsn.name}).insert()
		customer_group = frappe.get_doc({"doctype": "Customer Group", "customer_group_name": self.label("Customer Group"), "parent_customer_group": frappe.db.get_value("Customer Group", {"lft": 1}, "name"), "is_group": 0}).insert().name
		territory = frappe.get_doc({"doctype": "Territory", "territory_name": self.label("Territory"), "parent_territory": frappe.db.get_value("Territory", {"lft": 1}, "name"), "is_group": 0}).insert().name
		self.sales_partner = sales_partner(territory)
		self.customer = frappe.get_doc({"doctype": "Customer", "customer_name": self.label("Customer"),
			"customer_type": "Individual", "customer_group": customer_group, "territory": territory,
			"default_sales_partner": self.sales_partner.name}).insert()
		self.person = frappe.get_doc({"doctype": "Sales Person", "sales_person_name": self.label("Person"), "enabled": 1,
			"parent_sales_person": frappe.db.get_value("Sales Person", {"lft": 1}, "name"),
			"yrp_sales_partner": self.sales_partner.name,
			"yrp_customers": [{"customer": self.customer.name}]}).insert()
		self.user = frappe.get_doc({"doctype": "User", "email": "retail-"+frappe.generate_hash(length=12)+"@example.invalid",
			"first_name": "Fictional Salesperson", "send_welcome_email": 0, "roles": [{"role": "YRP Partner"}]}).insert()
		self.partner_type = frappe.get_doc({"doctype": "YRP Partner Type", "partner_type_name": self.label("Partner Type"), "reference_doctype": "Sales Person"}).insert()
		frappe.get_doc({"doctype": "Contact", "first_name": self.label("Contact"), "user": self.user.name,
			"links": [{"link_doctype": "Sales Person", "link_name": self.person.name}]}).insert()
		frappe.set_user(self.user.name)

	def label(self, label):
		return "Test "+label+" "+frappe.generate_hash(length=10)

	def retailer(self):
		return api.create_retailer(self.label("Retailer"), self.customer.name, shop_name=self.label("Shop"))["name"]

	def visit(self, secondary=True):
		return api.create_visit("Secondary" if secondary else "Primary", str(now_datetime()), 12.5, 77.5,
			customer=self.customer.name, retailer=self.retailer() if secondary else None)["name"]

	def items(self, qty=10):
		return [{"item_code": self.item.name, "qty": qty, "uom": self.uom.name}]

	def order(self, qty=10):
		return api.create_retail_order(self.visit(), self.items(qty))["name"]

	def test_primary_and_secondary_visits_and_actor_binding(self):
		for secondary in (False, True):
			doc = frappe.get_doc("YRP Visit", self.visit(secondary))
			self.assertEqual(doc.sales_person, self.person.name)
			self.assertEqual(doc.customer, self.customer.name)
			self.assertEqual(bool(doc.retailer), secondary)

	def test_missing_target_and_invalid_location_are_rejected(self):
		for kwargs in ({"visit_type": "Primary", "customer": None, "latitude": 1, "longitude": 2},
			{"visit_type": "Secondary", "customer": self.customer.name, "latitude": 1, "longitude": 2},
			{"visit_type": "Primary", "customer": self.customer.name, "latitude": 100, "longitude": 2}):
			with self.assertRaises(frappe.ValidationError):
				api.create_visit(visit_datetime=str(now_datetime()), **kwargs)

	def test_create_order_updates_visit_and_rejects_duplicate(self):
		visit = self.visit()
		name = api.create_retail_order(visit, self.items(7))["name"]
		self.assertEqual(frappe.db.get_value("YRP Visit", visit, "has_order"), 1)
		self.assertEqual(frappe.get_doc("YRP Retail Order", name).items[0].stock_qty, 7)
		with self.assertRaises(frappe.UniqueValidationError):
			api.create_retail_order(visit, self.items())
		self.assertEqual(frappe.db.count("YRP Retail Order", {"visit": visit}), 1)

	def test_summary_aggregates_and_locks_sources(self):
		orders = [self.order(10), self.order(5)]
		name = api.create_summary(orders, [{"item_code": self.item.name, "uom": self.uom.name, "customer_stock_qty": 4}])["name"]
		summary = frappe.get_doc("YRP Retail Order Summary", name)
		self.assertEqual(summary.items[0].requested_qty, 15)
		self.assertEqual(summary.items[0].company_qty, 11)
		self.assertEqual({frappe.db.get_value("YRP Retail Order", row, "summary") for row in orders}, {name})
		with self.assertRaises(frappe.ValidationError):
			api.update_retail_order(orders[0], self.items(20))
		with self.assertRaises(frappe.ValidationError):
			api.create_summary(orders)
		api.update_summary(name, [{"item_code": self.item.name, "uom": self.uom.name, "customer_stock_qty": 5}])
		self.assertEqual(api.submit_summary(name)["docstatus"], 1)
		self.assertEqual(frappe.db.get_value("YRP Retail Order Summary", name, "total_company_qty"), 10)

	def test_excess_stock_rolls_back_without_claiming_orders(self):
		order = self.order(2)
		before = frappe.db.count("YRP Retail Order Summary")
		with self.assertRaises(frappe.ValidationError):
			api.create_summary([order], [{"item_code": self.item.name, "uom": self.uom.name, "customer_stock_qty": 3}])
		self.assertFalse(frappe.db.get_value("YRP Retail Order", order, "summary"))
		self.assertEqual(frappe.db.count("YRP Retail Order Summary"), before)

	def test_invalid_quantities_and_injected_fields_fail(self):
		visit = self.visit()
		for qty in (0, -1, float("nan"), float("inf")):
			with self.assertRaises((frappe.ValidationError, ValueError)):
				api.create_retail_order(visit, self.items(qty))
		rows = self.items()
		rows[0]["flags"] = {"ignore_permissions": True}
		with self.assertRaises(frappe.ValidationError):
			api.create_retail_order(visit, rows)

	def test_revoked_assignment_and_wrong_actor_fail(self):
		with self.assertRaises(frappe.PermissionError):
			api.create_retailer("Forged", self.customer.name, sales_person="Not my salesperson")
		frappe.set_user("Administrator")
		self.person.set("yrp_customers", [])
		self.person.save()
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			api.create_retailer("Revoked", self.customer.name)

	def test_raw_save_and_nested_scope_cannot_bypass_permissions(self):
		name = self.retailer()
		doc = frappe.get_doc("YRP Retailer", name)
		doc.retailer_name = "Forbidden"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		actor = salesperson()
		with allow_write(doc, actor):
			other = frappe.get_doc("YRP Retailer", name)
			with self.assertRaises(frappe.PermissionError):
				other.save(ignore_permissions=True)

	def test_customer_assignment_is_visible_and_guest_is_denied(self):
		self.assertIn(self.customer.name, frappe.get_list("Customer", pluck="name"))
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			api.create_retailer("No login", self.customer.name)

	def test_visit_time_is_frozen_and_order_date_is_derived(self):
		visit = self.visit()
		order = api.create_retail_order(visit, self.items())["name"]
		from frappe.utils import getdate, add_days
		self.assertEqual(getdate(frappe.db.get_value("YRP Retail Order", order, "order_date")), getdate(frappe.db.get_value("YRP Visit", visit, "visit_datetime")))
		frappe.set_user("Administrator")
		doc = frappe.get_doc("YRP Visit", visit)
		doc.visit_datetime = add_days(doc.visit_datetime, 1)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_disabled_retailer_cannot_receive_visits(self):
		retailer = self.retailer()
		frappe.db.set_value("YRP Retailer", retailer, "disabled", 1)
		with self.assertRaises(frappe.ValidationError):
			api.create_visit("Secondary", str(now_datetime()), 12.5, 77.5, retailer=retailer)

	def test_whole_uom_rejects_fractional_summary_allocation(self):
		frappe.db.set_value("UOM", self.uom.name, "must_be_whole_number", 1)
		order = self.order(10)
		with self.assertRaises(frappe.ValidationError):
			api.create_summary([order], [{"item_code": self.item.name, "uom": self.uom.name, "customer_stock_qty": 0.5}])
		self.assertFalse(frappe.db.get_value("YRP Retail Order", order, "summary"))

	def test_cancelled_summary_releases_orders(self):
		order = self.order()
		name = api.create_summary([order])["name"]
		api.submit_summary(name)
		frappe.set_user("Administrator")
		frappe.get_doc("YRP Retail Order Summary", name).cancel()
		self.assertFalse(frappe.db.get_value("YRP Retail Order", order, "summary"))
		frappe.set_user(self.user.name)
		self.assertTrue(api.create_summary([order])["name"])

	def test_retailer_creation_supports_partner_generation(self):
		frappe.set_user("Administrator")
		type_doc = frappe.get_doc({"doctype": "YRP Partner Type", "partner_type_name": self.label("Retailer Type"), "reference_doctype": "YRP Retailer"}).insert()
		frappe.set_user(self.user.name)
		name = self.retailer()
		self.assertTrue(frappe.db.exists("YRP Partner", {"partner_type": type_doc.name, "reference_name": name}))

	def test_second_sales_person_can_visit_shared_customer_retailer(self):
		frappe.set_user("Administrator")
		second = frappe.get_doc({"doctype": "Sales Person", "sales_person_name": self.label("Second Person"), "enabled": 1,
			"parent_sales_person": frappe.db.get_value("Sales Person", {"lft": 1}, "name"),
			"yrp_sales_partner": self.sales_partner.name,
			"yrp_customers": [{"customer": self.customer.name}]}).insert()
		frappe.set_user(self.user.name)
		retailer = self.retailer()
		from yrp.yrp_retail.logic import validate_assignment
		validate_assignment(second.name, self.customer.name, retailer)
		frappe.set_user(self.user.name)
