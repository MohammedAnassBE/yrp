"""Transport and native-endpoint scope checks without database fixtures."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import frappe.api  # Load native routing before replacing request-local state.
from werkzeug.wrappers import Request

from yrp.yrp_partner import customer_api as api


class TestCustomerAPI(unittest.TestCase):
	def setUp(self):
		self.scoped = self.enterContext(patch.object(api, "is_customer_report_user", return_value=True))
		self.links = self.enterContext(patch.object(api, "customer_links", return_value=()))
		self.roles = self.enterContext(patch.object(frappe, "get_roles", return_value=["YRP Partner", "YRP Customer"]))
		self.local = SimpleNamespace(form_dict={}, request=None)
		self.enterContext(patch.object(frappe, "local", self.local))
		self.enterContext(patch.object(frappe, "throw", side_effect=frappe.PermissionError))
		self.enterContext(patch.object(api, "_", side_effect=lambda value: value))

	def request(self, path, form=None, method="GET"):
		self.local.request = Request.from_values(path, method=method)
		self.local.form_dict = dict(form or {})
		api.guard_request()

	def assert_denied(self, path, form=None, method="GET"):
		with self.assertRaises(frappe.PermissionError):
			self.request(path, form, method)

	def test_proven_raw_endpoints_blocked_for_every_rpc_version(self):
		for command in api.UNSAFE_RPC:
			for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
				with self.subTest(command=command, prefix=prefix):
					self.assert_denied(prefix + command)

	def test_legacy_command_takes_precedence_over_safe_url(self):
		self.assert_denied("/api/resource/Customer", {"cmd": next(iter(api.UNSAFE_RPC))}, "POST")

	def test_company_address_helpers_reject_unassigned_company_across_transports(self):
		with patch.object(api, "company_names", return_value={"Company A"}), patch.object(frappe, "has_permission", return_value=True):
			for command in api._COMPANY_ADDRESS_RPC:
				for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
					with self.subTest(command=command, prefix=prefix):
						self.assert_denied(prefix + command, {"name": "Company B"})
						self.request(prefix + command, {"name": "Company A"})

	def test_party_defaults_require_authorized_customer_company_pair(self):
		with (
			patch.object(api, "company_names", side_effect=lambda customers: {"Company A"} if customers == ["Customer A"] else set()),
			patch.object(frappe, "has_permission", return_value=True),
		):
			for command in api._CUSTOMER_COMPANY_RPC:
				self.request("/api/method/" + command, {"party": "Customer A", "company": "Company A", "party_type": "Customer"})
				for form in (
					{"party": "Customer A", "company": "Company B", "party_type": "Customer"},
					{"party": "Customer B", "company": "Company A", "party_type": "Customer"},
					{"party": "Customer A", "company": "Company A", "party_type": "Supplier"},
					{"party": "Customer A"},
				):
					with self.subTest(command=command, form=form):
						self.assert_denied("/api/method/" + command, form)

	def test_clear_party_details_remains_native_noop_without_reading_defaults(self):
		with patch.object(api, "company_names") as companies, patch.object(frappe, "has_permission") as permission:
			for form in ({}, {"party": "", "company": "Foreign"}, {"party": None}):
				self.request("/api/method/erpnext.accounts.party.get_party_details", form)
		companies.assert_not_called()
		permission.assert_not_called()

	def test_default_receivable_selection_requires_authorized_company_without_party(self):
		command = "erpnext.accounts.party.get_party_account"
		with patch.object(api, "company_names", return_value={"Company A"}), patch.object(frappe, "has_permission", return_value=True):
			self.request("/api/method/" + command, {"party_type": "Customer", "company": "Company A"})
			self.assert_denied("/api/method/" + command, {"party_type": "Customer", "company": "Company B"})
			self.assert_denied("/api/method/" + command, {"company": "Company A"})

	def test_party_company_address_cannot_bypass_address_read(self):
		command = "erpnext.accounts.party.get_party_details"
		def permission(doctype, ptype, doc=None):
			return doc in {"Company A", "Address A"}
		with patch.object(api, "company_names", return_value={"Company A"}), patch.object(frappe, "has_permission", side_effect=permission):
			base = {"party": "Customer A", "company": "Company A", "doctype": "Purchase Invoice"}
			self.request("/api/method/" + command, {**base, "company_address": "Address A"})
			self.assert_denied("/api/method/" + command, {**base, "company_address": "Foreign Address"})

	def test_party_defaults_do_not_restore_removed_company_read(self):
		with patch.object(api, "company_names", return_value={"Company A"}), patch.object(frappe, "has_permission", return_value=False):
			for command in api._CUSTOMER_COMPANY_RPC:
				self.assert_denied("/api/method/" + command, {
					"party_type": "Customer", "party": "Customer A", "company": "Company A",
				})

	def test_custom_search_query_cannot_reach_raw_journal_or_parent_account_queries(self):
		for command in api._SEARCH_RPC | {"frappe.client.validate_link_and_fetch"}:
			for query in (
				"erpnext.accounts.doctype.journal_entry.journal_entry.get_against_jv",
				"erpnext.accounts.doctype.account.account.get_parent_account",
			):
				for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
					with self.subTest(command=command, query=query, prefix=prefix):
						self.assert_denied(prefix + command, {"doctype": "Account", "query": query})
			self.request("/api/method/" + command, {"doctype": "Account", "query": "erpnext.controllers.queries.tax_account_query"})

	def test_tree_dispatch_cannot_bypass_raw_query_or_party_defaults_guard(self):
		for method in (
			"erpnext.accounts.doctype.journal_entry.journal_entry.get_against_jv",
			"erpnext.accounts.party.get_party_details",
		):
			with self.subTest(method=method):
				self.assert_denied("/api/method/frappe.desk.treeview.get_all_nodes", {
					"doctype": "Account", "parent": "", "tree_method": method,
				})
		self.request("/api/method/frappe.desk.treeview.get_all_nodes", {
			"doctype": "Company", "parent": "", "tree_method": "erpnext.setup.doctype.company.company.get_children",
		})

	def test_account_tree_root_helper_hides_inaccessible_company_ancestors(self):
		with (
			patch.object(api, "company_names", return_value={"Company A"}) as companies,
			patch.object(frappe, "has_permission", return_value=True),
			patch("erpnext.accounts.doctype.account.account.get_root_company", return_value=["Hidden Parent"]) as root,
		):
			self.assertEqual(api.account_root_company("Company A"), [])
			companies.return_value.add("Hidden Parent")
			self.assertEqual(api.account_root_company("Company A"), ["Hidden Parent"])
			root.reset_mock()
			with self.assertRaises(frappe.PermissionError):
				api.account_root_company("Foreign")
			root.assert_not_called()

	def test_company_tree_hides_unassigned_ancestors_and_never_grants_descendants(self):
		rows = [frappe._dict(name="Company A", parent_company="Hidden Parent"),
			frappe._dict(name="Company B", parent_company="Company A")]
		with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", return_value=rows):
			self.assertEqual(api.company_children("Company"), [dict(value="Company A", expandable=1)])
			self.assertEqual(api.company_children("Company", parent="Company A"), [dict(value="Company B", expandable=0)])
			self.assertEqual(api.company_children("Company", parent="Hidden Parent"), [])

	def test_v1_legacy_rpc_suffix_does_not_bypass_guard(self):
		self.assert_denied("/api/method/erpnext.stock.doctype.item.item.get_item_prices/ignored")

	def test_v2_doctype_alias_expands_before_checking_endpoint(self):
		module = SimpleNamespace(__name__="erpnext.stock.doctype.pick_list.pick_list")
		with patch("frappe.modules.utils.load_doctype_module", return_value=module):
			self.assert_denied("/api/v2/method/Pick%20List/get_pick_list_query")

	def test_v1_controller_commands_cover_saved_and_posted_documents(self):
		for command in ("run_doc_method", "frappe.handler.run_doc_method",
				"runserverobj", "frappe.handler.runserverobj"):
			for form in (
				{"dt": "Sales Invoice", "dn": "INV", "method": "set_advances"},
				{"docs": json.dumps({"doctype": "Sales Invoice", "name": "INV"}), "method": "set_advances"},
			):
				with self.subTest(command=command, form=form):
					self.assert_denied("/api/method/" + command, form, "POST")

	def test_controller_uses_native_dt_precedence(self):
		self.assert_denied("/api/method/run_doc_method", {
			"dt": "Sales Invoice", "docs": {"doctype": "Unrelated"}, "method": "set_advances",
		})

	def test_v1_rest_controller_routes_block_get_and_post(self):
		for prefix in ("/api/resource/", "/api/v1/resource/"):
			for method in ("GET", "POST"):
				with self.subTest(prefix=prefix, method=method):
					self.assert_denied(prefix + "Sales%20Invoice/INV", {"run_method": "set_advances"}, method)

	def test_v2_controller_routes_block_saved_and_posted_documents(self):
		for method in ("GET", "POST"):
			with self.subTest(method=method):
				self.assert_denied("/api/v2/document/Sales%20Invoice/INV/method/set_advances", method=method)
				self.assert_denied("/api/v2/method/run_doc_method", {
					"document": {"doctype": "Sales Invoice", "name": "INV"}, "method": "set_advances",
				}, method)

	def test_controller_protection_extends_to_additive_customer_link_grants(self):
		self.links.return_value = (("customer", None),)
		self.assert_denied("/api/method/run_doc_method", {
			"dt": "Production Plan", "method": "get_pending_material_requests",
		})

	def test_report_and_financial_controllers_cannot_use_direct_rpc(self):
		for doctype in ("Report", "Prepared Report", "GL Entry", "Payment Ledger Entry", "Journal Entry", "Payment Entry"):
			with self.subTest(doctype=doctype):
				self.assert_denied("/api/method/run_doc_method", {"dt": doctype, "method": "run"})

	def test_raw_ledger_rest_reads_and_lists_block_additive_account_roles(self):
		self.roles.return_value += ["Accounts User"]
		for doctype in ("Payment%20Ledger%20Entry", "Journal%20Entry", "Payment%20Entry"):
			for prefix in ("/api/resource/", "/api/v1/resource/", "/api/v2/document/"):
				for suffix in ("", "/LEDGER-1"):
					with self.subTest(doctype=doctype, prefix=prefix, suffix=suffix):
						self.assert_denied(prefix + doctype + suffix)
			self.assert_denied("/api/v2/document/" + doctype + "/LEDGER-1/copy")
			self.assert_denied("/api/v2/doctype/" + doctype + "/count")

	def test_raw_ledger_desk_reads_and_exports_blocked(self):
		for command in ("frappe.client.get_value", "frappe.desk.form.load.getdoc",
				"frappe.desk.reportview.get", "frappe.desk.reportview.export_query",
				"frappe.desk.query_report.get_data_for_custom_field"):
			with self.subTest(command=command):
				self.assert_denied("/api/method/" + command, {"doctype": "Payment Ledger Entry"})

	def test_link_fetch_cannot_bypass_raw_financial_read_guard_through_nested_calls(self):
		self.roles.return_value += ["Accounts User"]
		for doctype in ("Payment Ledger Entry", "Journal Entry", "Payment Entry"):
			for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
				with self.subTest(doctype=doctype, prefix=prefix):
					self.assert_denied(prefix + "frappe.client.validate_link_and_fetch", {
						"doctype": doctype, "docname": "OWN-RAW-ENTRY",
						"fields_to_fetch": ["remarks", "against"],
					})
		self.request("/api/method/frappe.client.validate_link_and_fetch", {
			"doctype": "Customer", "docname": "Customer A", "fields_to_fetch": ["customer_name"],
		})

	def test_gl_row_reads_lists_links_and_exports_reach_native_document_scope(self):
		for prefix in ("/api/resource/", "/api/v1/resource/", "/api/v2/document/"):
			self.request(prefix + "GL%20Entry")
			self.request(prefix + "GL%20Entry/GL-OWN")
		for command in api._RAW_READ_RPC:
			with self.subTest(command=command):
				self.request("/api/method/" + command, {"doctype": "GL Entry"})
		self.assert_denied("/api/method/run_doc_method", {"dt": "GL Entry", "method": "cancel"})

	def test_normal_desk_loads_lists_metadata_reports_and_saves_keep_native_paths(self):
		for path, form in (
			("/api/method/frappe.desk.form.load.getdoc", {"doctype": "Sales Invoice"}),
			("/api/method/frappe.desk.reportview.get", {"doctype": "Sales Invoice"}),
			("/api/method/frappe.desk.form.load.getdoctype", {"doctype": "GL Entry"}),
			("/api/method/frappe.desk.query_report.run", {"report_name": "General Ledger"}),
			("/api/method/frappe.desk.query_report.export_query", {"report_name": "General Ledger"}),
			("/api/method/frappe.desk.form.save.savedocs", {"docs": '{"doctype":"Sales Order"}'}),
			("/api/v2/document/Sales%20Invoice/INV", {}),
			("/api/v2/document/Sales%20Invoice", {}),
		):
			with self.subTest(path=path):
				self.request(path, form)

	def test_salesperson_retail_actions_and_partner_draft_actions_keep_native_checks(self):
		for role, doctype, method in (
			("YRP Sales Person", "YRP Retail Order", "submit"),
			("YRP Sales Person", "YRP Retail Order Summary", "cancel"),
			("YRP Sales Partner", "Sales Order", "apply_shipping_rule"),
		):
			self.roles.return_value = ["YRP Partner", role]
			with self.subTest(role=role, method=method):
				self.request("/api/method/run_doc_method", {"dt": doctype, "method": method}, "POST")

	def test_action_roles_cannot_use_internal_methods_even_with_mixed_customer_role(self):
		self.roles.return_value = ["YRP Partner", "YRP Sales Partner", "YRP Customer"]
		for method in ("set_advances", "repost_accounting_entries", "create_delivery_schedule"):
			with self.subTest(method=method):
				self.assert_denied("/api/method/run_doc_method", {"dt": "Sales Order", "method": method})

	def test_mixed_customer_sales_roles_preserve_reviewed_document_actions(self):
		for role, doctype, method in (
			("YRP Sales Partner", "Sales Order", "apply_shipping_rule"),
			("YRP Sales Person", "YRP Retail Order", "submit"),
			("YRP Sales Person", "YRP Retail Order Summary", "cancel"),
		):
			self.roles.return_value = ["YRP Partner", "YRP Customer", role]
			with self.subTest(role=role, method=method):
				self.request("/api/method/run_doc_method", {"dt": doctype, "method": method}, "POST")

	def test_customer_role_alone_cannot_use_sales_action_exceptions(self):
		self.assert_denied("/api/method/run_doc_method", {"dt": "Sales Order", "method": "apply_shipping_rule"})
		self.assert_denied("/api/method/run_doc_method", {"dt": "YRP Retail Order", "method": "submit"})

	def test_additive_account_read_cannot_open_global_balance_helpers(self):
		self.roles.return_value += ["Accounts User"]
		for command in (
			"erpnext.accounts.doctype.journal_entry.journal_entry.get_outstanding",
			"erpnext.accounts.utils.get_account_balances",
			"erpnext.accounts.doctype.journal_entry.journal_entry.get_default_bank_cash_account",
			"erpnext.accounts.doctype.journal_entry.journal_entry.get_average_exchange_rate",
			"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_account_balance",
		):
			with self.subTest(command=command):
				self.assert_denied("/api/method/" + command)

	def test_unscoped_users_and_unrelated_controllers_are_unchanged(self):
		self.request("/api/method/run_doc_method", {"dt": "Unrelated", "method": "safe_method"})
		self.scoped.return_value = False
		self.request("/api/method/" + next(iter(api.UNSAFE_RPC)))
		self.request("/api/method/run_doc_method", {"dt": "Production Plan", "method": "get_pending_material_requests"})

	def test_malformed_controller_documents_fail_closed(self):
		for document in (None, "[", "[]", {}, {"doctype": ["Sales Invoice"]}):
			with self.subTest(document=document):
				self.assert_denied("/api/v2/method/run_doc_method", {"document": document, "method": "set_advances"})

	def test_calendar_uses_live_customer_union_and_native_permissions_with_empty_filters(self):
		row = {"name": "SO-A", "delivery_date": "2026-09-24"}
		with (
			patch.object(api, "customer_names", return_value={"Customer B", "Customer A"}),
			patch.object(frappe, "has_permission") as permission,
			patch.object(frappe, "get_list", return_value=[row]) as query,
		):
			result = api.sales_order_events("2026-09-01", "2026-09-30")
		permission.assert_called_once_with("Sales Order", "read", throw=True)
		self.assertIn(["Sales Order", "customer", "in", ["Customer A", "Customer B"]], query.call_args.kwargs["filters"])
		self.assertEqual(query.call_args.args, ("Sales Order",))
		self.assertEqual(result[0]["allDay"], 0)

	def test_calendar_client_customer_filter_cannot_replace_mandatory_scope(self):
		filters = {"customer": "Foreign"}
		with (
			patch.object(api, "customer_names", return_value={"Customer A"}),
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_list", return_value=[]) as query,
		):
			api.sales_order_events("2026-09-01", "2026-09-30", filters)
		self.assertEqual(filters, {"customer": "Foreign"})
		self.assertIn(["customer", "=", "Foreign"], query.call_args.kwargs["filters"])
		self.assertIn(["Sales Order", "customer", "in", ["Customer A"]], query.call_args.kwargs["filters"])

	def test_calendar_without_membership_never_runs_data_query(self):
		with (
			patch.object(api, "customer_names", return_value=set()),
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_list") as query,
		):
			self.assertEqual(api.sales_order_events("2026-09-01", "2026-09-30"), [])
		query.assert_not_called()
