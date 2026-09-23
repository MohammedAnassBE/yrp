"""Exercise native Retailer documents, Contact panels, and role boundaries."""
import unittest

import frappe
from yrp.yrp_retail.tests.fixtures import customer, sales_person


class TestYRPRetailer(unittest.TestCase):
	def setUp(self):
		self.point = "retail_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.original_user = frappe.session.user
		self.addCleanup(frappe.set_user, self.original_user)

	def user(self, role):
		return frappe.get_doc({
			"doctype": "User", "email": "retail-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Test Retail User", "send_welcome_email": 0,
			"roles": [{"role": role}],
		}).insert()

	def test_retail_user_creates_retailer_and_linked_contact(self):
		party = customer()
		person = sales_person(party)
		user = self.user("YRP Retail User")
		frappe.set_user(user.name)
		retailer = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": "Test Retailer", "shop_name": "Test Shop", "sales_person": person.name, "customer": party.name}).insert()
		contact = frappe.get_doc({
			"doctype": "Contact", "first_name": "Test Retail Contact",
			"links": [{"link_doctype": "YRP Retailer", "link_name": retailer.name}],
		}).insert()
		retailer.run_method("onload")
		self.assertIn(contact.name, [row.name for row in retailer.get_onload().contact_list])

	def test_partner_login_cannot_manage_configuration_or_retailers(self):
		user = self.user("YRP Partner")
		for doctype in ("YRP Partner Type", "YRP Partner", "YRP Retailer", "Employee"):
			self.assertFalse(frappe.has_permission(doctype, "create", user=user.name))

	def test_manager_can_configure_but_cannot_manually_create_partner(self):
		user = self.user("YRP Partner Manager")
		self.assertTrue(frappe.has_permission("YRP Partner Type", "create", user=user.name))
		self.assertTrue(frappe.has_permission("YRP Retailer", "write", user=user.name))
		self.assertFalse(frappe.has_permission("YRP Partner", "create", user=user.name))
		self.assertFalse(frappe.has_permission("Employee", "write", user=user.name))

	def test_source_forms_have_contact_panels(self):
		for doctype in ("Sales Person", "Employee", "YRP Retailer"):
			self.assertEqual(frappe.get_meta(doctype).get_field("contact_html").fieldtype, "HTML")
