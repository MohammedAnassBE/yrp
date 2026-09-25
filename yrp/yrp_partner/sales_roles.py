"""Sales action roles layered on Contact-derived Partner record scope.

DocPerm controls available actions. These checks only narrow those permissions:
the actor must still have YRP Partner membership, valid assignments and a valid
document state. Nothing here creates Users or assigns roles to them.
"""
import frappe
from frappe.utils import cint

SALES_PERSON = "YRP Sales Person"
SALES_PARTNER = "YRP Sales Partner"
ACTION_ROLES = {SALES_PERSON, SALES_PARTNER}
RETAIL_DOCUMENTS = {"YRP Retailer", "YRP Visit", "YRP Retail Order", "YRP Retail Order Summary"}
ORDER_DOCUMENTS = {"YRP Retail Order", "YRP Retail Order Summary"}

# Maximum action boundaries; actual grants are native Custom DocPerm fixtures.
# Removing a native grant takes effect immediately. These caps prevent additive
# roles from granting submission/stock processing to scoped sales partners.
ROLE_PERMISSIONS = {
	SALES_PERSON: {
		"YRP Retailer": ("read", "create", "write"),
		"YRP Visit": ("read", "create", "write"),
		"YRP Retail Order": ("read", "create", "write", "submit", "cancel"),
		"YRP Retail Order Summary": ("read", "create", "write", "submit", "cancel"),
	},
	SALES_PARTNER: {
		"Sales Order": ("read", "create", "write"),
		"Packing Slip": ("read",),
	},
}


def references(doctype, user):
	"""Resolve current memberships only for enabled logins with the scope role."""
	from yrp.yrp_partner.customer_access import _source_references
	return set(frappe.db.sql(_source_references(doctype, user, {"YRP Partner"}), pluck=True))


def _retail_scope(doc, user):
	from yrp.yrp_retail.setup import assigned_customers

	people = references("Sales Person", user)
	if not people:
		return False
	# The source Visit supplies read-only headers on a new Retail Order, even
	# when a client posts only its Visit and items. Check it before validation.
	if doc.doctype == "YRP Retail Order" and doc.get("visit"):
		visit = frappe.db.get_value("YRP Visit", doc.visit, ["sales_person", "customer"], as_dict=True)
		if not visit or any(doc.get(key) and doc.get(key) != visit[key] for key in visit):
			return False
		person, customer = visit.sales_person, visit.customer
	else:
		person, customer = doc.get("sales_person"), doc.get("customer")
		if not customer and doc.doctype == "YRP Visit" and doc.get("retailer"):
			customer = frappe.db.get_value("YRP Retailer", doc.retailer, "customer")
	if doc.doctype == "YRP Retailer" and not doc.is_new():
		# Any assigned salesperson may maintain the shared shop. The original
		# registering Sales Person remains provenance, not an access boundary.
		return any(customer in assigned_customers(value) for value in people)
	return person in people and customer in assigned_customers(person)


def _sales_partner_scope(doc, user):
	partners = references("Sales Partner", user)
	if not partners or not doc.get("customer"):
		return False
	assigned = frappe.db.get_value("Customer", doc.customer, "default_sales_partner")
	return assigned in partners and (not doc.get("sales_partner") or doc.sales_partner == assigned)


def allowed_action(doc, ptype, user=None):
	"""Check both stored and incoming scope; payload edits cannot steal a record."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if not (roles & ACTION_ROLES) or "YRP Partner" not in roles:
		return False
	if doc.doctype in RETAIL_DOCUMENTS and SALES_PERSON in roles:
		scope = _retail_scope
		allowed = set(ROLE_PERMISSIONS[SALES_PERSON][doc.doctype])
	elif doc.doctype == "Sales Order" and SALES_PARTNER in roles:
		scope = _sales_partner_scope
		allowed = set(ROLE_PERMISSIONS[SALES_PARTNER][doc.doctype])
	else:
		return False
	if ptype not in allowed or ptype == "read":
		return False
	stored = None if doc.is_new() else frappe.get_doc(doc.doctype, doc.name)
	# Lifecycle guards also respect administrator changes to native DocPerm.
	# Use persisted ownership, never an owner supplied in an update payload.
	from frappe.permissions import get_role_permissions
	role_permissions = get_role_permissions(frappe.get_meta(doc.doctype), user=user,
		is_owner=((stored or doc).get("owner") or "").lower() == user.lower())
	if not (role_permissions.get(ptype) or role_permissions.get("if_owner", {}).get(ptype)):
		return False
	if not scope(doc, user) or (stored and not scope(stored, user)):
		return False
	# Company selection is an assignment, not a browser-only filter. Recheck
	# both payload and persisted document so forged drafts cannot cross it.
	from yrp.yrp_partner.customer_access import company_names
	for candidate in (doc, stored):
		if candidate is not None and candidate.meta.has_field("company"):
			if candidate.get("company") not in company_names(user, customers=[candidate.get("customer")]):
				return False
	if stored and doc.doctype in RETAIL_DOCUMENTS:
		# Retailer sharing permits details edits, not reassignment/ownership edits.
		if any(doc.get(key) != stored.get(key) for key in ("sales_person", "customer")):
			return False
	status = cint(stored.docstatus) if stored else 0
	if ptype == "create":
		return stored is None and cint(doc.docstatus) == 0
	if ptype in ("submit", "cancel"):
		return doc.doctype in ORDER_DOCUMENTS and status == (0 if ptype == "submit" else 1) and (
			ptype != "cancel" or business_values(stored) == business_values(doc))
	if ptype == "write":
		# Frappe checks write before submit/cancel. This is only the transition
		# gate; before_submit/before_cancel check the action separately below.
		if doc.doctype in ORDER_DOCUMENTS and status == 1 and cint(doc.docstatus) == 2:
			return allowed_action(doc, "cancel", user)
		return status == 0 and cint(doc.docstatus) == 0 or (
			doc.doctype in ORDER_DOCUMENTS and status == 0 and cint(doc.docstatus) == 1
			and allowed_action(doc, "submit", user))
	return False


def business_values(doc):
	"""Compare cancellation content without native status/timestamp fields.

	Frappe cancellation skips validate() and can receive a full form payload.
	Normalize native field types and include row identities/order so cancelling
	cannot also edit the submitted business record.
	"""
	values = doc.get_valid_dict(convert_dates_to_str=True)
	result = {df.fieldname: values[df.fieldname] for df in doc.meta.fields if df.fieldname in values}
	for df in doc.meta.get_table_fields():
		result[df.fieldname] = [(row.name, business_values(row)) for row in doc.get(df.fieldname)]
	return result


def prepare_sales_order(doc, method=None):
	"""Fill the native partner field from its Customer for scoped draft creators."""
	from yrp.yrp_partner.permissions import is_partner
	if is_partner() and SALES_PARTNER in frappe.get_roles() and not doc.get("sales_partner"):
		doc.sales_partner = frappe.db.get_value("Customer", doc.customer, "default_sales_partner")


def permitted_event(doc, event):
	"""Map real lifecycle events, never client flags, to the action being allowed."""
	if event in ("before_validate", "before_save"):
		ptype = "create" if doc.is_new() else "write"
	elif event == "before_submit":
		ptype = "submit"
	elif event == "before_cancel":
		ptype = "cancel"
	else:
		return False
	return allowed_action(doc, ptype)
