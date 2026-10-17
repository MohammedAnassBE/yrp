"""Summary-owned source transitions and cancellation history."""

from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _

from yrp.yrp_retail.logic import require

_submitting = ContextVar("summary_submitting_order", default=None)


def submitting_order(order):
	return _submitting.get() is order


@contextmanager
def source_submission(order):
	token = _submitting.set(order)
	try:
		yield
	finally:
		_submitting.reset(token)


def linked_order(summary):
	return frappe.db.sql(
		"select name from `tabYRP Sales Order` "
		"where retail_order_summary=%s and docstatus<2 limit 1 for update",
		summary.name,
	)


def submit_sources(summary):
	require(
		summary.flags.submit_without_order or linked_order(summary),
		"Can't proceed without a linked Sales Order.",
	)
	for order in source_orders(summary):
		if order.docstatus == 0:
			with source_submission(order):
				order.flags.ignore_permissions = True
				order.submit()


def source_orders(summary):
	for name in sorted(row.order for row in summary.source_orders):
		order = frappe.get_doc("YRP Retail Order", name, for_update=True)
		require(order.summary == summary.name, "Retail Order is not part of this summary.")
		require(order.docstatus != 2, "A source Retail Order is cancelled.")
		yield order


def reopen_sources(summary):
	for order in source_orders(summary):
		# Legacy cancellation reopens demand rather than cancelling the source.
		order.db_set({"summary": None, "docstatus": 0})
		for row in order.items:
			row.db_set("docstatus", 0, update_modified=False)


def cancel_with_reason(summary, reason):
	"""Retain drafts through cancellation without running submission effects."""
	from yrp.yrp_retail.api import atomic

	require(isinstance(reason, str) and reason.strip(), "Enter the reason to cancel the summary.")
	require(summary.docstatus in (0, 1), "Only a draft or submitted summary can be cancelled.")
	summary.check_permission("write" if summary.docstatus == 0 else "cancel")
	with atomic():
		if summary.docstatus == 0:
			frappe.db.set_value(summary.doctype, summary.name, "docstatus", 1, update_modified=False)
		summary.cancel()
		summary.add_comment("Comment", _("Cancelled: {0}").format(reason.strip()))
