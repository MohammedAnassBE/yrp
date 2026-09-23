"""Read-only partner access, enforced for lists and individual documents.

Management roles grant ordinary Frappe permissions. YRP Partner narrows supported
records to derived memberships; only System Manager bypasses this restriction.
No membership means no scoped rows. Explicit Frappe document sharing remains
an administrator-controlled read exception; lifecycle guards still deny writes.
"""
import frappe

CORE_DOCTYPES = {"Sales Partner", "Sales Person", "Customer", "Employee", "YRP Retailer", "Contact", "Address", "YRP Partner", "YRP Partner Type", "YRP Visit", "YRP Retail Order", "YRP Retail Order Summary", "YRP Customer Stock"}
# Processing stays read-only for partners even when their configured source is
# only Sales Person (linked through a child table on native sales documents).
# Keep this separate from CORE_DOCTYPES so existing read scope is unchanged.
PROCESSING_DOCTYPES = {"Sales Order", "Delivery Note", "Sales Invoice", "Packing Slip"}


def is_partner(user=None):
	"""Internal administrators bypass partner scope; ordinary partner users do not."""
	user = user or frappe.session.user
	if user == "Administrator":
		return False
	roles = frappe.get_roles(user)
	return user != "Administrator" and "YRP Partner" in roles and "System Manager" not in roles


def source_types():
	return set(frappe.get_all("YRP Partner Type", pluck="reference_doctype"))


def protected(doctype):
	"""Cover core masters, configured sources, and documents directly linked to sources."""
	sources = source_types()
	return doctype in CORE_DOCTYPES | sources or any(
		field.fieldtype == "Link" and field.options in sources
		for field in frappe.get_meta(doctype).fields
	)


def _references(doctype, user):
	"""Build a subquery from current membership, never from a cached list of IDs."""
	return f"""SELECT p.reference_name FROM `tabYRP Partner` p
		JOIN `tabYRP Partner User` u ON u.parent=p.name
		WHERE u.parenttype='YRP Partner' AND u.parentfield='users'
		AND u.user={frappe.db.escape(user)} AND p.reference_doctype={frappe.db.escape(doctype)}"""


def _condition(doctype, user, table=None, include_assigned_customers=True):
	"""Scope direct links plus Customer -> Retailer and linked Address/Contact rows."""
	table = table or f"`tab{doctype.replace('`', '``')}`"
	if doctype == "YRP Partner Type":
		return "1=0"
	if doctype == "YRP Partner":
		return f"{table}.name IN (SELECT parent FROM `tabYRP Partner User` WHERE parenttype='YRP Partner' AND user={frappe.db.escape(user)})"
	conditions = [f"{table}.name IN ({_references(doctype, user)})"]
	for field in frappe.get_meta(doctype).fields:
		# Customer assignment grants salesperson access. The registering person's
		# link must not keep access alive after that assignment is revoked.
		if doctype == "YRP Retailer" and field.fieldname in ("sales_partner", "sales_person"):
			continue
		if field.fieldtype == "Link" and field.options in source_types():
			conditions.append(f"{table}.`{field.fieldname}` IN ({_references(field.options, user)})")
	if (doctype == "Customer" and include_assigned_customers
		and frappe.db.table_exists("YRP Sales Person Customers")
		and frappe.get_meta("Sales Person").has_field("yrp_sales_partner")):
		# A saved child row alone is not a grant: reassignment of either master
		# must revoke inherited access immediately, before any cleanup/backfill.
		conditions.append(f"""{table}.name IN (
			SELECT a.customer FROM `tabYRP Sales Person Customers` a
			JOIN `tabSales Person` sp ON sp.name=a.parent
			WHERE a.parenttype='Sales Person' AND a.parentfield='yrp_customers'
			AND sp.name IN ({_references('Sales Person', user)})
			AND sp.enabled=1 AND sp.is_group=0
			AND COALESCE(sp.yrp_sales_partner, '') != ''
			AND sp.yrp_sales_partner={table}.default_sales_partner
		)""")
	if doctype == "YRP Customer Stock":
		conditions.append(f"{table}.customer IN (SELECT c.name FROM `tabCustomer` c WHERE {_condition('Customer', user, 'c')})")
	if doctype == "YRP Retailer":
		# Retailers are shared by every salesperson assigned to their Customer;
		# sales_partner remains an audit/partner field, not the access boundary.
		conditions.append(f"{table}.customer IN (SELECT c.name FROM `tabCustomer` c WHERE {_condition('Customer', user, 'c')})")
	if doctype in ("Contact", "Address"):
		linked = []
		for target in sorted((CORE_DOCTYPES | source_types()) - {"Contact", "Address", "YRP Partner", "YRP Partner Type"}):
			linked.append(f"(l.link_doctype={frappe.db.escape(target)} AND l.link_name IN (SELECT r.name FROM `tab{target.replace('`', '``')}` r WHERE {_condition(target, user, 'r')}))")
		conditions.append(f"""EXISTS (SELECT 1 FROM `tabDynamic Link` l WHERE l.parent={table}.name
			AND l.parenttype={frappe.db.escape(doctype)} AND l.parentfield='links'
			AND ({' OR '.join(linked)}))""")
	return "(" + " OR ".join(conditions) + ")"


def query_conditions(user=None, doctype=None):
	"""Frappe list/report hook: filter even when role or owner permissions allow read."""
	user = user or frappe.session.user
	if not is_partner(user) or not protected(doctype):
		return ""
	return _condition(doctype, user)


def has_permission(doc, ptype=None, user=None, **kwargs):
	"""Deny writes and out-of-scope document reads, including direct REST access."""
	user = user or frappe.session.user
	if not is_partner(user):
		return True
	if doc.doctype in PROCESSING_DOCTYPES and ptype not in (None, "read", "select"):
		return False
	if not protected(doc.doctype):
		return True
	if ptype not in (None, "read", "select") or doc.is_new():
		return False
	table = f"`tab{doc.doctype.replace('`', '``')}`"
	return bool(frappe.db.sql(f"SELECT name FROM {table} WHERE name=%s AND {_condition(doc.doctype, user)} LIMIT 1", doc.name))


def prevent_partner_write(doc, *args, **kwargs):
	"""Enforce read-only writes even through APIs that use permission-bypassing saves."""
	if is_partner() and (doc.doctype in PROCESSING_DOCTYPES or protected(doc.doctype)):
		from yrp.yrp_partner.sync import _GENERATION_TOKEN

		if doc.doctype == "YRP Partner" and doc.flags.generation_token is _GENERATION_TOKEN:
			return
		from yrp.yrp_retail.customer_stock import permitted_write as stock_write

		if stock_write(doc):
			return
		from yrp.yrp_retail.access import permitted_write

		event = args[0] if args else kwargs.get("method")
		if permitted_write(doc, event):
			return
		frappe.throw("Partner users have read-only access to this document.", frappe.PermissionError)
