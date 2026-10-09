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

# Maximum action boundaries; actual grants are native DocPerm rows.
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
		"YRP Sales Order": ("read", "create", "write"),
		"YRP Packing Slip": ("read",),
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


def registered_processing_customer_query(user):
	"""Live Contact membership for registered processing operations, never finance."""
	roles=set(frappe.get_roles(user))
	if 'YRP Partner' not in roles or not roles & {'Sales Manager','Sales User'} or roles & {'System Manager','YRP Customer','YRP Sales Person','YRP Sales Partner'}:
		return None
	from yrp.yrp_partner.permissions import _condition
	condition=_condition('Customer',user,'manager_customer',include_customer_scope=False)
	return f'SELECT manager_customer.name FROM `tabCustomer` manager_customer WHERE {condition}'


def registered_manager_customer_query(user):
    """Compatibility for manager-only callers; shared processing scope is broader."""
    if 'Sales Manager' not in frappe.get_roles(user):return None
    return registered_processing_customer_query(user)


def processing_customer_names(user=None, company=None):
	"""Current processing-only Customer pairs; financial role policy is untouched."""
	user=user or frappe.session.user
	query=registered_processing_customer_query(user)
	if query is None:
		from yrp.yrp_partner.customer_access import customer_names
		return customer_names(user,company=company)
	if company is not None:
		from yrp.yrp_partner.customer_access import customer_company_condition
		query+=' AND '+customer_company_condition('manager_customer.name',frappe.db.escape(company))
	return set(frappe.db.sql(query,pluck=True))


def extension_action(doc, ptype, user):
	"""Registered server extensions may narrow native actions on scoped documents.

	No handler means the existing cap remains authoritative. Every grant requires
	actual native DocPerm and the same current/stored Customer-company assignment.
	Handlers enforce their document-specific state and exact business diff.
	"""
	handlers=frappe.get_hooks("yrp_partner_action_permissions", {}).get(doc.doctype, [])
	if not handlers:
		return None
	if isinstance(handlers,str):
		handlers=[handlers]
	if ptype not in ("create","write","submit","cancel","delete"):
		return False
	parent_links=frappe.get_hooks('yrp_partner_action_parent_links',{}).get(doc.doctype,[])
	if isinstance(parent_links,str):parent_links=[parent_links]
	if parent_links:
		if len(parent_links) != 1:return False
		target,separator,fieldname=parent_links[0].rpartition('.')
		field=doc.meta.get_field(fieldname) if separator else None
		if not field or field.fieldtype != 'Link' or field.options != target:return False
	else:
		for name,target in (('customer','Customer'),('company','Company')):
			field=doc.meta.get_field(name)
			if not field or field.fieldtype != 'Link' or field.options != target:return False
	stored=None if doc.is_new() or not doc.get('name') else frappe.get_doc(doc.doctype,doc.name)
	from frappe.permissions import get_role_permissions
	permissions=get_role_permissions(frappe.get_meta(doc.doctype),user=user,
		is_owner=((stored or doc).get("owner") or "").lower() == user.lower())
	if not (permissions.get(ptype) or permissions.get("if_owner",{}).get(ptype)):
		return False
	if parent_links and stored and doc.get(fieldname) != stored.get(fieldname):return False
	for candidate in (doc,stored):
		if candidate is None:continue
		if parent_links:
			if not candidate.get(fieldname):return False
			parent=frappe.get_doc(target,candidate.get(fieldname))
			if not all(parent.meta.get_field(name) and parent.meta.get_field(name).fieldtype == 'Link' and parent.meta.get_field(name).options == kind
			           for name,kind in (('customer','Customer'),('company','Company'))):return False
			if not parent.has_permission('read',user=user):return False
			candidate=parent
		if not candidate.get('company') or candidate.get('customer') not in processing_customer_names(user,company=candidate.company):return False
	if not parent_links and stored and any(doc.get(field) != stored.get(field) for field in ('customer','company')):return False
	for handler in handlers:
		answer=frappe.get_attr(handler)(doc,ptype,user,stored)
		if answer is not None:
			return answer is True
	return None


def allowed_action(doc, ptype, user=None):
	"""Check both stored and incoming scope; payload edits cannot steal a record."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if "YRP Partner" not in roles:
		return False
	extended=extension_action(doc,ptype,user)
	if extended is not None:
		return extended
	if not roles & ACTION_ROLES:
		return False
	if doc.doctype in RETAIL_DOCUMENTS and SALES_PERSON in roles:
		scope = _retail_scope
		allowed = set(ROLE_PERMISSIONS[SALES_PERSON][doc.doctype])
	elif doc.doctype == "YRP Sales Order" and SALES_PARTNER in roles:
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


def permitted_event(doc, event):
	"""Map real lifecycle events, never client flags, to the action being allowed."""
	if event in ("before_validate", "before_save", "before_update_after_submit"):
		ptype = "create" if doc.is_new() else "write"
	elif event == "before_submit":
		ptype = "submit"
	elif event == "before_cancel":
		ptype = "cancel"
	elif event == "on_trash":
		ptype = "delete"
	else:
		return False
	return allowed_action(doc, ptype)
