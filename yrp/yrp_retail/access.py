"""Authenticated salesperson identity and exact-document API write capability.

The context is server-only and cannot be supplied in JSON flags. It permits only
the requested retail save; nested saves of other documents stay restricted.
"""
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _

_write_scope = ContextVar("yrp_retail_write_scope", default=None)
API_DOCTYPES = {"YRP Retailer", "YRP Visit", "YRP Retail Order", "YRP Retail Order Summary"}


def salesperson(selected=None):
	"""Resolve an enabled leaf Sales Person from the current user's memberships."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in."), frappe.PermissionError)
	rows = frappe.db.sql("""SELECT DISTINCT p.reference_name FROM `tabYRP Partner` p
		JOIN `tabYRP Partner User` u ON u.parent=p.name
		WHERE p.reference_doctype='Sales Person' AND u.parenttype='YRP Partner'
		AND u.parentfield='users' AND u.user=%s""", frappe.session.user, pluck=True)
	if selected and selected not in rows:
		frappe.throw(_("This Sales Person is not assigned to your login."), frappe.PermissionError)
	if not selected:
		if len(rows) != 1:
			frappe.throw(_("Select one of your assigned Sales Persons."), frappe.PermissionError)
		selected = rows[0]
	doc = frappe.get_doc("Sales Person", selected)
	if not doc.enabled or doc.is_group:
		frappe.throw(_("The Sales Person must be enabled and must not be a group."))
	return doc


def require_customer(actor, customer):
	"""Read the current assignment on every call so revocations apply immediately."""
	from yrp.yrp_retail.setup import assigned_customers

	if not customer or customer not in assigned_customers(actor.name):
		frappe.throw(_("Customer is not assigned to this Sales Person."), frappe.PermissionError)


def require_owned(actor, doc):
	if doc.sales_person != actor.name:
		frappe.throw(_("This record belongs to another Sales Person."), frappe.PermissionError)
	if doc.get("customer"):
		require_customer(actor, doc.customer)


@contextmanager
def allow_write(doc, actor, operation="save"):
	"""Issue a temporary capability after the API has checked actor and ownership."""
	if doc.doctype not in API_DOCTYPES or doc.sales_person != actor.name:
		frappe.throw(_("Unsupported retail operation."), frappe.PermissionError)
	token = _write_scope.set((doc, operation))
	try:
		yield
	finally:
		_write_scope.reset(token)


def permitted_write(doc, event=None):
	"""Check identity, not docname/flags, and prohibit unrequested lifecycle actions."""
	scope = _write_scope.get()
	if not scope or scope[0] is not doc:
		return False
	operation = scope[1]
	return event in ({"before_validate"} if operation == "save" else {"before_validate", "before_submit"})
