"""Customer GL rows are readable; mixed accounting vouchers remain closed.

All identity, membership, and query rows are fictional and rolled back. Reuse
fixture methods by composition, so this module collects no inherited tests and
does not post invoices or execute financial lifecycle side effects.
"""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner import customer_access
from yrp.yrp_partner import test_customer_access as access_fixtures
from yrp.yrp_retail.tests.fixtures import sales_person


class TestCustomerRawDocuments(unittest.TestCase):
	make_user = access_fixtures.TestCustomerAccess.make_user
	link = access_fixtures.TestCustomerAccess.link
	row = access_fixtures.TestCustomerAccess.row

	def setUp(self):
		access_fixtures.TestCustomerAccess.setUp(self)
		self.user.append("roles", {"role": "Accounts User"})
		self.user.save()
		self.users = {"YRP Customer": self.user.name}
		for role, source, reference in (
			("YRP Sales Person", "Sales Person", sales_person(self.own).name),
			("YRP Sales Partner", "Sales Partner", self.own.default_sales_partner),
		):
			user = self.make_user("YRP Partner", role, "Accounts User")
			self.link(user, source, reference)
			self.users[role] = user.name
		self.documents = {}
		for doctype in ("Payment Ledger Entry", "Journal Entry", "Payment Entry"):
			self.documents[doctype] = [self.row(doctype, docstatus=1,
				party_type="Customer", party=party.name,
				remarks="Fictional private accounting details",
				user_remark="Fictional private journal details")
				for party in (self.own, self.foreign)]

	def test_raw_document_reads_stay_denied_with_accounts_user_for_all_scoped_roles(self):
		from frappe.client import get

		for role, user in self.users.items():
			with self.subTest(role=role):
				frappe.set_user(user)
				self.assertEqual(customer_access.customer_names(), {self.own.name})
				# Prove the additive role supplies the underlying native grant;
				# document-level denial must still override it.
				self.assertTrue(frappe.has_permission("Journal Entry", "read"))
				self.assertTrue(frappe.has_permission("Payment Entry", "read"))
				for doctype, documents in self.documents.items():
					for document in documents:
						with self.subTest(doctype=doctype, document=document.name):
							for action in ("read", "select", "print"):
								self.assertFalse(frappe.has_permission(doctype, action, document), action)
							with self.assertRaises(frappe.PermissionError):
								get(doctype, document.name)

	def test_native_print_validation_cannot_use_report_print_grant_for_raw_documents(self):
		from frappe.www.printview import validate_print_permission

		with patch.object(frappe, "form_dict", frappe._dict()):
			for role, user in self.users.items():
				frappe.set_user(user)
				for doctype, documents in self.documents.items():
					with self.subTest(role=role, doctype=doctype):
						# Same permission gate used by /printview and download_pdf.
						with self.assertRaises(frappe.PermissionError):
							validate_print_permission(frappe.get_doc(doctype, documents[0].name))

	def test_journal_and_payment_lists_stay_empty_with_additive_read_role(self):
		for role, user in self.users.items():
			frappe.set_user(user)
			for doctype in ("Journal Entry", "Payment Entry"):
				with self.subTest(role=role, doctype=doctype):
					names = [document.name for document in self.documents[doctype]]
					self.assertEqual(frappe.get_list(doctype, filters={"name": ["in", names]}, pluck="name"), [])

	def test_native_general_ledger_report_print_grant_remains_available(self):
		from frappe.desk.query_report import get_print_format_data

		with patch.object(frappe, "form_dict", frappe._dict()):
			for role, user in self.users.items():
				with self.subTest(role=role):
					frappe.set_user(user)
					self.assertTrue(frappe.has_permission("GL Entry", "print"))
					self.assertTrue(frappe.has_permission("GL Entry", "report"))
					self.assertTrue(frappe.get_doc("Report", "General Ledger").is_permitted())
					self.assertIn("html", get_print_format_data("General Ledger Standard"))

	def test_gl_entry_reads_lists_and_prints_include_only_authorized_customer_rows(self):
		from frappe.client import get
		from frappe.www.printview import validate_print_permission

		rows = [self.row("GL Entry", company=self.company.name, docstatus=1,
			party_type=party_type, party=party, voucher_type="Journal Entry", voucher_no="Fictional Mixed Journal")
			for party_type, party in (("Customer", self.own.name), ("Customer", self.foreign.name), ("Supplier", self.own.name))]
		other_company = self.row("Company", company_name="Fictional Unassigned GL Company " + frappe.generate_hash(length=8))
		rows.append(self.row("GL Entry", company=other_company.name, docstatus=1,
			party_type="Customer", party=self.own.name, voucher_type="Journal Entry", voucher_no="Fictional Other Company Journal"))
		for role, user in self.users.items():
			frappe.set_user(user)
			with self.subTest(role=role), patch.object(frappe, "form_dict", frappe._dict()):
				self.assertEqual(frappe.get_list("GL Entry", filters={"name": ["in", [row.name for row in rows]]}, pluck="name"), [rows[0].name])
				self.assertEqual(get("GL Entry", rows[0].name)["party"], self.own.name)
				validate_print_permission(frappe.get_doc("GL Entry", rows[0].name))
				for row in rows[1:]:
					with self.assertRaises(frappe.PermissionError):
						get("GL Entry", row.name)
					with self.assertRaises(frappe.PermissionError):
						validate_print_permission(frappe.get_doc("GL Entry", row.name))
				for action in ("write", "create", "delete", "submit", "cancel"):
					self.assertFalse(frappe.has_permission("GL Entry", action, rows[0]))

	def test_native_login_log_insert_remains_available_for_customer(self):
		from frappe.core.doctype.activity_log.activity_log import add_authentication_log

		frappe.set_user(self.user.name)
		subject = f"Fictional customer login {frappe.generate_hash(length=10)}"
		with patch.object(frappe.local, "request_ip", "127.0.0.1", create=True):
			# This is the native login path, including document lifecycle hooks.
			add_authentication_log(subject, self.user.name)
		self.assertEqual(
			frappe.db.get_value("Activity Log", {"subject": subject},
				["user", "operation", "status", "ip_address"]),
			(self.user.name, "Login", "Success", "127.0.0.1"),
		)
