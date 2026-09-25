"""Close native RPC paths that bypass Customer document/query permissions.

Register ``guard_request`` as an ``auth_hooks`` hook, not ``before_request``:
Frappe authenticates API keys after before_request. This hook inspects the same
routing map as the dispatcher, without intercepting normal document loads.
The Sales Order calendar and Company tree use permission-aware replacements;
ordinary Desk reads and sales/retail save workflows keep their native paths.
"""

import json

import frappe
from frappe import _

from yrp.yrp_partner.customer_access import (
	CUSTOMER_DOCTYPES,
	company_names,
	customer_links,
	customer_names,
	is_customer_report_user,
)


UNSAFE_RPC = frozenset({
	"erpnext.stock.doctype.item.item.get_item_prices",
	"erpnext.manufacturing.doctype.production_plan.production_plan.sales_order_query",
	"erpnext.stock.doctype.pick_list.pick_list.get_pick_list_query",
	"erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry.get_invoices",
	"erpnext.regional.italy.utils.export_invoices",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_outstanding",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_against_jv",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_account_details_and_party_type",
	"erpnext.accounts.utils.get_account_balances",
	"erpnext.accounts.utils.get_account_balances_coa",
	"erpnext.accounts.doctype.account.account.get_parent_account",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_default_bank_cash_account",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_average_exchange_rate",
	"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.get_account_balance",
})
_RAW_LEDGERS = {"Payment Ledger Entry", "Journal Entry", "Payment Entry"}
_SEARCH_RPC = {"frappe.desk.search.search_link", "frappe.desk.search.search_widget"}
_INDIRECT_RPC = {
	**dict.fromkeys(_SEARCH_RPC | {"frappe.client.validate_link_and_fetch"}, "query"),
	"frappe.desk.treeview.get_all_nodes": "tree_method",
}
_RAW_READ_RPC = frozenset({
	"frappe.client.get", "frappe.client.get_list", "frappe.client.get_value",
	"frappe.client.get_count", "frappe.client.validate_link_and_fetch",
	"frappe.desk.form.load.getdoc",
	"frappe.desk.form.load.get_docinfo", "frappe.desk.reportview.get",
	"frappe.desk.reportview.get_list", "frappe.desk.reportview.get_count",
	"frappe.desk.reportview.export_query", "frappe.desk.reportview.get_stats",
	"frappe.desk.reportview.get_sidebar_stats",
	"frappe.desk.query_report.get_data_for_custom_field",
	"frappe.desk.search.search_link", "frappe.desk.search.search_widget",
})
_CONTROLLER_RPC = {
	"run_doc_method", "frappe.handler.run_doc_method",
	"runserverobj", "frappe.handler.runserverobj",
	"frappe.api.v2.run_doc_method",
}
_CONTROLLER_DOCTYPES = CUSTOMER_DOCTYPES | _RAW_LEDGERS | {
	"Journal Entry", "Report", "Prepared Report",
}
# These actions still pass the existing native permission and lifecycle checks.
# No sales/retail save endpoint is blocked by this controller-method boundary.
_RETAIL_ACTION_METHODS = {"submit", "cancel", "discard", "add_comment"}
_SALES_ORDER_DRAFT_METHODS = {"apply_shipping_rule", "add_comment"}
_COMPANY_ADDRESS_RPC = {
	"erpnext.setup.doctype.company.company.get_default_company_address",
	"erpnext.setup.doctype.company.company.get_billing_shipping_address",
}
_CUSTOMER_COMPANY_RPC = {
	"erpnext.accounts.party.get_party_details",
	"erpnext.accounts.party.get_party_account",
	"erpnext.accounts.doctype.journal_entry.journal_entry.get_party_account_and_currency",
}


def _deny():
	frappe.throw(_("This operation is unavailable for customer-scoped access."), frappe.PermissionError)


def _document_doctype(value):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			_deny()
	if not isinstance(value, dict) or not isinstance(value.get("doctype"), str):
		_deny()
	return value["doctype"]


