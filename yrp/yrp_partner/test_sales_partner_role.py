"""Action-role boundaries use fictional native documents and rollback fixtures."""
import json
import unittest

import frappe
from frappe.utils import add_days, today

from yrp.yrp_retail import test_packing as packing_fixtures
from yrp.yrp_retail.tests.fixtures import customer, sales_partner


class TestSalesPartnerRole(unittest.TestCase):
	delivery_note = packing_fixtures.TestPacking.delivery_note
	carton = packing_fixtures.TestPacking.carton

	def setUp(self):
		# Reuse the native non-stock fixture without inheriting unrelated tests.
		packing_fixtures.TestPacking.setUp(self)
		self.partner = sales_partner(self.customer.territory)
		self.customer.default_sales_partner = self.partner.name
		self.customer.append("accounts", {"company": self.company.name})
		self.customer.save()
		self.other_customer = customer()
		self.other_partner = self.other_customer.default_sales_partner
		frappe.get_doc({"doctype": "YRP Partner Type",
			"partner_type_name": "Test Sales Partner Role " + frappe.generate_hash(length=10),
			"reference_doctype": "Sales Partner"}).insert()
		self.user = self.make_user("YRP Sales Partner")
		self.contact = frappe.get_doc({"doctype": "Contact",
			"first_name": "Fictional Action Partner " + frappe.generate_hash(length=10),
			"user": self.user.name,
			"email_ids": [{"email_id": self.user.email, "is_primary": 1}],
			"links": [{"link_doctype": "Sales Partner", "link_name": self.partner.name}]}).insert()

	def make_user(self, *roles):
		return frappe.get_doc({"doctype": "User",
			"email": "sales-role-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Fictional Sales Partner", "send_welcome_email": 0,
			"roles": [{"role": role} for role in ("YRP Partner", *roles)]}).insert()

	def sales_order(self, party=None, partner=None):
		return frappe.get_doc({"doctype": "Sales Order", "company": self.company.name,
			"customer": party or self.customer.name,
			"sales_partner": partner or self.partner.name,
			"transaction_date": today(), "delivery_date": add_days(today(), 7),
			"currency": "USD", "conversion_rate": 1,
			"selling_price_list": self.price_list.name, "price_list_currency": "USD",
			"plc_conversion_rate": 1, "ignore_pricing_rule": 1,
			"items": [{"item_code": self.item.name, "qty": 4,
				"uom": self.uom.name, "stock_uom": self.uom.name,
				"conversion_factor": 1, "rate": 10, "price_list_rate": 10,
				"delivery_date": add_days(today(), 7)}]})

	def add_sales_role(self):
		frappe.set_user("Administrator")
		self.user.append("roles", {"role": "Sales User"})
		self.user.save()
		frappe.set_user(self.user.name)

	def test_partner_creates_and_edits_own_draft_sales_order(self):
		frappe.set_user(self.user.name)
		order = self.sales_order().insert()
		self.assertEqual(order.docstatus, 0)
		order.items[0].qty = 3
		order.save()
		self.assertEqual(frappe.get_doc("Sales Order", order.name).items[0].qty, 3)
		self.assertIn(order.name, frappe.get_list("Sales Order", pluck="name"))
		self.assertTrue(frappe.has_permission("Sales Order", "read", order))

	def test_unrelated_customer_and_forged_sales_partner_are_rejected(self):
		self.add_sales_role()
		for party, partner in (
			(self.other_customer.name, self.other_partner),
			(self.other_customer.name, self.partner.name),
			(self.customer.name, self.other_partner),
		):
			for bypass in (False, True):
				with self.subTest(customer=party, partner=partner, bypass=bypass):
					order = self.sales_order(party, partner)
					with self.assertRaises(frappe.PermissionError):
						order.insert(ignore_permissions=bypass)

	def test_unrelated_existing_order_cannot_be_claimed_by_rewriting_header(self):
		other = self.sales_order(self.other_customer.name, self.other_partner).insert()
		self.add_sales_role()
		self.assertNotIn(other.name, frappe.get_list("Sales Order", pluck="name"))
		self.assertFalse(frappe.has_permission("Sales Order", "read", other))
		with self.assertRaises(frappe.PermissionError):
			other.check_permission("read")
		other.customer = self.customer.name
		other.sales_partner = self.partner.name
		with self.assertRaises(frappe.PermissionError):
			other.save(ignore_permissions=True)

	def test_sales_user_role_and_permission_bypass_do_not_enable_lifecycle_actions(self):
		draft = self.sales_order().insert()
		submitted = self.sales_order().insert()
		submitted.submit()
		self.add_sales_role()
		for action, name in (("submit", draft.name), ("delete", draft.name), ("cancel", submitted.name)):
			for bypass in (False, True):
				with self.subTest(action=action, bypass=bypass):
					doc = frappe.get_doc("Sales Order", name)
					self.assertFalse(frappe.has_permission("Sales Order", action, doc))
					doc.flags.ignore_permissions = bypass
					with self.assertRaises(frappe.PermissionError):
						if action == "delete":
							doc.delete(ignore_permissions=bypass)
						else:
							getattr(doc, action)()
		self.assertEqual(frappe.db.get_value("Sales Order", draft.name, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("Sales Order", submitted.name, "docstatus"), 1)
		submitted.reload()
		submitted.po_no = "Forbidden submitted edit"
		with self.assertRaises(frappe.PermissionError):
			submitted.save(ignore_permissions=True)

	def test_packing_slip_read_scope_follows_delivery_note_partner(self):
		self.dn.sales_partner = self.partner.name
		self.dn.save()
		own = self.carton(1)
		other_dn = self.delivery_note()
		# Identical Customer proves access follows the Delivery Note partner.
		other_dn.sales_partner = self.other_partner
		other_dn.save()
		other = self.carton(1, dn=other_dn)
		self.add_sales_role()
		self.assertEqual(set(frappe.get_list("Packing Slip", pluck="name")), {own.name})
		self.assertTrue(frappe.has_permission("Packing Slip", "read", own))
		self.assertFalse(frappe.has_permission("Packing Slip", "read", other))
		with self.assertRaises(frappe.PermissionError):
			other.check_permission("read")
		for action in ("create", "write", "submit", "cancel", "delete"):
			self.assertFalse(frappe.has_permission("Packing Slip", action, own), action)
		with self.assertRaises(frappe.PermissionError):
			own.save(ignore_permissions=True)

	def test_native_draft_discard_cannot_bypass_lifecycle_permissions(self):
		draft = self.sales_order().insert()
		self.add_sales_role()
		for bypass in (False, True):
			with self.subTest(bypass=bypass):
				doc = frappe.get_doc("Sales Order", draft.name)
				doc.flags.ignore_permissions = bypass
				with self.assertRaises(frappe.PermissionError):
					doc.discard()
				self.assertEqual(frappe.db.get_value("Sales Order", draft.name, "docstatus"), 0)

	def test_native_bulk_close_reopen_cannot_process_own_or_foreign_submitted_orders(self):
		from erpnext.selling.doctype.sales_order.sales_order import close_or_unclose_sales_orders

		self.add_sales_role()
		for requested_status in ("Closed", "Draft"):
			for foreign in (False, True):
				with self.subTest(requested_status=requested_status, foreign=foreign):
					frappe.set_user("Administrator")
					order = self.sales_order(
						self.other_customer.name if foreign else self.customer.name,
						self.other_partner if foreign else self.partner.name,
					).insert()
					order.submit()
					if requested_status == "Draft":
						order.update_status("Closed")
					original_status = order.reload().status
					frappe.set_user(self.user.name)
					# The endpoint's DocType-level check passes; the document's
					# processing method must still enforce the role boundary.
					self.assertTrue(frappe.has_permission("Sales Order", "write"))
					with self.assertRaises(frappe.PermissionError):
						close_or_unclose_sales_orders(json.dumps([order.name]), requested_status)
					order.reload()
					self.assertEqual(order.status, original_status)
					self.assertEqual(order.docstatus, 1)

	def test_native_reservation_and_schedule_actions_deny_before_related_writes(self):
		order = self.sales_order(self.other_customer.name, self.other_partner).insert()
		order.submit()
		self.add_sales_role()
		self.assertFalse(frappe.has_permission("Sales Order", "read", order))
		before = {doctype: frappe.db.count(doctype)
			for doctype in ("Stock Reservation Entry", "Delivery Schedule Item")}
		# Null schedule dates avoid the native method's final parent save, so
		# the guard must run before that method writes any related records.
		actions = (
			("create_stock_reservation_entries", {}),
			("cancel_stock_reservation_entries", {"sre_list": ["unrelated-reservation"]}),
			("create_delivery_schedule", {"child_row": order.items[0].as_dict(),
				"schedules": [{"qty": 1, "delivery_date": None}]}),
		)
		for method, arguments in actions:
			with self.subTest(method=method):
				order.flags.ignore_permissions = True
				with self.assertRaises(frappe.PermissionError):
					getattr(order, method)(**arguments)
				for doctype, count in before.items():
					self.assertEqual(frappe.db.count(doctype), count)
				self.assertEqual(frappe.db.get_value("Sales Order", order.name, "docstatus"), 1)
		for doctype in before:
			doc = frappe.new_doc(doctype)
			self.assertFalse(frappe.has_permission(doctype, "create", doc))
			with self.assertRaises(frappe.PermissionError):
				doc.insert(ignore_permissions=True)

	def test_missing_or_revoked_membership_grants_no_rows_or_creation(self):
		order = self.sales_order().insert()
		self.dn.sales_partner = self.partner.name
		self.dn.save()
		slip = self.carton(1)
		unlinked = self.make_user("YRP Sales Partner", "Sales User")
		self.contact.set("email_ids", [])
		self.contact.save()
		# Both never-linked and revoked users retain the role but no scope.
		for user in (unlinked.name, self.user.name):
			frappe.set_user(user)
			with self.subTest(user=user):
				for doctype, doc in (("Sales Order", order), ("Packing Slip", slip)):
					self.assertEqual(frappe.get_list(doctype, pluck="name"), [])
					self.assertFalse(frappe.has_permission(doctype, "read", doc))
				for bypass in (False, True):
					with self.assertRaises(frappe.PermissionError):
						self.sales_order().insert(ignore_permissions=bypass)
