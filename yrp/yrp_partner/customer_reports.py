"""Bound native reports to the current partner's customer memberships.

Frappe v16 desk/query_report.py checks Report.is_permitted before choosing the
prepared result path, and exports re-run that same path. Report controller
extensions therefore keep live and exported results under the same boundary.
PreparedReport.before_insert also runs for make_prepared_report's privileged
insert; cached attachments are deliberately unavailable to these roles.

ERPNext GL get_conditions and ReceivablePayableReport.add_common_filters apply
party_type/party before opening balances, ageing, subtotals and charts. Native
AR's invoice lookup also requests Journal Entry read permission; the subclass
below replaces only that lookup with scoped Sales Invoice metadata. Summary's
native GL comparison and future-payment queries are not customer-safe. The
bounded summary retains native ageing/aggregation and scopes advances itself.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from yrp.yrp_partner.customer_access import company_names, customer_names, is_customer_report_user


CUSTOMER_REPORTS = {
	"General Ledger": "GL Entry",
	"Accounts Receivable": "Sales Invoice",
	"Accounts Receivable Summary": "Sales Invoice",
	"YRP Sales Order Fulfilment": "Sales Order",
	"YRP Packing and Delivery": "Packing Slip",
	"YRP Retail Demand": "YRP Retail Order",
	"YRP Retail Summary Allocation": "YRP Retail Order Summary",
}
_RETAIL_KINDS = {
	"YRP Sales Order Fulfilment": "fulfilment",
	"YRP Packing and Delivery": "packing",
	"YRP Retail Demand": "demand",
	"YRP Retail Summary Allocation": "allocation",
}

_GL_FIELDS = frozenset({
	"posting_date", "account", "debit", "credit", "balance", "voucher_type",
	"voucher_subtype", "voucher_no", "party_type", "party", "party_name",
	"debit_in_account_currency", "credit_in_account_currency", "account_currency",
	"presentation_currency", "debit_in_transaction_currency", "credit_in_transaction_currency",
	"transaction_currency",
})
_AR_FIELDS = frozenset({
	"posting_date", "party_type", "party", "customer_name", "party_name",
	"voucher_type", "voucher_no", "due_date", "payment_term", "invoice_grand_total",
	"invoiced", "paid", "credit_note", "outstanding", "age", "currency", "po_no",
	"advance", "total_due", "bold", "indent",
})
_DELIVERY_FIELDS = frozenset({
	"document", "date", "status", "customer", "item_code", "uom", "qty",
	"delivered_qty", "pending_qty", "delivery_note", "from_case_no", "to_case_no",
	"delivered_at", "requested_qty", "customer_stock_qty", "company_qty", "ordered_qty",
	"sales_person", "order_type", "retailer",
})


def _deny():
	frappe.throw(_("This report option is unavailable for customer-scoped access."), frappe.PermissionError)


def _parse(value):
	if value is None or value == "":
		return None
	if isinstance(value, str):
		try:
			return json.loads(value)
		except (TypeError, ValueError):
			_deny()
	return value


def _request_options(custom_columns=None, user=None):
	"""Reject expansion after the report executor, including saved/async exports."""
	if user and user != frappe.session.user:
		_deny()
	if _parse(custom_columns):
		_deny()
	form = getattr(frappe.local, "form_dict", None) or {}
	if _parse(form.get("custom_columns")) or cint(form.get("export_in_background")):
		_deny()
	if form.get("user") and form["user"] != frappe.session.user:
		_deny()


def _selected_names(value):
	if not value:
		return set()
	if isinstance(value, str):
		value = _parse(value) if value.startswith("[") else [value]
	if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v for v in value):
		_deny()
	return set(value)


def scoped_filters(filters=None, require_company=False):
	"""Own the Customer/company pair constraints before any native aggregation."""
	filters = _parse(filters) if filters else {}
	if not isinstance(filters, dict):
		_deny()
	filters = frappe._dict(filters.copy())
	allowed = set(customer_names())
	if not allowed or filters.get("party_type") not in (None, "", "Customer"):
		_deny()
	if filters.get("prepared_report_name"):
		_deny()
	selected = allowed.copy()
	explicit_selection = False
	for field in ("party", "customer"):
		requested = _selected_names(filters.get(field))
		if requested - allowed:
			_deny()
		if requested:
			explicit_selection = True
			selected &= requested
	if not selected:
		_deny()
	company = filters.get("company")
	if company is None or company == "":
		# Native AR falls back to Global Defaults, which need not be authorized.
		# Retail reports may omit Company because their document queries enforce
		# the complete live Customer/company boundary for every returned record.
		if require_company:
			_deny()
	else:
		if not isinstance(company, str) or company not in company_names(customers=selected):
			_deny()
		company_customers = set(customer_names(company=company))
		if explicit_selection and selected - company_customers:
			_deny()
		selected = selected & company_customers
		if not selected:
			_deny()
	filters.party_type = "Customer"
	filters.party = sorted(selected)
	filters.pop("customer", None)
	# Never read comments/counterparty metadata or the unsafe auxiliary queries.
	for field in ("show_remarks", "include_dimensions", "show_gl_balance",
			"show_future_payments", "show_sales_person", "show_delivery_notes"):
		filters[field] = 0
	return filters


def _project(report_name, result):
	"""Drop internal columns AND undeclared row keys (JSON/export hidden data)."""
	parts = list(result)
	columns, rows = parts[:2]
	allowed = _GL_FIELDS if report_name == "General Ledger" else (
		_AR_FIELDS if report_name.startswith("Accounts Receivable") else _DELIVERY_FIELDS
	)
	allowed = set(allowed)
	# Native ageing buckets are generated from the user-selected range.
	if report_name.startswith("Accounts Receivable"):
		allowed.update(c["fieldname"] for c in columns if c.get("fieldname", "").startswith("range")
			and c["fieldname"][5:].isdigit())
	fields = [c["fieldname"] for c in columns]
	parts[0] = [c for c in columns if c["fieldname"] in allowed]
	parts[1] = [
		{k: v for k, v in (dict(zip(fields, row)) if isinstance(row, (list, tuple)) else row).items()
			if k in allowed}
		for row in rows
	]
	return tuple(parts)


def _receivable_class():
	from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport

	class CustomerReceivables(ReceivablePayableReport):
		def set_defaults(self):
			super().set_defaults()
			self.party_type = ["Customer"]

		def get_invoice_details(self):
			# Journal Entry headers may describe several customers. PLE already
			# provides the customer row's due date and amounts without that grant.
			rows = frappe.get_all("Sales Invoice", filters={
				"customer": ["in", self.filters.party], "company": self.filters.company,
				"posting_date": ["<=", self.filters.report_date], "docstatus": 1,
			}, fields=["name", "due_date", "po_no"])
			self.invoice_details = frappe._dict({row.name: row for row in rows})

	return CustomerReceivables


def _receivable_args():
	return {"account_type": "Receivable", "naming_by": ["Selling Settings", "cust_master_name"]}


def _receivable_summary(filters):
	# Summary's native advance helper returns company-currency amounts. The
	# detail engine switches to account currency for either of these options,
	# so subtracting that advance would produce mixed-currency paid totals.
	# Neither option is part of the native Summary filter UI.
	if filters.get("in_party_currency") or filters.get("party_account"):
		_deny()
	from erpnext.accounts.party import get_partywise_advanced_payment_amount
	from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
		AccountsReceivableSummary,
	)
	from erpnext.accounts.utils import get_currency_precision

	class CustomerSummary(AccountsReceivableSummary):
		def get_data(self, args):
			self.filters.group_by_party = 0
			receivable_report = _receivable_class()(self.filters)
			self.receivables = receivable_report.run(args)[1]
			self.filters.company = receivable_report.filters.company
			self.currency_precision = get_currency_precision() or 2
			self.get_party_total(args)
			self.data = []
			for party, amounts in self.party_total.items():
				if flt(amounts.outstanding, self.currency_precision) == 0:
					continue
				# Native helper only supports one party. Each aggregate is bounded
				# to Customer + this authorized customer before computing advances.
				advance = get_partywise_advanced_payment_amount(
					["Customer"], self.filters.report_date, 0, self.filters.company, party=party,
				) or {}
				row = frappe._dict(amounts)
				row.party = party
				row.advance = advance.get(party, 0)
				row.paid -= row.advance
				if self.party_naming_by == "Naming Series":
					row.party_name = frappe.get_cached_value("Customer", party, "customer_name")
				self.data.append(row)

	return CustomerSummary(filters).run(_receivable_args())


def _execute(report_name, filters):
	if report_name == "General Ledger":
		from erpnext.accounts.report.general_ledger.general_ledger import execute
		return execute(filters)
	if report_name == "Accounts Receivable":
		return _receivable_class()(filters).run(_receivable_args())
	if report_name == "Accounts Receivable Summary":
		return _receivable_summary(filters)
	from yrp.yrp_retail.reporting import run as retail_report
	parts, result = None, []
	for customer in filters.party:
		current = filters.copy()
		current["customer"] = customer
		parts = retail_report(_RETAIL_KINDS[report_name], current)
		result.extend(parts[1])
	return parts[0], result, parts[2]


class CustomerReportMixin:
	def is_permitted(self):
		if not is_customer_report_user():
			return super().is_permitted()
		_request_options()
		if (self.name not in CUSTOMER_REPORTS or not customer_names()
				or self.ref_doctype != CUSTOMER_REPORTS[self.name]
				or self.report_type != "Script Report" or self.is_standard != "Yes"
				or self.get("disabled") or self.get("snapshot_report")
				or self.get("custom_report") or self.get("is_custom_report")
				or self.get("custom_columns") or self.get("custom_filters")):
			return False
		# Only this in-memory Report instance changes, never stored settings.
		self.prepared_report = 0
		self.disable_prepared_report_automation = 1
		# Scope narrows the configured report grant; it never restores a role
		# removed by an administrator from this report's allowed roles.
		return super().is_permitted()

	def execute_script_report(self, filters):
		if not is_customer_report_user():
			return super().execute_script_report(filters)
		return self.execute_module(filters)

	def execute_module(self, filters):
		if not is_customer_report_user():
			return super().execute_module(filters)
		if not self.is_permitted():
			_deny()
		return _project(self.name, _execute(self.name, scoped_filters(
			filters, require_company=self.name not in _RETAIL_KINDS,
		)))

	def execute_query_report(self, filters):
		if is_customer_report_user():
			_deny()
		return super().execute_query_report(filters)

	def execute_script(self, filters):
		if is_customer_report_user():
			_deny()
		return super().execute_script(filters)

	def execute_snapshot_report(self, filters):
		if is_customer_report_user():
			_deny()
		return super().execute_snapshot_report(filters)

	def get_data(self, filters=None, limit=None, user=None, as_dict=False,
			ignore_prepared_report=False, are_default_filters=True):
		if is_customer_report_user():
			_request_options(user=user)
			if not self.is_permitted():
				_deny()
		return super().get_data(filters, limit, user, as_dict, ignore_prepared_report, are_default_filters)

	def run_standard_report(self, filters, limit, user):
		if is_customer_report_user():
			_deny()
		return super().run_standard_report(filters, limit, user)


class CustomerPreparedReportMixin:
	def before_insert(self):
		if is_customer_report_user():
			_deny()
		return super().before_insert()

	def get_prepared_data(self, with_file_name=False):
		if is_customer_report_user():
			_deny()
		return super().get_prepared_data(with_file_name=with_file_name)


@frappe.whitelist()
def run(report_name: str, filters=None, user=None, ignore_prepared_report=False,
		custom_columns=None, is_tree=False, parent_field=None, are_default_filters=True, js_filters=None):
	from frappe.desk.query_report import run as native_run
	if is_customer_report_user():
		_request_options(custom_columns, user)
	return native_run(report_name, filters, user, ignore_prepared_report, custom_columns,
		is_tree, parent_field, are_default_filters, js_filters)


@frappe.whitelist()
def export_query():
	from frappe.desk.query_report import export_query as native_export
	if is_customer_report_user():
		_request_options()
	return native_export()
