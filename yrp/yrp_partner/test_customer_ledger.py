"""Real posted invoices/payments exercise customer report totals and exports.

All masters, identities and ledger rows are fictional and rolled back by the
native non-stock fixture. No existing user or role assignment is changed.
"""

import json
import unittest
from unittest.mock import patch

import frappe
from frappe.utils import flt, today

from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from yrp.yrp_partner import customer_reports
from yrp.yrp_retail.test_packing import TestPacking
from yrp.yrp_retail.tests.fixtures import customer, sales_partner, sales_person


class TestCustomerLedger(unittest.TestCase):
	delivery_note = TestPacking.delivery_note

	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		TestPacking.setUp(self)
		self.customer.default_sales_partner = sales_partner(self.customer.territory).name
		self.customer.save()
		self.second, self.foreign = customer(), customer()
		for party in (self.customer, self.second, self.foreign):
			party.append("accounts", {"company": self.company.name})
			party.save()
		for doctype in ("Customer", "Sales Person", "Sales Partner"):
			with patch("yrp.yrp_partner.backfill.sync_partner_type"):
				frappe.get_doc({"doctype": "YRP Partner Type", "reference_doctype": doctype,
					"partner_type_name": "Test Ledger Type " + frappe.generate_hash(length=12)}).insert()
		self.receivable = frappe.db.get_value("Account", {
			"company": self.company.name, "account_type": "Receivable", "is_group": 0}, "name")
		self.cash = frappe.db.get_value("Account", {
			"company": self.company.name, "account_type": "Cash", "is_group": 0}, "name")
		self.income = self.company.default_income_account or frappe.db.get_value("Account", {
			"company": self.company.name, "root_type": "Income", "is_group": 0}, "name")
		self.assertTrue(self.receivable and self.cash and self.income)
		self.invoices, self.payments = {}, {}
		for party, amount, payment in ((self.customer, 100, 25), (self.second, 200, 50), (self.foreign, 1000, 400)):
			invoice = self.make_invoice(party, amount)
			self.invoices[party.name] = invoice
			entry = get_payment_entry("Sales Invoice", invoice.name, bank_account=self.cash, party_amount=payment)
			entry.posting_date = today()
			entry.reference_no = "Fictional customer receipt"
			entry.reference_date = today()
			entry.insert()
			entry.submit()
			self.payments[party.name] = entry
		self.users = {}
		for role, doctype, names in (
			("YRP Customer", "Customer", [self.customer.name, self.second.name]),
			("YRP Sales Person", "Sales Person", [sales_person(self.customer).name, sales_person(self.second).name]),
			("YRP Sales Partner", "Sales Partner", [self.customer.default_sales_partner, self.second.default_sales_partner]),
		):
			user = frappe.get_doc({"doctype": "User",
				"email": "ledger-test-" + frappe.generate_hash(length=12) + "@example.invalid",
				"first_name": "Fictional Ledger User", "send_welcome_email": 0,
				"roles": [{"role": "YRP Partner"}, {"role": role}]}).insert()
			frappe.get_doc({"doctype": "Contact", "first_name": "Fictional Ledger Contact",
				"user": user.name, "email_ids": [{"email_id": user.email, "is_primary": 1}],
				"links": [{"link_doctype": doctype, "link_name": name} for name in names]}).insert()
			self.users[role] = user.name

	def make_invoice(self, party, amount):
		doc = frappe.get_doc({"doctype": "Sales Invoice", "company": self.company.name,
			"customer": party.name, "posting_date": today(), "due_date": today(),
			"currency": "USD", "conversion_rate": 1, "debit_to": self.receivable,
			"selling_price_list": self.price_list.name, "price_list_currency": "USD",
			"plc_conversion_rate": 1, "ignore_pricing_rule": 1, "update_stock": 0,
			"remarks": "INTERNAL-LEDGER-MARKER " + self.foreign.name,
			"items": [{"item_code": self.item.name, "qty": amount / 10,
				"uom": self.uom.name, "stock_uom": self.uom.name, "conversion_factor": 1,
				"rate": 10, "price_list_rate": 10, "income_account": self.income,
				"cost_center": self.company.cost_center}]}).insert()
		doc.submit()
		self.assertEqual(flt(doc.grand_total), amount)
		return doc

	def filters(self, **values):
		return {"company": self.company.name, "from_date": today(), "to_date": today(),
			"report_date": today(), "age_as_on": "Report Date", "ageing_based_on": "Due Date",
			"range": "30, 60, 90, 120", **values}

	def run_report(self, name, **filters):
		# Calling native run also exercises the controller boundary used by
		# internal exports, which do not dispatch the whitelisted override.
		from frappe.desk.query_report import run
		result = run(name, self.filters(**filters))
		fields = [column["fieldname"] for column in result["columns"]]
		result["result"] = [dict(zip(fields, row)) if isinstance(row, (list, tuple)) else row
			for row in result["result"]]
		return result

	def assert_projection(self, result):
		forbidden = {"remarks", "against", "against_voucher", "against_voucher_type", "bill_no",
			"customer_primary_contact", "sales_team", "cost_center", "project", "gl_entry"}
		self.assertFalse(forbidden & {column["fieldname"] for column in result["columns"]})
		for row in result["result"]:
			self.assertFalse(forbidden & set(row))
		payload = json.dumps(result, default=str)
		self.assertNotIn("INTERNAL-LEDGER-MARKER", payload)
		self.assertNotIn(self.foreign.name, payload)
		self.assertNotIn(self.invoices[self.foreign.name].name, payload)

	def test_posted_ledger_and_receivables_union_all_customers_for_each_role(self):
		for role, user in self.users.items():
			with self.subTest(role=role):
				frappe.set_user(user)
				self.assertFalse(frappe.has_permission("Journal Entry", "read"))
				self.assertTrue(frappe.has_permission("GL Entry", "read"))
				ledger = self.run_report("General Ledger", show_remarks=1, include_dimensions=1)
				self.assert_projection(ledger)
				entries = [row for row in ledger["result"] if row.get("voucher_no")]
				self.assertEqual({row["party"] for row in entries}, {self.customer.name, self.second.name})
				self.assertEqual(sum(flt(row.get("debit")) for row in entries), 300)
				self.assertEqual(sum(flt(row.get("credit")) for row in entries), 75)
				self.assertEqual(flt(ledger["result"][-1]["balance"]), 225)
				for name in ("Accounts Receivable", "Accounts Receivable Summary"):
					with self.subTest(report=name):
						result = self.run_report(name, show_remarks=1, show_gl_balance=1, show_future_payments=1)
						self.assert_projection(result)
						rows = {row["party"]: row for row in result["result"] if row.get("party") in {self.customer.name, self.second.name}}
						self.assertEqual(set(rows), {self.customer.name, self.second.name})
						self.assertEqual(flt(rows[self.customer.name]["outstanding"]), 75)
						self.assertEqual(flt(rows[self.second.name]["outstanding"]), 150)
						self.assertEqual(flt(result["result"][-1]["outstanding"]), 225)
				# Explicit own selection narrows the totals as well as the rows.
				subset = self.run_report("General Ledger", party=[self.customer.name])
				self.assertEqual(flt(subset["result"][-1]["balance"]), 75)
				for filters in ({"party": [self.foreign.name]}, {"customer": self.foreign.name},
					{"party_type": "Supplier", "party": [self.customer.name]}):
					with self.subTest(filters=filters), self.assertRaises(frappe.PermissionError):
						self.run_report("General Ledger", **filters)
		# Actual unallocated receipts exercise Summary's separate advance
		# aggregate, including a foreign advance in the same company.
		frappe.set_user("Administrator")
		for party, amount in ((self.customer, 10), (self.foreign, 50)):
			advance = get_payment_entry("Sales Invoice", self.invoices[party.name].name,
				bank_account=self.cash, party_amount=amount)
			advance.set("references", [])
			advance.reference_no = "Fictional unallocated receipt"
			advance.reference_date = today()
			advance.insert()
			advance.submit()
		for role, user in self.users.items():
			with self.subTest(advance_role=role):
				frappe.set_user(user)
				result = self.run_report("Accounts Receivable Summary")
				self.assert_projection(result)
				rows = {row["party"]: row for row in result["result"] if row.get("party") in {self.customer.name, self.second.name}}
				self.assertEqual(flt(rows[self.customer.name]["advance"]), 10)
				self.assertEqual(flt(rows[self.customer.name]["outstanding"]), 65)
				self.assertEqual(flt(rows[self.second.name]["advance"]), 0)
				self.assertEqual(flt(result["result"][-1]["outstanding"]), 215)

	def test_real_csv_export_redacts_internal_data_and_prepared_paths_fail_closed(self):
		frappe.set_user(self.users["YRP Customer"])
		form = frappe._dict(report_name="General Ledger", filters=json.dumps(self.filters(show_remarks=1)),
			file_format_type="CSV", custom_columns="[]", include_hidden_columns=1,
			ignore_visible_idx=1, export_in_background=0)
		with patch.object(frappe.local, "form_dict", form, create=True), \
				patch.object(frappe.local, "response", frappe._dict(), create=True):
			customer_reports.export_query()
			payload = frappe.local.response.filecontent.decode("utf-8")
			self.assertIn(self.invoices[self.customer.name].name, payload)
			self.assertIn(self.invoices[self.second.name].name, payload)
			self.assertNotIn(self.invoices[self.foreign.name].name, payload)
			self.assertNotIn(self.foreign.name, payload)
			self.assertNotIn("INTERNAL-LEDGER-MARKER", payload)
			self.assertNotIn("Against Account", payload)
			form.export_in_background = 1
			with self.assertRaises(frappe.PermissionError):
				customer_reports.export_query()
		with self.assertRaises(frappe.PermissionError):
			customer_reports.run("General Ledger", self.filters(), user="Administrator")
		with self.assertRaises(frappe.PermissionError):
			customer_reports.run("General Ledger", self.filters(), custom_columns=[{
				"fieldname": "remarks", "doctype": "GL Entry", "link_field": "gl_entry", "insert_after_index": 0}])
		with self.assertRaises(frappe.PermissionError):
			self.run_report("General Ledger", prepared_report_name="Unrestricted cache")
		from frappe.core.doctype.prepared_report.prepared_report import make_prepared_report
		with self.assertRaises(frappe.PermissionError):
			make_prepared_report("General Ledger", self.filters())
		prepared = frappe.get_doc({"doctype": "Prepared Report", "report_name": "General Ledger",
			"name": "Fictional unrestricted cache", "status": "Completed", "owner": "Administrator"})
		with self.assertRaises(frappe.PermissionError):
			prepared.get_prepared_data()
		for name in ("Profit and Loss Statement", "Accounts Payable"):
			with self.subTest(report=name), self.assertRaises(frappe.PermissionError):
				self.run_report(name)
