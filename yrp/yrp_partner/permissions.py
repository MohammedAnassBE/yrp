"""Partner record scope with explicit, state-limited sales action roles.

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
PROCESSING_DOCTYPES = {"Sales Order", "Delivery Note", "Sales Invoice", "Packing Slip", "Stock Reservation Entry", "Delivery Schedule Item"}


def is_partner(user=None):
	"""Internal administrators bypass partner scope; ordinary partner users do not."""
	user = user or frappe.session.user
	if user == "Administrator":
		return False
	roles = frappe.get_roles(user)
	from yrp.yrp_partner.sales_roles import ACTION_ROLES
	return bool(set(roles) & ({"YRP Partner", "YRP Customer"} | ACTION_ROLES)) and "System Manager" not in roles


def source_types():
	return set(frappe.get_all("YRP Partner Type", pluck="reference_doctype"))


def protected(doctype):
	"""Cover core masters, configured sources, and documents directly linked to sources."""
	from yrp.yrp_partner.customer_access import CUSTOMER_DOCTYPES, customer_links
	if doctype in CUSTOMER_DOCTYPES | {"Prepared Report", "Payment Ledger Entry", "Journal Entry"} or customer_links(doctype):
		return True
	sources = source_types()
	return doctype in CORE_DOCTYPES | PROCESSING_DOCTYPES | sources or any(
		field.fieldtype == "Link" and field.options in sources
		for field in frappe.get_meta(doctype).fields
	)


def _references(doctype, user):
	"""Build a subquery from current membership, never from a cached list of IDs."""
	from yrp.yrp_partner.customer_access import _source_references
	return _source_references(doctype, user, {"YRP Partner"})


def _condition(doctype, user, table=None, include_assigned_customers=True, include_customer_scope=True):
	"""Scope direct links plus Customer -> Retailer and linked Address/Contact rows."""
	table = table or f"`tab{doctype.replace('`', '``')}`"
	# Action roles never create record membership by themselves.
	if "YRP Partner" not in frappe.get_roles(user):
		return "1=0"
	from yrp.yrp_partner.customer_access import (
		customer_links, customer_query_condition, document_company_condition,
		is_customer_report_user, is_customer_user,
	)
	if include_customer_scope and is_customer_report_user(user):
		roles = set(frappe.get_roles(user))
		if doctype in {"Company", "Account"}:
			return customer_query_condition(doctype, user, table)
		if doctype in {"Prepared Report", "Payment Ledger Entry", "Journal Entry", "Payment Entry"}:
			return "1=0"
		# Financial access follows the current Customer assignments, even if an
		# older invoice/order still stores a previous Sales Partner.
		keep_partner_dispatch_scope = (
			doctype in {"Sales Order", "Delivery Note", "Packing Slip"}
			and "YRP Sales Partner" in roles
			and not roles & {"YRP Customer", "YRP Sales Person"}
		)
		if not keep_partner_dispatch_scope and doctype in {
			"Customer", "Sales Order", "Delivery Note", "Sales Invoice",
			"Packing Slip", "GL Entry", "Payment Entry", "Quotation", "Account",
		}:
			return customer_query_condition(doctype, user, table)
		if not keep_partner_dispatch_scope and customer_links(doctype) and (is_customer_user(user) or not doctype.startswith("YRP ")):
			return customer_query_condition(doctype, user, table)
		if is_customer_user(user) and doctype in {"Company", "Contact", "Address", "YRP Customer Stock"}:
			customer_condition = customer_query_condition(doctype, user, table)
			if doctype in {"Contact", "Address"} and roles & {"YRP Sales Person", "YRP Sales Partner"}:
				legacy_condition = _condition(doctype, user, table, include_customer_scope=False)
				return f"({customer_condition} OR {legacy_condition})"
			return customer_condition
	if doctype == "Packing Slip":
		return f"{table}.delivery_note IN (SELECT dn.name FROM `tabDelivery Note` dn WHERE {_condition('Delivery Note', user, 'dn')})"
	if doctype in ("Sales Order", "Delivery Note") and "YRP Sales Partner" in frappe.get_roles(user):
		partners = _references("Sales Partner", user)
		return f"""({table}.sales_partner IN ({partners}) OR
			(COALESCE({table}.sales_partner, '')='' AND {table}.customer IN
			(SELECT c.name FROM `tabCustomer` c WHERE c.default_sales_partner IN ({partners}))))
			AND {document_company_condition(doctype, user, table)}"""
	if doctype == "YRP Partner Type":
		return "1=0"
	if doctype == "YRP Partner":
		return f"{table}.name IN (SELECT parent FROM `tabYRP Partner User` WHERE parenttype='YRP Partner' AND user={frappe.db.escape(user)})"
	conditions = [f"{table}.name IN ({_references(doctype, user)})"]
	sources = source_types()
	for field in frappe.get_meta(doctype).fields:
		# Customer assignment grants salesperson access. The registering person's
		# link must not keep access alive after that assignment is revoked.
		if doctype == "YRP Retailer" and field.fieldname in ("sales_partner", "sales_person"):
			continue
		if field.fieldtype == "Link" and field.options in sources:
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
		if doctype == "Address" and is_customer_report_user(user):
			from yrp.yrp_partner.customer_access import company_address_condition
			conditions.append(company_address_condition(user, table))
		linked = []
		for target in sorted((CORE_DOCTYPES | sources) - {"Contact", "Address", "YRP Partner", "YRP Partner Type"}):
			linked.append(f"(l.link_doctype={frappe.db.escape(target)} AND l.link_name IN (SELECT r.name FROM `tab{target.replace('`', '``')}` r WHERE {_condition(target, user, 'r')}))")
		conditions.append(f"""EXISTS (SELECT 1 FROM `tabDynamic Link` l WHERE l.parent={table}.name
			AND l.parenttype={frappe.db.escape(doctype)} AND l.parentfield='links'
			AND ({' OR '.join(linked)}))""")
	condition = "(" + " OR ".join(conditions) + ")"
	if is_customer_report_user(user):
		condition = f"({condition} AND {document_company_condition(doctype, user, table)})"
	return condition


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
	from yrp.yrp_partner.customer_access import is_customer_report_user
	if is_customer_report_user(user) and doc.doctype in {"Payment Ledger Entry", "Journal Entry", "Payment Entry"}:
		# Report-level print permission must not expose raw ledger documents.
		# Journal/payment headers can contain bank and other-party details.
		return False
	if not protected(doc.doctype):
		return True
	if doc.is_new() and ptype is None:
		# Desk asks for the form's role permissions before the user has selected
		# any links. Allow editing the blank form; insert/create and lifecycle
		# checks still validate the complete record scope on the server.
		from yrp.yrp_partner.sales_roles import ROLE_PERMISSIONS
		roles = set(frappe.get_roles(user))
		return "YRP Partner" in roles and any(
			role in roles and "create" in doctypes.get(doc.doctype, ())
			for role, doctypes in ROLE_PERMISSIONS.items()
		)
	if ptype not in (None, "read", "select", "print", "export", "report") or doc.is_new():
		from yrp.yrp_partner.sales_roles import allowed_action
		return allowed_action(doc, ptype or "create", user)
	table = f"`tab{doc.doctype.replace('`', '``')}`"
	return bool(frappe.db.sql(f"SELECT name FROM {table} WHERE name=%s AND {_condition(doc.doctype, user)} LIMIT 1", doc.name))


def prevent_partner_write(doc, *args, **kwargs):
	"""Enforce read-only writes even through APIs that use permission-bypassing saves."""
	if is_partner() and (doc.doctype in PROCESSING_DOCTYPES or protected(doc.doctype)):
		from yrp.yrp_partner.sync import _GENERATION_TOKEN

		if doc.doctype == "YRP Partner" and doc.flags.generation_token is _GENERATION_TOKEN:
			return
		from yrp.yrp_partner.sales_roles import permitted_event

		event = args[0] if args else kwargs.get("method")
		if permitted_event(doc, event):
			return
		from yrp.yrp_retail.customer_stock import permitted_write as stock_write

		if stock_write(doc):
			return
		frappe.throw("Your Partner roles do not allow this action on this record.", frappe.PermissionError)
