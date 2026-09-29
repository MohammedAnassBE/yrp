"""Report security boundaries without database fixtures or live role changes."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from yrp.yrp_partner import customer_reports as reports


class _NativeReport:
	def __init__(self, name="General Ledger", **values):
		self.name = name
		self.ref_doctype = reports.CUSTOMER_REPORTS.get(name, "GL Entry")
		self.report_type = "Script Report"
		self.is_standard = "Yes"
		self.prepared_report = 1
		self.disable_prepared_report_automation = 0
		self.__dict__.update(values)

	def get(self, key):
		return self.__dict__.get(key)

	def is_permitted(self):
		return "native permission"

	def execute_module(self, filters):
		return "native execution", filters


class _Report(reports.CustomerReportMixin, _NativeReport):
	pass


class TestCustomerReports(unittest.TestCase):
	def setUp(self):
		self.scoped = self.enterContext(patch.object(reports, "is_customer_report_user", return_value=True))
		self.memberships = self.enterContext(patch.object(reports, "customer_names", return_value=["Customer A", "Customer B"]))
		self.companies = self.enterContext(patch.object(reports, "company_names", return_value={"Company"}))
		self.local = SimpleNamespace(form_dict={})
		self.enterContext(patch.object(frappe, "local", self.local))
		self.enterContext(patch.object(frappe, "session", frappe._dict(user="customer@example.invalid")))
		self.enterContext(patch.object(frappe, "throw", side_effect=frappe.PermissionError))
		self.enterContext(patch.object(reports, "_", side_effect=lambda text: text))

	def test_blank_selection_is_full_membership_union_and_does_not_mutate_input(self):
		filters = {"company": "Company", "party": [], "show_remarks": 1, "include_dimensions": 1}
		result = reports.scoped_filters(filters)
		self.assertEqual(result.party, ["Customer A", "Customer B"])
		self.assertEqual(result.party_type, "Customer")
		self.assertEqual(result.show_remarks, 0)
		self.assertEqual(result.include_dimensions, 0)
		self.assertEqual(filters["party"], [])
		self.assertEqual(filters["show_remarks"], 1)

	def test_explicit_owned_subset_accepts_native_multiselect_json(self):
		self.assertEqual(reports.scoped_filters({"party": '["Customer B"]'}).party, ["Customer B"])
		self.assertEqual(reports.scoped_filters({"customer": "Customer A"}).party, ["Customer A"])

	def test_company_limits_blank_party_union_to_customers_with_that_default(self):
		self.memberships.side_effect = lambda **kwargs: (
			{"Customer A"} if kwargs.get("company") == "Company" else {"Customer A", "Customer B"}
		)
		result = reports.scoped_filters({"company": "Company"})
		self.assertEqual(result.party, ["Customer A"])
		self.companies.assert_called_once_with(customers={"Customer A", "Customer B"})

	def test_explicit_customer_without_selected_company_is_rejected(self):
		self.memberships.side_effect = lambda **kwargs: (
			{"Customer A"} if kwargs.get("company") == "Company" else {"Customer A", "Customer B"}
		)
		for selection in ({"party": ["Customer B"]}, {"party": ["Customer A", "Customer B"]},
				{"customer": "Customer B"}):
			with self.subTest(selection=selection), self.assertRaises(frappe.PermissionError):
				reports.scoped_filters({"company": "Company", **selection})

	def test_foreign_and_malformed_company_filters_are_rejected(self):
		for company in ("Foreign", ["Company"], {"in": ["Company"]}, 0, False, ["=", "Company"]):
			with self.subTest(company=company), self.assertRaises(frappe.PermissionError):
				reports.scoped_filters({"company": company})

	def test_financial_reports_require_authorized_company_before_native_execution(self):
		with patch.object(reports, "_execute") as execute:
			for name in ("General Ledger", "Accounts Receivable", "Accounts Receivable Summary"):
				for filters in ({}, {"company": ""}, {"company": None}, {"company": "Foreign"}):
					with self.subTest(name=name, filters=filters), self.assertRaises(frappe.PermissionError):
						_Report(name).execute_script_report(filters)
			execute.assert_not_called()

	def test_company_default_revocation_takes_effect_before_next_execution(self):
		self.assertEqual(reports.scoped_filters({"company": "Company"}).company, "Company")
		self.companies.return_value = set()
		with self.assertRaises(frappe.PermissionError):
			reports.scoped_filters({"company": "Company"})

	def test_no_membership_fails_closed_in_permission_and_execution(self):
		self.memberships.return_value = []
		self.assertFalse(_Report().is_permitted())
		with self.assertRaises(frappe.PermissionError):
			reports.scoped_filters({})

	def test_foreign_and_malformed_party_filters_are_rejected(self):
		for filters in (
			{"party": ["Customer A", "Foreign"]}, {"customer": "Foreign"},
			{"party_type": "Supplier"}, {"party": {"in": ["Customer A"]}},
			{"party": [["in", "Customer A"]]}, {"party": 1},
			{"party": ["Customer A"], "customer": "Customer B"},
			{"prepared_report_name": "unrestricted-cache"}, '["party", "Customer A"]',
		):
			with self.subTest(filters=filters), self.assertRaises(frappe.PermissionError):
				reports.scoped_filters(filters)

	def test_unsafe_finance_options_never_reach_native_helpers(self):
		filters = reports.scoped_filters({"show_gl_balance": 1, "show_future_payments": 1,
			"show_remarks": 1, "show_delivery_notes": 1})
		for field in ("show_gl_balance", "show_future_payments", "show_remarks", "show_delivery_notes"):
			self.assertEqual(filters[field], 0)

	def test_native_execution_receives_scope_before_totals_and_projects_raw_rows(self):
		seen = []
		def execute(name, filters):
			seen.append(filters)
			ledger = [{"party": "Customer A", "debit": 12, "remarks": "private", "against": "Other"},
				{"party": "Foreign", "debit": 9000}]
			own = [row for row in ledger if row["party"] in filters.party]
			own.append({"account": "Total", "debit": sum(row["debit"] for row in own)})
			return ([{"fieldname": field} for field in ("party", "debit", "account", "against")], own)
		with patch.object(reports, "_execute", side_effect=execute):
			columns, rows = _Report().execute_script_report({"company": "Company", "party": ["Customer A"]})
		self.assertEqual(seen[0].party_type, "Customer")
		self.assertEqual(rows[-1]["debit"], 12)
		self.assertEqual(rows[0], {"party": "Customer A", "debit": 12})
		self.assertNotIn("against", [c["fieldname"] for c in columns])

	def test_projection_removes_hidden_extra_keys_even_without_columns(self):
		columns, rows = reports._project("Accounts Receivable", (
			[{"fieldname": "party"}, {"fieldname": "range1"}, {"fieldname": "customer_primary_contact"}],
			[{"party": "Customer A", "range1": 10, "customer_primary_contact": "Contact",
				"remarks": "secret", "bill_no": "foreign supplier invoice", "sales_person": "private"}],
		))
		self.assertEqual(rows, [{"party": "Customer A", "range1": 10}])
		self.assertEqual([c["fieldname"] for c in columns], ["party", "range1"])

	def test_only_reviewed_standard_live_reports_are_permitted(self):
		for name in reports.CUSTOMER_REPORTS:
			with self.subTest(name=name):
				doc = _Report(name)
				self.assertTrue(doc.is_permitted())
				self.assertEqual(doc.prepared_report, 0)
				self.assertEqual(doc.disable_prepared_report_automation, 1)
		for values in (
			{"name": "Profit and Loss Statement"}, {"snapshot_report": 1},
			{"is_standard": "No"}, {"report_type": "Query Report"},
			{"ref_doctype": "Journal Entry"}, {"custom_report": "Saved copy"},
			{"custom_columns": [{"fieldname": "remarks"}]}, {"disabled": 1},
		):
			with self.subTest(values=values):
				self.assertFalse(_Report(**values).is_permitted())

	def test_direct_query_script_snapshot_and_standard_paths_are_denied(self):
		doc = _Report()
		for method in ("execute_query_report", "execute_script", "execute_snapshot_report"):
			with self.subTest(method=method), self.assertRaises(frappe.PermissionError):
				getattr(doc, method)({})
		with self.assertRaises(frappe.PermissionError):
			doc.run_standard_report({}, None, None)

	def test_unsaved_custom_columns_and_user_substitution_are_denied(self):
		for options in ({"custom_columns": [{"fieldname": "remarks"}]}, {"user": "Administrator"}):
			with self.subTest(options=options), self.assertRaises(frappe.PermissionError):
				reports._request_options(**options)
		self.local.form_dict["custom_columns"] = '[{"fieldname":"remarks"}]'
		with self.assertRaises(frappe.PermissionError):
			_Report().is_permitted()

	def test_synchronous_plain_export_allowed_async_export_denied(self):
		self.local.form_dict.update(custom_columns="[]", export_in_background="0")
		reports._request_options()
		self.local.form_dict["export_in_background"] = "1"
		with self.assertRaises(frappe.PermissionError):
			reports._request_options()

	def test_prepared_creation_and_download_denied_even_without_membership(self):
		self.memberships.return_value = []
		doc = reports.CustomerPreparedReportMixin()
		with self.assertRaises(frappe.PermissionError):
			doc.before_insert()
		with self.assertRaises(frappe.PermissionError):
			doc.get_prepared_data(with_file_name=True)

	def test_unscoped_users_retain_native_behavior(self):
		self.scoped.return_value = False
		doc = _Report("Unrelated report")
		self.assertEqual(doc.is_permitted(), "native permission")
		self.assertEqual(doc.execute_module({}), ("native execution", {}))
		self.assertEqual(doc.prepared_report, 1)

	def test_receivable_invoice_metadata_is_scoped_without_journal_permission(self):
		cls = reports._receivable_class()
		doc = object.__new__(cls)
		doc.filters = frappe._dict(party=["Customer A"], company="Company", report_date="2026-09-24")
		with patch.object(frappe, "get_all", return_value=[frappe._dict(name="INV-A", po_no="PO-A")]) as get_all:
			doc.get_invoice_details()
		self.assertEqual(get_all.call_count, 1)
		self.assertEqual(get_all.call_args.args, ("Sales Invoice",))
		self.assertEqual(get_all.call_args.kwargs["filters"]["customer"], ["in", ["Customer A"]])
		self.assertEqual(doc.invoice_details["INV-A"].po_no, "PO-A")

	def test_retail_reports_run_each_owned_customer_without_losing_detail_columns(self):
		def retail(kind, filters):
			return ([{"fieldname": "customer"}, {"fieldname": "requested_qty"}],
				[{"customer": filters["customer"], "requested_qty": 4}], "Detail quantities")
		with patch("yrp.yrp_retail.reporting.run", side_effect=retail) as native:
			columns, rows, message = _Report("YRP Retail Summary Allocation").execute_script_report({})
		self.assertEqual([call.args[0] for call in native.call_args_list], ["allocation", "allocation"])
		self.assertEqual([row["customer"] for row in rows], ["Customer A", "Customer B"])
		self.assertEqual(rows[0]["requested_qty"], 4)
		self.assertEqual(message, "Detail quantities")

	def test_summary_advance_query_is_scoped_to_customer_and_effective_company(self):
		from erpnext.accounts.report.accounts_receivable_summary import accounts_receivable_summary as native
		row = frappe._dict(party="Customer A", party_type="Customer", currency="USD",
			invoiced=12.0, paid=2.0, credit_note=0.0, outstanding=10.0, range1=10.0, total_due=10.0)
		receivables = Mock(filters=frappe._dict(company="Effective Company"))
		receivables.run.return_value = [], [row]
		def initialize(doc, filters):
			doc.filters = frappe._dict(filters)
			doc.range_numbers = [1]
		def columns(doc):
			doc.columns = []
		with (
			patch.object(native.AccountsReceivableSummary, "__init__", initialize),
			patch.object(native.AccountsReceivableSummary, "get_columns", columns),
			patch.object(native, "get_party_types_from_account_type", return_value=["Customer", "Other Party"]),
			patch.object(frappe, "db", Mock(get_single_value=Mock(return_value="Customer Name"))),
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"),
			patch.object(reports, "_receivable_class", return_value=Mock(return_value=receivables)),
			patch("erpnext.accounts.utils.get_currency_precision", return_value=2),
			patch("erpnext.accounts.party.get_partywise_advanced_payment_amount",
				return_value={"Customer A": 3, "Foreign": 9000}) as advance,
		):
			_, rows = reports._receivable_summary(frappe._dict(party=["Customer A"], report_date="2026-09-24"))
		advance.assert_called_once_with(["Customer"], "2026-09-24", 0, "Effective Company", party="Customer A")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].advance, 3)
		self.assertEqual(rows[0].paid, -1)

	def test_summary_rejects_account_currency_options_before_querying(self):
		with patch.object(reports, "_receivable_class") as detail:
			for filters in ({"in_party_currency": 1}, {"party_account": "EUR Receivable"}):
				with self.subTest(filters=filters), self.assertRaises(frappe.PermissionError):
					reports._receivable_summary(frappe._dict(filters))
			detail.assert_not_called()
