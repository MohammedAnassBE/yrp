"""Single-partner assignment and live revocation using fictional, rolled-back masters."""
import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_retail.access import require_customer
from yrp.yrp_retail.logic import validate_assignment
from yrp.yrp_retail.setup import assigned_customers, validate_sales_person
from yrp.yrp_retail.tests.fixtures import customer, sales_person


class TestSalesPersonAssignment(unittest.TestCase):
	def setUp(self):
		self.point = "person_assignment_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.customer = customer()
		self.other = customer()
		self.person = sales_person(self.customer)

	def test_partner_required_for_leaf_but_not_tree_group(self):
		self.person.yrp_sales_partner = None
		self.person.set("yrp_customers", [])
		with self.assertRaisesRegex(frappe.ValidationError, "Select a Sales Partner"):
			validate_sales_person(self.person)
		group = frappe.get_doc({"doctype": "Sales Person", "is_group": 1})
		validate_sales_person(group)
		group.append("yrp_customers", {"customer": self.customer.name})
		with self.assertRaisesRegex(frappe.ValidationError, "individual Sales Persons"):
			validate_sales_person(group)

	def test_customers_must_match_selected_partner(self):
		self.person.append("yrp_customers", {"customer": self.other.name})
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			self.person.save()
		self.other.default_sales_partner = self.customer.default_sales_partner
		self.other.save()
		self.person.reload()
		self.person.append("yrp_customers", {"customer": self.other.name})
		self.person.save()
		self.assertEqual(assigned_customers(self.person.name), {self.customer.name, self.other.name})

	def test_duplicate_and_partner_change_are_rejected(self):
		self.person.append("yrp_customers", {"customer": self.customer.name})
		with self.assertRaisesRegex(frappe.ValidationError, "only be assigned once"):
			self.person.save()
		self.person.reload()
		self.person.yrp_sales_partner = self.other.default_sales_partner
		with self.assertRaisesRegex(frappe.ValidationError, "does not belong"):
			self.person.save()

	def test_customer_reassignment_revokes_api_even_with_stale_actor(self):
		require_customer(self.person, self.customer.name)
		self.customer.default_sales_partner = self.other.default_sales_partner
		self.customer.save()
		self.assertEqual(assigned_customers(self.person.name), set())
		with self.assertRaises(frappe.PermissionError):
			require_customer(self.person, self.customer.name)
		with self.assertRaises(frappe.ValidationError):
			validate_assignment(self.person.name, self.customer.name)

	def test_legacy_mismatched_assignments_do_not_grant_list_or_direct_read(self):
		# Restrict the initial Partner Type backfill to this test's new masters;
		# ordinary Contact hooks still generate the actual membership below.
		with patch("yrp.yrp_partner.backfill.sync_partner_type"):
			frappe.get_doc({"doctype": "YRP Partner Type",
				"partner_type_name": "Test Assignment " + frappe.generate_hash(length=10),
				"reference_doctype": "Sales Person"}).insert()
		user = frappe.get_doc({"doctype": "User", "first_name": "Fictional Sales Person",
			"email": frappe.generate_hash(length=12) + "@example.invalid",
			"send_welcome_email": 0, "roles": [{"role": "YRP Partner"}]}).insert()
		frappe.get_doc({"doctype": "Contact", "first_name": "Fictional Assignment",
			"user": user.name, "links": [{"link_doctype": "Sales Person", "link_name": self.person.name}]}).insert()
		retailer = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": "Fictional Owner",
			"shop_name": "Fictional Shop", "sales_person": self.person.name,
			"customer": self.customer.name}).insert()
		frappe.set_user(user.name)
		self.assertIn(self.customer.name, frappe.get_list("Customer", pluck="name"))
		self.assertIn(retailer.name, frappe.get_list("YRP Retailer", pluck="name"))
		self.assertTrue(frappe.has_permission("YRP Retailer", "read", retailer))
		frappe.set_user("Administrator")
		self.customer.default_sales_partner = self.other.default_sales_partner
		self.customer.save()
		frappe.set_user(user.name)
		self.assertNotIn(self.customer.name, frappe.get_list("Customer", pluck="name"))
		self.assertNotIn(retailer.name, frappe.get_list("YRP Retailer", pluck="name"))
		self.assertFalse(frappe.has_permission("YRP Retailer", "read", retailer))
		self.assertFalse(frappe.has_permission("Customer", "read", self.customer))
		# Model an imported stale child row after the Sales Person's partner is
		# changed outside normal validation. Queries must fail closed here too.
		frappe.set_user("Administrator")
		self.customer.default_sales_partner = self.person.yrp_sales_partner
		self.customer.save()
		frappe.db.set_value("Sales Person", self.person.name,
			"yrp_sales_partner", self.other.default_sales_partner)
		frappe.set_user(user.name)
		self.assertNotIn(self.customer.name, frappe.get_list("Customer", pluck="name"))
		self.assertNotIn(retailer.name, frappe.get_list("YRP Retailer", pluck="name"))
		self.assertFalse(frappe.has_permission("YRP Retailer", "read", retailer))
		with self.assertRaises(frappe.PermissionError):
			require_customer(self.person, self.customer.name)

	def test_disabled_salesperson_has_no_inherited_customer_scope(self):
		self.person.enabled = 0
		self.person.save()
		self.assertEqual(assigned_customers(self.person.name), set())
		with self.assertRaises(frappe.PermissionError):
			require_customer(self.person, self.customer.name)