def _guard_controller(doctype, method):
	if not isinstance(doctype, str) or not doctype:
		_deny()
	if doctype not in _CONTROLLER_DOCTYPES and not customer_links(doctype):
		return
	from yrp.yrp_partner.sales_roles import RETAIL_DOCUMENTS, SALES_PARTNER, SALES_PERSON

	# Customer membership can coexist with sales action roles. Preserve their
	# reviewed actions; native per-document/lifecycle checks remain authoritative.
	roles = set(frappe.get_roles())
	if "YRP Partner" in roles:
		if (SALES_PERSON in roles and doctype in RETAIL_DOCUMENTS
			and method in _RETAIL_ACTION_METHODS):
			return
		if (SALES_PARTNER in roles and doctype == "Sales Order"
			and method in _SALES_ORDER_DRAFT_METHODS):
			return
	_deny()


def _guard_customer_company(command, form):
	party, company = form.get("party"), form.get("company")
	details = command == "erpnext.accounts.party.get_party_details"
	# Preserve the native clear-Customer action, which returns an empty mapping
	# before it reads any account, company or address.
	if details and not party:
		return
	party_type = form.get("party_type", "Customer" if details else None)
	if party_type != "Customer" or not isinstance(company, str) or not company:
		_deny()
	if party is None or party == "":
		# The account selector can ask for the Company's default receivable
		# account before a party is selected; the native account check still runs.
		if command != "erpnext.accounts.party.get_party_account":
			_deny()
		customers = None
	elif isinstance(party, str):
		customers = [party]
	else:
		_deny()
	if company not in company_names(customers=customers):
		_deny()
	if not frappe.has_permission("Company", "read", doc=company):
		_deny()
	address = form.get("company_address")
	if details and address:
		# Native purchase-side formatting can render this supplied address with
		# check_permissions=False, independently of the selected Company's name.
		if not isinstance(address, str) or not frappe.has_permission("Address", "read", doc=address):
			_deny()


def _guard_rpc(command, form):
	if command in UNSAFE_RPC:
		_deny()
	if command in _INDIRECT_RPC and form.get(_INDIRECT_RPC[command]):
		# Search/tree helpers dispatch supplied whitelisted functions internally.
		# Their positional arguments need not match the target's DocType/party.
		query = form[_INDIRECT_RPC[command]]
		if (not isinstance(query, str)
			or query in UNSAFE_RPC | _CUSTOMER_COMPANY_RPC | _COMPANY_ADDRESS_RPC | _CONTROLLER_RPC):
			_deny()
	if command in _COMPANY_ADDRESS_RPC:
		name = form.get("name")
		if not isinstance(name, str) or name not in company_names():
			_deny()
		if not frappe.has_permission("Company", "read", doc=name):
			_deny()
	if command in _CUSTOMER_COMPANY_RPC:
		_guard_customer_company(command, form)
	if command in _RAW_READ_RPC and form.get("doctype") in _RAW_LEDGERS:
		_deny()
	if command in _CONTROLLER_RPC:
		# Legacy run_doc_method gives dt precedence over its in-memory docs.
		if command == "frappe.api.v2.run_doc_method":
			doctype = _document_doctype(form.get("document"))
		else:
			doctype = form.get("dt") or _document_doctype(form.get("docs"))
		_guard_controller(doctype, form.get("method"))


