"""Sales Partner draft YRP Sales Order actions without a registered action extension."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner import sales_roles
from yrp.yrp_retail.tests.fixtures import customer, sales_partner
from yrp.yrp_sales.doctype.yrp_sales_order import test_yrp_sales_order as order_fixtures


class TestSalesPartnerRole(unittest.TestCase):
	label = order_fixtures.TestYRPSalesOrder.label

	def setUp(self):
		order_fixtures.TestYRPSalesOrder.setUp(self)
		# Test yrp's own rule; an installed app may register a stricter extension.
		self.enterContext(patch.object(sales_roles, "extension_action", return_value=None))
		self.partner = sales_partner(self.customer.territory)
		self.customer.default_sales_partner = self.partner.name
		self.customer.append("accounts", {"company": self.company})
		self.customer.save()
		self.other_customer = customer()
		self.other_customer.append("accounts", {"company": self.company})
		self.other_customer.save()
		with patch("yrp.yrp_partner.backfill.sync_partner_type"):
			frappe.get_doc(
				{
					"doctype": "YRP Partner Type",
					"reference_doctype": "Sales Partner",
					"partner_type_name": self.label("ZZ Sales Partner Role"),
				}
			).insert()
		self.user = self.make_user("YRP Sales Partner")
		self.contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": self.label("ZZ Partner"),
				"email_ids": [{"email_id": self.user.email, "is_primary": 1}],
				"links": [{"link_doctype": "Sales Partner", "link_name": self.partner.name}],
			}
		).insert()

	def make_user(self, *roles):
		return frappe.get_doc(
			{
				"doctype": "User",
				"send_welcome_email": 0,
				"email": "zz-sales-partner-" + frappe.generate_hash(length=12) + "@example.invalid",
				"first_name": "ZZ Sales Partner",
				"roles": [{"role": role} for role in ("YRP Partner", *roles)],
			}
		).insert()

	def order(self, party=None):
		return frappe.get_doc(
			{
				"doctype": "YRP Sales Order",
				"customer": party or self.customer.name,
				"company": self.company,
				"delivery_date": frappe.utils.add_days(frappe.utils.today(), 3),
				"selling_price_list": self.price_list.name,
				"items": [{"item_code": self.item.name, "uom": self.uom.name, "qty": 4}],
			}
		)

	def add_sales_role(self):
		frappe.set_user("Administrator")
		self.user.append("roles", {"role": "Sales User"})
		self.user.save()
		frappe.set_user(self.user.name)

	def test_partner_creates_and_edits_own_draft_order(self):
		frappe.set_user(self.user.name)
		order = self.order().insert()
		order.items[0].qty = 3
		order.save()
		self.assertEqual(frappe.get_doc("YRP Sales Order", order.name).items[0].qty, 3)
		self.assertIn(order.name, frappe.get_list("YRP Sales Order", pluck="name"))

	def test_unrelated_customer_order_is_rejected(self):
		self.add_sales_role()
		for bypass in (False, True):
			with self.subTest(bypass=bypass), self.assertRaises(frappe.PermissionError):
				self.order(self.other_customer.name).insert(ignore_permissions=bypass)

	def test_unrelated_existing_order_cannot_be_claimed_by_rewriting_header(self):
		other = self.order(self.other_customer.name).insert()
		self.add_sales_role()
		self.assertNotIn(other.name, frappe.get_list("YRP Sales Order", pluck="name"))
		self.assertFalse(frappe.has_permission("YRP Sales Order", "read", other))
		other.customer = self.customer.name
		with self.assertRaises(frappe.PermissionError):
			other.save(ignore_permissions=True)

	def test_additive_roles_and_bypass_do_not_enable_lifecycle_actions(self):
		draft = self.order().insert()
		submitted = self.order().insert()
		submitted.submit()
		self.add_sales_role()
		for action, name in (("submit", draft.name), ("delete", draft.name), ("cancel", submitted.name)):
			for bypass in (False, True):
				with self.subTest(action=action, bypass=bypass):
					doc = frappe.get_doc("YRP Sales Order", name)
					self.assertFalse(frappe.has_permission("YRP Sales Order", action, doc))
					doc.flags.ignore_permissions = bypass
					with self.assertRaises(frappe.PermissionError):
						if action == "delete":
							doc.delete(ignore_permissions=bypass)
						else:
							getattr(doc, action)()
		self.assertEqual(frappe.db.get_value("YRP Sales Order", draft.name, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("YRP Sales Order", submitted.name, "docstatus"), 1)

	def test_draft_discard_cannot_bypass_lifecycle_permissions(self):
		draft = self.order().insert()
		self.add_sales_role()
		for bypass in (False, True):
			with self.subTest(bypass=bypass):
				doc = frappe.get_doc("YRP Sales Order", draft.name)
				doc.flags.ignore_permissions = bypass
				with self.assertRaises(frappe.PermissionError):
					doc.discard()
				self.assertEqual(frappe.db.get_value("YRP Sales Order", draft.name, "docstatus"), 0)

	def test_missing_or_revoked_membership_grants_no_rows_or_creation(self):
		order = self.order().insert()
		unlinked = self.make_user("YRP Sales Partner", "Sales User")
		self.contact.set("email_ids", [])
		self.contact.save()
		for user in (unlinked.name, self.user.name):
			frappe.set_user(user)
			with self.subTest(user=user):
				self.assertEqual(frappe.get_list("YRP Sales Order", pluck="name"), [])
				self.assertFalse(frappe.has_permission("YRP Sales Order", "read", order))
				for bypass in (False, True):
					with self.assertRaises(frappe.PermissionError):
						self.order().insert(ignore_permissions=bypass)
