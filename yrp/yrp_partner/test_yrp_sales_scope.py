"""Partner scope on YRP Sales Orders, Delivery Notes and Packing Slips; rows roll back."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_retail.tests.fixtures import customer, sales_person

SALES_DOCTYPES = ("YRP Sales Order", "YRP Delivery Note", "YRP Packing Slip")


class TestYRPSalesScope(unittest.TestCase):
	def setUp(self):
		point = "yrp_sales_scope_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.addCleanup(frappe.db.rollback, save_point=point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		for doctype in ("Customer", "Sales Partner", "Sales Person"):
			with patch("yrp.yrp_partner.backfill.sync_partner_type"):
				frappe.get_doc(
					{
						"doctype": "YRP Partner Type",
						"reference_doctype": doctype,
						"partner_type_name": "ZZ Sales Scope " + frappe.generate_hash(length=12),
					}
				).insert()
		self.own, self.foreign = customer(), customer()
		self.company = self.row(
			"Company", company_name="ZZ Sales Scope Company " + frappe.generate_hash(length=10)
		)
		unpaired = self.row(
			"Company", company_name="ZZ Sales Scope Unpaired " + frappe.generate_hash(length=10)
		)
		for party in (self.own, self.foreign):
			self.row(
				"Party Account",
				parent=party.name,
				parenttype="Customer",
				parentfield="accounts",
				company=self.company.name,
			)
		self.users = {
			"YRP Customer": self.member("YRP Customer", "Customer", self.own.name),
			"YRP Sales Partner": self.member(
				"YRP Sales Partner", "Sales Partner", self.own.default_sales_partner
			),
			"YRP Sales Person": self.member("YRP Sales Person", "Sales Person", sales_person(self.own).name),
		}
		self.own_docs = self.documents(self.own, self.company)
		self.foreign_docs = self.documents(self.foreign, self.company)
		self.unpaired_docs = self.documents(self.own, unpaired)

	def row(self, doctype, **values):
		"""Synthetic permission rows; no stock, pricing or ledger side effects."""
		doc = frappe.get_doc(
			{
				"doctype": doctype,
				"name": "ZZ Sales Scope " + frappe.generate_hash(length=16),
				"docstatus": 0,
				**values,
			}
		)
		doc.db_insert()
		return doc

	def member(self, role, source_doctype, source_name):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"send_welcome_email": 0,
				"email": "zz-sales-scope-" + frappe.generate_hash(length=12) + "@example.invalid",
				"first_name": "ZZ Sales Scope",
				"roles": [{"role": "YRP Partner"}, {"role": role}],
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "ZZ Sales Scope " + frappe.generate_hash(length=10),
				"email_ids": [{"email_id": user.email, "is_primary": 1}],
				"links": [{"link_doctype": source_doctype, "link_name": source_name}],
			}
		).insert()
		return user

	def documents(self, party, company):
		order = self.row("YRP Sales Order", customer=party.name, company=company.name)
		note = self.row("YRP Delivery Note", customer=party.name, company=company.name)
		slip = self.row("YRP Packing Slip", customer=party.name, delivery_note=note.name)
		return dict(zip(SALES_DOCTYPES, (order, note, slip), strict=True))

	def visible(self, doctype):
		names = [docs[doctype].name for docs in (self.own_docs, self.foreign_docs, self.unpaired_docs)]
		return set(frappe.get_list(doctype, filters={"name": ["in", names]}, pluck="name"))

	def test_each_role_lists_only_documents_of_its_paired_customers(self):
		# Sales Partner reaches the Customer only through its default_sales_partner.
		for role, user in self.users.items():
			frappe.set_user(user.name)
			for doctype in SALES_DOCTYPES:
				with self.subTest(role=role, doctype=doctype):
					self.assertEqual(self.visible(doctype), {self.own_docs[doctype].name})

	def test_each_role_reads_its_own_document_and_never_another(self):
		for role, user in self.users.items():
			frappe.set_user(user.name)
			for doctype in SALES_DOCTYPES:
				with self.subTest(role=role, doctype=doctype):
					self.assertTrue(frappe.has_permission(doctype, "read", self.own_docs[doctype]))
					for other in (self.foreign_docs[doctype], self.unpaired_docs[doctype]):
						self.assertFalse(frappe.has_permission(doctype, "read", other))
						with self.assertRaises(frappe.PermissionError):
							frappe.get_doc(doctype, other.name, check_permission="read")

	def test_each_role_is_denied_writes_and_lifecycle_actions(self):
		for role, user in self.users.items():
			for doctype in SALES_DOCTYPES:
				if role == "YRP Sales Partner" and doctype == "YRP Sales Order":
					continue  # Draft orders are the Sales Partner's one action; see test_sales_partner_role.
				with self.subTest(role=role, doctype=doctype):
					frappe.set_user("Administrator")
					doc = frappe.get_doc(doctype, self.own_docs[doctype].name)
					frappe.set_user(user.name)
					for action in ("create", "write", "submit", "cancel", "delete"):
						self.assertFalse(frappe.has_permission(doctype, action, doc), action)
					with self.assertRaises(frappe.PermissionError):
						doc.save(ignore_permissions=True)

	def test_processing_user_scope_pairs_delivery_notes_and_their_packing_slips(self):
		user = self.member("Sales User", "Customer", self.own.name)
		frappe.set_user(user.name)
		for doctype in ("YRP Delivery Note", "YRP Packing Slip"):
			with self.subTest(doctype=doctype):
				self.assertEqual(self.visible(doctype), {self.own_docs[doctype].name})

	def test_erpnext_sales_documents_grant_partner_roles_nothing(self):
		for role, user in self.users.items():
			frappe.set_user(user.name)
			for doctype in ("Sales Order", "Delivery Note", "Packing Slip", "Pick List"):
				with self.subTest(role=role, doctype=doctype):
					self.assertFalse(frappe.has_permission(doctype, "read"))
