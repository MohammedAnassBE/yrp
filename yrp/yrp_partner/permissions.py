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
	if doctype == "Warehouse":
		return True
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


def _processing_account_condition(customers, table):
	"""Only native default account metadata for exact processing Customer pairs.

	Match ERPNext's Customer -> Customer Group -> Company default fallback.
	Historical ledger accounts deliberately remain a financial-role boundary.
	The supplied subquery is built by the server's live membership policy.
	"""
	return f"""EXISTS (SELECT 1 FROM `tabParty Account` processing_default
		JOIN `tabCompany` processing_company ON processing_company.name=processing_default.company
		JOIN `tabCustomer` processing_customer ON processing_customer.name=processing_default.parent
		LEFT JOIN `tabParty Account` processing_group ON processing_group.parenttype='Customer Group'
			AND processing_group.parentfield='accounts' AND processing_group.parent=processing_customer.customer_group
			AND processing_group.company=processing_default.company
		WHERE processing_default.parenttype='Customer' AND processing_default.parentfield='accounts'
		AND processing_default.parent IN ({customers}) AND processing_default.company={table}.company
		AND ({table}.name=COALESCE(NULLIF(processing_default.account,''), NULLIF(processing_group.account,''), processing_company.default_receivable_account)
		OR {table}.name=COALESCE(NULLIF(processing_default.advance_account,''), NULLIF(processing_group.advance_account,''), processing_company.default_advance_received_account)))"""


def _condition(doctype, user, table=None, include_assigned_customers=True, include_customer_scope=True):
	"""Scope direct links plus Customer -> Retailer and linked Address/Contact rows."""
	table = table or f"`tab{doctype.replace('`', '``')}`"
	# Action roles never create record membership by themselves.
	if "YRP Partner" not in frappe.get_roles(user):
		return "1=0"
	from yrp.yrp_partner.customer_access import (
		customer_links, customer_query_condition, document_company_condition, warehouse_company_condition,
		is_customer_report_user, is_customer_user,
	)
	if include_customer_scope:
		from yrp.yrp_partner.sales_roles import registered_processing_customer_query
		manager_customers=registered_processing_customer_query(user)
		if manager_customers:
			if doctype == 'Account':
				return _processing_account_condition(manager_customers,table)
			if doctype == 'Warehouse':
				return warehouse_company_condition(user,table,customers=manager_customers)
			if doctype == 'Company':
				field=f'{table}.name' if doctype == 'Company' else f'{table}.company'
				return f"""{field} IN (SELECT manager_defaults.company FROM `tabParty Account` manager_defaults
					WHERE manager_defaults.parenttype='Customer' AND manager_defaults.parentfield='accounts'
					AND manager_defaults.parent IN ({manager_customers}))"""
			# Processing read scope does not itself grant native actions;
			# registered callbacks and native DocPerm still constrain writes.
			parent_links=frappe.get_hooks('yrp_partner_action_parent_links',{}).get(doctype,[])
			if isinstance(parent_links,str):parent_links=[parent_links]
			if parent_links:
				if len(parent_links) != 1:return '1=0'
				target,separator,fieldname=parent_links[0].rpartition('.')
				field=frappe.get_meta(doctype).get_field(fieldname) if separator else None
				if not field or field.fieldtype != 'Link' or field.options != target:return '1=0'
				parent_meta=frappe.get_meta(target)
				if not all(parent_meta.get_field(name) and parent_meta.get_field(name).fieldtype == 'Link' and parent_meta.get_field(name).options == kind
				           for name,kind in (('customer','Customer'),('company','Company'))):return '1=0'
				parent_table=f"`tab{target.replace('`','``')}`"
				parent_condition=_condition(target,user,'processing_parent')
				return f"{table}.`{fieldname}` IN (SELECT processing_parent.name FROM {parent_table} processing_parent WHERE {parent_condition})"
			if doctype == 'YRP Delivery Note' or doctype in frappe.get_hooks('yrp_partner_action_permissions',{}):
				meta=frappe.get_meta(doctype)
				if not all(meta.get_field(name) and meta.get_field(name).fieldtype == 'Link' and meta.get_field(name).options == target
				           for name,target in (('customer','Customer'),('company','Company'))):
					return '1=0'
				from yrp.yrp_partner.customer_access import customer_company_condition
				return f"({table}.customer IN ({manager_customers}) AND {customer_company_condition(f'{table}.customer',f'{table}.company')})"
	if doctype == "Warehouse":
		return warehouse_company_condition(user,table)
	if include_customer_scope and is_customer_report_user(user):
		roles = set(frappe.get_roles(user))
		if doctype in {"Company", "Account"}:
			return customer_query_condition(doctype, user, table)
		if doctype in {"Prepared Report", "Payment Ledger Entry", "Journal Entry", "Payment Entry"}:
			return "1=0"
		# Financial access follows the current Customer assignments, including
		# every Customer whose default Sales Partner is the user's partner.
		if doctype in {
			"Customer", "YRP Sales Order", "YRP Delivery Note", "Sales Invoice",
			"YRP Packing Slip", "GL Entry", "Payment Entry", "Quotation", "Account",
		}:
			return customer_query_condition(doctype, user, table)
		if customer_links(doctype) and (is_customer_user(user) or not doctype.startswith("YRP ")):
			return customer_query_condition(doctype, user, table)
		if is_customer_user(user) and doctype in {"Company", "Contact", "Address", "YRP Customer Stock"}:
			customer_condition = customer_query_condition(doctype, user, table)
			if doctype in {"Contact", "Address"} and roles & {"YRP Sales Person", "YRP Sales Partner"}:
				legacy_condition = _condition(doctype, user, table, include_customer_scope=False)
				return f"({customer_condition} OR {legacy_condition})"
			return customer_condition
	if doctype == "YRP Packing Slip":
		return f"{table}.delivery_note IN (SELECT dn.name FROM `tabYRP Delivery Note` dn WHERE {_condition('YRP Delivery Note', user, 'dn')})"
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
