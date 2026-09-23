"""Permission integration tests use fictional masters and rollback all records."""
import unittest
from unittest.mock import patch

import frappe
from yrp.yrp_retail.tests.fixtures import customer, sales_person


class TestPartnerPermissions(unittest.TestCase):
	def setUp(self):
		self.point = "partner_perm_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.user = frappe.get_doc({
			"doctype": "User", "email": "scope-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Test Scoped Partner", "send_welcome_email": 0,
			"roles": [{"role": "YRP Partner"}],
		}).insert()
		self.partner_type = frappe.get_doc({
			"doctype": "YRP Partner Type", "partner_type_name": "Test Scope " + frappe.generate_hash(length=10),
			"reference_doctype": "YRP Retailer",
		}).insert()
		self.customer = customer()
		person = sales_person(self.customer)
		self.allowed = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": "Test Allowed", "shop_name": "Test Shop", "sales_person": person.name, "customer": self.customer.name}).insert()
		self.denied = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": "Test Unrelated", "shop_name": "Other Shop", "sales_person": person.name, "customer": self.customer.name}).insert()
		self.contact = frappe.get_doc({
			"doctype": "Contact", "first_name": "Test Scoped Contact", "user": self.user.name,
			"links": [{"link_doctype": "YRP Retailer", "link_name": self.allowed.name}],
		}).insert()

	def test_list_and_direct_read_match(self):
		frappe.set_user(self.user.name)
		self.assertEqual(frappe.get_list("YRP Retailer", pluck="name"), [self.allowed.name])
		self.assertTrue(frappe.has_permission("YRP Retailer", "read", self.allowed))
		self.assertFalse(frappe.has_permission("YRP Retailer", "read", self.denied))
		with self.assertRaises(frappe.PermissionError):
			self.denied.check_permission("read")

	def test_contact_scope_and_read_only_even_for_owner(self):
		frappe.db.set_value("Contact", self.contact.name, "owner", self.user.name)
		frappe.set_user(self.user.name)
		self.assertIn(self.contact.name, frappe.get_list("Contact", pluck="name"))
		self.contact.first_name = "Not allowed"
		with self.assertRaises(frappe.PermissionError):
			self.contact.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "Contact", "first_name": "Not allowed"}).insert()

	def test_removing_membership_denies_all_rows(self):
		self.contact.user = None
		self.contact.save()
		frappe.set_user(self.user.name)
		self.assertEqual(frappe.get_list("YRP Retailer", pluck="name"), [])
		self.assertFalse(frappe.has_permission("YRP Retailer", "read", self.allowed))

	def test_additional_edit_role_cannot_bypass_partner_read_only(self):
		self.user.append("roles", {"role": "YRP Retail User"})
		self.user.save()
		frappe.set_user(self.user.name)
		self.allowed.retailer_name = "Not allowed"
		with self.assertRaises(frappe.PermissionError):
			self.allowed.save()
		with self.assertRaises(frappe.PermissionError):
			self.allowed.save(ignore_permissions=True)

	def test_system_manager_bypasses_partner_scope(self):
		self.user.append("roles", {"role": "System Manager"})
		self.user.save()
		frappe.set_user(self.user.name)
		self.assertTrue(frappe.has_permission("YRP Retailer", "write", self.denied))
		self.assertIn(self.denied.name, frappe.get_list("YRP Retailer", pluck="name"))

	def test_processing_stays_read_only_with_sales_and_stock_roles(self):
		from yrp.yrp_partner import permissions

		for role in ("Sales Manager", "Stock Manager"):
			self.user.append("roles", {"role": role})
		self.user.save()
		frappe.set_user(self.user.name)
		# A salesperson-only installation has no direct source Link on these
		# documents. Extra operational roles must still never enable writes.
		with patch.object(permissions, "source_types", return_value={"Sales Person"}):
			for doctype in sorted(permissions.PROCESSING_DOCTYPES):
				with self.subTest(doctype=doctype):
					doc = frappe.new_doc(doctype)
					self.assertFalse(permissions.protected(doctype))
					for action in ("create", "write", "submit", "cancel", "delete"):
						self.assertFalse(permissions.has_permission(doc, action))
					self.assertFalse(frappe.has_permission(doctype, "create", doc))
					# Read/list scope remains the existing native permission policy.
					self.assertTrue(permissions.has_permission(doc, "read"))
					self.assertEqual(permissions.query_conditions(doctype=doctype), "")
					doc.flags.ignore_permissions = True
					for event in ("before_validate", "before_submit", "before_cancel", "before_update_after_submit", "on_trash"):
						with self.assertRaises(frappe.PermissionError):
							permissions.prevent_partner_write(doc, event)

	def test_processing_partner_restriction_preserves_administrator_exceptions(self):
		from yrp.yrp_partner import permissions

		self.user.append("roles", {"role": "System Manager"})
		self.user.save()
		for user in ("Administrator", self.user.name):
			frappe.set_user(user)
			for doctype in sorted(permissions.PROCESSING_DOCTYPES):
				with self.subTest(user=user, doctype=doctype):
					doc = frappe.new_doc(doctype)
					self.assertTrue(permissions.has_permission(doc, "write"))
					permissions.prevent_partner_write(doc, "before_validate")

	def test_manager_can_manage_sales_masters_but_not_employees(self):
		user = frappe.get_doc({
			"doctype": "User", "email": "manager-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Test Manager", "send_welcome_email": 0,
			"roles": [{"role": "YRP Partner Manager"}],
		}).insert()
		for doctype in ("Sales Person", "Customer", "YRP Retailer", "Contact", "Address"):
			for action in ("read", "create", "write"):
				self.assertTrue(frappe.has_permission(doctype, action, user=user.name), (doctype, action))
		self.assertFalse(frappe.has_permission("Employee", "write", user=user.name))

	def test_address_scope_and_native_panel(self):
		country = frappe.get_all("Country", pluck="name", limit=1)[0]
		address = frappe.get_doc({
			"doctype": "Address", "address_title": "Test Partner Address", "address_type": "Shipping",
			"address_line1": "1 Fictional Street", "city": "Test City", "country": country,
			"links": [{"link_doctype": "YRP Retailer", "link_name": self.allowed.name}],
		}).insert()
		self.allowed.run_method("onload")
		self.assertIn(address.name, [row.name for row in self.allowed.get_onload().addr_list])
		frappe.set_user(self.user.name)
		self.assertIn(address.name, frappe.get_list("Address", pluck="name"))
		address.city = "Not allowed"
		with self.assertRaises(frappe.PermissionError):
			address.save()