def guard_request():
	"""Check authenticated RPC dispatch, including legacy cmd and API v1/v2."""
	if not is_customer_report_user():
		return
	request = getattr(frappe.local, "request", None)
	if not request or request.method == "OPTIONS":
		return
	form = getattr(frappe.local, "form_dict", None) or {}
	# application() dispatches a posted cmd before looking at the request URL.
	if form.get("cmd"):
		_guard_rpc(form["cmd"], form)
		return
	if not request.path.startswith("/api/"):
		return
	from frappe.api import API_URL_MAP, v1, v2
	from werkzeug.exceptions import HTTPException

	try:
		rule, arguments = API_URL_MAP.bind_to_environ(request.environ).match(return_rule=True)
	except HTTPException:
		# Leave malformed/unknown routes to the native dispatcher.
		return
	endpoint = rule.endpoint
	if rule.rule == "/api/v2/method/run_doc_method":
		_guard_controller(_document_doctype(form.get("document")), form.get("method"))
	elif endpoint in (v1.handle_rpc_call, v2.handle_rpc_call):
		command = arguments["method"]
		if endpoint is v1.handle_rpc_call:
			command = command.split("/")[0]
		elif arguments.get("doctype"):
			from frappe.modules.utils import load_doctype_module

			command = load_doctype_module(arguments["doctype"]).__name__ + "." + command
		_guard_rpc(command, form)
	elif endpoint in (v1.execute_doc_method, v2.execute_doc_method) or (
		endpoint is v1.read_doc and "run_method" in form
	):
		_guard_controller(arguments["doctype"], arguments.get("method") or form.get("run_method"))
	elif arguments.get("doctype") in _RAW_LEDGERS and endpoint in (
		v1.read_doc, v1.document_list, v2.read_doc, v2.document_list, v2.copy_doc, v2.count,
	):
		_deny()


@frappe.whitelist()
def account_root_company(company):
	"""Preserve the Account tree helper without exposing unassigned ancestors."""
	from erpnext.accounts.doctype.account.account import get_root_company

	if not is_customer_report_user():
		return get_root_company(company)
	allowed = company_names()
	if not isinstance(company, str) or company not in allowed:
		_deny()
	frappe.has_permission("Company", "read", doc=company, throw=True)
	return [name for name in get_root_company(company)
		if name in allowed and frappe.has_permission("Company", "read", doc=name)]


@frappe.whitelist()
def company_children(doctype, parent=None, company=None, is_root=False):
	"""Show configured Companies, including leaves under inaccessible parents.

	Unassigned ancestors are not exposed just to render the tree: an assigned
	child appears at the visible root instead. Link and direct reads use the
	same native permission query; no descendant is implicitly granted access.
	"""
	if not is_customer_report_user():
		from erpnext.setup.doctype.company.company import get_children
		return get_children(doctype, parent, company, is_root)
	if doctype != "Company":
		_deny()
	frappe.has_permission("Company", "read", throw=True)
	rows = frappe.get_list("Company", fields=["name", "parent_company"],
		order_by="name asc", limit_page_length=0)
	names = {row.name for row in rows}
	if parent and parent != "All Companies" and parent not in names:
		return []
	root = not parent or parent == "All Companies"
	parents = {row.parent_company for row in rows}
	return [frappe._dict(value=row.name, expandable=int(row.name in parents))
		for row in rows if (row.parent_company not in names if root else row.parent_company == parent)]


@frappe.whitelist()
def sales_order_events(start, end, filters=None):
	"""Use permission-aware queries even when native calendar filters are empty."""
	if not is_customer_report_user():
		from erpnext.selling.doctype.sales_order.sales_order import get_events

		return get_events(start, end, filters)
	frappe.has_permission("Sales Order", "read", throw=True)
	allowed = sorted(customer_names())
	if not allowed:
		return []
	if isinstance(filters, str):
		filters = json.loads(filters)
	if isinstance(filters, dict):
		filters = [[field, *value] if isinstance(value, (list, tuple)) else [field, "=", value]
			for field, value in filters.items()]
	elif filters is not None and not isinstance(filters, (list, tuple)):
		_deny()
	from frappe.utils import getdate

	# Mandatory restrictions are appended; client predicates cannot replace them.
	filters = list(filters or []) + [
		["Sales Order", "customer", "in", allowed],
		["Sales Order", "docstatus", "<", 2],
		["Sales Order", "skip_delivery_note", "=", 0],
		["Sales Order Item", "delivery_date", "between", [getdate(start), getdate(end)]],
	]
	rows = frappe.get_list("Sales Order", filters=filters, fields=[
		"name", "customer_name", "status", "delivery_status", "billing_status",
		"items.delivery_date as delivery_date",
	], distinct=True, limit_page_length=0)
	for row in rows:
		row.update(allDay=0, convertToUserTz=0)
	return rows
