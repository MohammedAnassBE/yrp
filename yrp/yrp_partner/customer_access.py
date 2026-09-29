"""Live Customer scope for customer-facing records and financial reports.

Roles select which sources can grant access; roles alone grant no records.
Every grant needs a generated Partner membership and a current Contact email
matching an enabled login. Retailer memberships never grant Customer ledgers.
No result is cached, so reassignment and identity revocation apply immediately.
"""

import frappe

CUSTOMER_ROLE = "YRP Customer"
FINANCIAL_ROLES = {CUSTOMER_ROLE, "YRP Sales Person", "YRP Sales Partner"}
FINANCIAL_DOCTYPES = {"Sales Invoice", "GL Entry", "Payment Entry", "Quotation"}
# Generic framework Dynamic Links (Activity Log, Comment, Version) are not
# Customer business records. Restrict dynamic discovery to actual party documents.
CUSTOMER_PARTY_DOCTYPES = {"Quotation", "Opportunity", "Payment Entry", "GL Entry", "Payment Ledger Entry", "Payment Request"}
CUSTOMER_DOCTYPES = FINANCIAL_DOCTYPES | {
	"Customer", "Sales Order", "Delivery Note", "Packing Slip", "Company", "Account",
	"YRP Retailer", "YRP Customer Stock", "Contact", "Address",
	"YRP Partner", "YRP Partner Type",
	"YRP Visit", "YRP Retail Order", "YRP Retail Order Summary",
}


def _has_scoped_role(user, roles):
	if user in ("Administrator", "Guest"):
		return False
	assigned = set(frappe.get_roles(user))
	return "System Manager" not in assigned and bool(assigned & roles)


def is_customer_user(user=None):
	"""Recognize the restriction even when its required Partner role is absent."""
	return _has_scoped_role(user or frappe.session.user, {CUSTOMER_ROLE})


def is_customer_report_user(user=None):
	"""Apply the same financial boundary to all three scoped business roles."""
	return _has_scoped_role(user or frappe.session.user, FINANCIAL_ROLES)


def _source_references(doctype, user, roles):
	"""Recheck generated membership against its current source and identity."""
	role_values = ", ".join(frappe.db.escape(role) for role in sorted(roles))
	return f"""SELECT DISTINCT p.reference_name FROM `tabYRP Partner` p
		JOIN `tab{doctype.replace('`', '``')}` source_record ON source_record.name=p.reference_name
		JOIN `tabYRP Partner Type` pt ON pt.name=p.partner_type
			AND pt.reference_doctype=p.reference_doctype
		JOIN `tabYRP Partner User` m ON m.parent=p.name
			AND m.parenttype='YRP Partner' AND m.parentfield='users'
		JOIN `tabUser` actor ON actor.name=m.user AND actor.enabled=1
		WHERE actor.name={frappe.db.escape(user)}
		AND actor.name NOT IN ('Administrator', 'Guest')
		AND p.reference_doctype={frappe.db.escape(doctype)}
		AND EXISTS (SELECT 1 FROM `tabHas Role` scope_role
			WHERE scope_role.parent=actor.name AND scope_role.parenttype='User'
			AND scope_role.parentfield='roles' AND scope_role.role='YRP Partner')
		AND EXISTS (SELECT 1 FROM `tabHas Role` action_role
			WHERE action_role.parent=actor.name AND action_role.parenttype='User'
			AND action_role.parentfield='roles' AND action_role.role IN ({role_values}))
		AND EXISTS (SELECT 1 FROM `tabDynamic Link` identity_link
			JOIN `tabContact` identity_contact ON identity_contact.name=identity_link.parent
			JOIN `tabContact Email` identity_email ON identity_email.parent=identity_contact.name
				AND identity_email.parenttype='Contact' AND identity_email.parentfield='email_ids'
			WHERE identity_link.parenttype='Contact' AND identity_link.parentfield='links'
			AND identity_link.link_doctype=p.reference_doctype
			AND identity_link.link_name=p.reference_name AND identity_email.email_id=actor.email)"""


def _customer_references(user):
	direct = _source_references("Customer", user, {CUSTOMER_ROLE})
	partners = _source_references("Sales Partner", user, {"YRP Sales Partner"})
	conditions = [
		f"customer_scope.name IN ({direct})",
		f"customer_scope.default_sales_partner IN ({partners})",
	]
	if (frappe.db.table_exists("YRP Sales Person Customers")
		and frappe.get_meta("Sales Person").has_field("yrp_sales_partner")):
		people = _source_references("Sales Person", user, {"YRP Sales Person"})
		conditions.append(f"""EXISTS (SELECT 1 FROM `tabYRP Sales Person Customers` assignment
			JOIN `tabSales Person` person ON person.name=assignment.parent
			WHERE assignment.parenttype='Sales Person' AND assignment.parentfield='yrp_customers'
			AND assignment.customer=customer_scope.name AND person.name IN ({people})
			AND person.enabled=1 AND person.is_group=0
			AND COALESCE(person.yrp_sales_partner, '') != ''
			AND person.yrp_sales_partner=customer_scope.default_sales_partner)""")
	return f"""SELECT customer_scope.name FROM `tabCustomer` customer_scope
		WHERE ({' OR '.join(conditions)})"""


def customer_company_condition(customer_field, company_field):
	"""Match an exact Customer/company assignment in native Default Accounts.

	SQL identifiers come only from server-owned metadata/callers. A Company's
	parent, past transactions and site defaults never create this assignment.
	"""
	return f"""EXISTS (SELECT 1 FROM `tabParty Account` customer_company_default
		WHERE customer_company_default.parenttype='Customer'
		AND customer_company_default.parentfield='accounts'
		AND customer_company_default.parent={customer_field}
		AND customer_company_default.company={company_field})"""


def customer_names(user=None, company=None):
	"""Union current Customers, optionally narrowed to their configured Company."""
	user = user or frappe.session.user
	if not is_customer_report_user(user):
		return set()
	query = _customer_references(user)
	if company is not None:
		query += " AND " + customer_company_condition("customer_scope.name", frappe.db.escape(company))
	return set(frappe.db.sql(query, pluck=True))


def company_names(user=None, customers=None):
	"""Read companies from authorized Customers' Default Accounts, before a sale.

	An optional Customer subset can only narrow the live membership union. Empty
	defaults fail closed; credit limits and internal-company links are unrelated.
	"""
	user = user or frappe.session.user
	allowed = customer_names(user)
	if customers is not None:
		allowed &= set(customers)
	if not allowed:
		return set()
	return set(frappe.db.sql("""SELECT DISTINCT defaults.company FROM `tabParty Account` defaults
		JOIN `tabCompany` company ON company.name=defaults.company
		WHERE defaults.parenttype='Customer' AND defaults.parentfield='accounts'
		AND defaults.parent IN %(customers)s""", {"customers": sorted(allowed)}, pluck=True))


def document_company_condition(doctype, user, table):
	"""Keep a record in its Customer/company pair when it has a Company field."""
	meta = frappe.get_meta(doctype)
	company = meta.get_field("company")
	if not company or company.fieldtype != "Link" or company.options != "Company":
		return "1=1"
	conditions = []
	customers = _customer_references(user)
	for fieldname, discriminator in customer_links(doctype):
		field = f"{table}.`{fieldname.replace('`', '``')}`"
		condition = f"({field} IN ({customers}) AND {customer_company_condition(field, f'{table}.company')})"
		if discriminator:
			condition = f"({table}.`{discriminator.replace('`', '``')}`='Customer' AND {condition})"
		conditions.append(condition)
	if conditions:
		return "(" + " OR ".join(conditions) + ")"
	return f"{table}.company IN (SELECT company_scope.name FROM `tabCompany` company_scope WHERE {customer_query_condition('Company', user, 'company_scope')})"


def retailer_names(user=None):
	"""Direct retailer access is independent of upstream financial access."""
	user = user or frappe.session.user
	if not _has_scoped_role(user, FINANCIAL_ROLES | {"YRP Partner"}):
		return set()
	return set(frappe.db.sql(f"""SELECT name FROM `tabYRP Retailer`
		WHERE {customer_query_condition('YRP Retailer', user)}""", pluck=True))


def customer_links(doctype):
	"""Describe parent Customer links as (field, optional type discriminator).

	Dynamic Links are constrained to rows whose discriminator is ``Customer``.
	The role-fixture discovery caller can use this for ordinary parent DocTypes;
	child tables always inherit their parent's permission instead.
	"""
	meta = frappe.get_meta(doctype)
	if meta.istable or meta.issingle or meta.is_virtual:
		return ()
	return tuple(
		(field.fieldname, None if field.fieldtype == "Link" else field.options)
		for field in meta.fields
		if (field.fieldtype == "Link" and field.options == "Customer")
		or (doctype in CUSTOMER_PARTY_DOCTYPES and field.fieldtype == "Dynamic Link" and meta.has_field(field.options))
	)


def company_address_condition(user, table):
	"""Official addresses follow Company access, independently of its ledger.

	All scoped roles use the same Customer Default Accounts assignments, including
	before their first sale. No assignment means no official Company addresses.
	This does not grant any other Customer's address or financial records.
	"""
	if not frappe.has_permission("Company", "read", user=user):
		return "1=0"
	company_scope = customer_query_condition("Company", user, "company_address_scope")
	return f"""EXISTS (SELECT 1 FROM `tabDynamic Link` official_address
		WHERE official_address.parent={table}.name AND official_address.parenttype='Address'
		AND official_address.parentfield='links' AND official_address.link_doctype='Company'
		AND official_address.link_name IN (SELECT company_address_scope.name FROM `tabCompany` company_address_scope
			WHERE {company_scope}))"""


def customer_query_condition(doctype, user=None, table=None):
	"""Combine Customer membership with exact Customer/company assignments."""
	user = user or frappe.session.user
	table = table or f"`tab{doctype.replace('`', '``')}`"
	condition = _customer_query_condition(doctype, user, table)
	if condition != "1=0" and is_customer_report_user(user) and doctype not in {"Company", "Account"}:
		company_condition = document_company_condition(doctype, user, table)
		if company_condition != "1=1":
			return f"({condition} AND {company_condition})"
	return condition


def _customer_query_condition(doctype, user=None, table=None):
	"""Predicate shared by lists, direct reads, and scoped report queries.

	Drafts, submitted and cancelled documents retain their native visibility.
	Callers still need ordinary DocPerm grants; report-specific date/cancellation
	filters belong to the report, and internal accounting grants are separate.
	Unknown protected documents fail closed instead of inheriting source links.
	"""
	user = user or frappe.session.user
	if not _has_scoped_role(user, FINANCIAL_ROLES | {"YRP Partner"}):
		return "1=0"
	table = table or f"`tab{doctype.replace('`', '``')}`"
	customers = _customer_references(user)
	if doctype == "Customer":
		return f"{table}.name IN ({customers})"
	if doctype in {"Sales Order", "Sales Invoice", "Delivery Note"}:
		return f"{table}.customer IN ({customers})"
	if doctype == "Quotation":
		return f"({table}.quotation_to='Customer' AND {table}.party_name IN ({customers}))"
	if doctype in {"GL Entry", "Payment Entry"}:
		return f"({table}.party_type='Customer' AND {table}.party IN ({customers}))"
	if doctype == "Packing Slip":
		return f"""{table}.delivery_note IN (
			SELECT dn.name FROM `tabDelivery Note` dn
			WHERE {customer_query_condition('Delivery Note', user, 'dn')})"""
	if doctype in {"YRP Customer Stock", "YRP Retail Order Summary"}:
		return f"{table}.customer IN ({customers})"
	if doctype == "YRP Retailer":
		direct = _source_references("YRP Retailer", user, {"YRP Partner"})
		return f"({table}.name IN ({direct}) OR {table}.customer IN ({customers}))"
	if doctype in {"YRP Visit", "YRP Retail Order"}:
		retailers = customer_query_condition("YRP Retailer", user, "retailer_scope")
		return f"""({table}.customer IN ({customers}) OR {table}.retailer IN
			(SELECT retailer_scope.name FROM `tabYRP Retailer` retailer_scope WHERE {retailers}))"""
	if doctype in {"Contact", "Address"}:
		retailers = customer_query_condition("YRP Retailer", user, "retailer_scope")
		condition = f"""EXISTS (SELECT 1 FROM `tabDynamic Link` contact_scope
			WHERE contact_scope.parent={table}.name
			AND contact_scope.parenttype={frappe.db.escape(doctype)} AND contact_scope.parentfield='links'
			AND ((contact_scope.link_doctype='Customer' AND contact_scope.link_name IN ({customers}))
			OR (contact_scope.link_doctype='YRP Retailer' AND contact_scope.link_name IN
				(SELECT retailer_scope.name FROM `tabYRP Retailer` retailer_scope WHERE {retailers}))))"""
		if doctype == "Address":
			return f"({condition} OR {company_address_condition(user, table)})"
		return condition
	if doctype == "Company":
		return f"""EXISTS (SELECT 1 FROM `tabParty Account` company_default
			WHERE company_default.parenttype='Customer' AND company_default.parentfield='accounts'
			AND company_default.company={table}.name AND company_default.parent IN ({customers}))"""
	if doctype == "Account":
		# Related account metadata is readable; a shared receivable account
		# never grants its other Customers' balances or the entire chart.
		# Resolve the live union once: nesting the membership query on both
		# sides of this OR triggers MariaDB's nullable semijoin materialization.
		allowed = customer_names(user)
		if not allowed:
			return "1=0"
		customer_values = ", ".join(frappe.db.escape(value) for value in sorted(allowed))
		return f"""(EXISTS (SELECT 1 FROM `tabParty Account` account_default
			JOIN `tabCompany` account_company ON account_company.name=account_default.company
			JOIN `tabCustomer` account_customer ON account_customer.name=account_default.parent
			LEFT JOIN `tabParty Account` group_default ON group_default.parenttype='Customer Group'
				AND group_default.parentfield='accounts' AND group_default.parent=account_customer.customer_group
				AND group_default.company=account_default.company
			WHERE account_default.parenttype='Customer' AND account_default.parentfield='accounts'
			AND account_default.parent IN ({customer_values}) AND account_default.company={table}.company
			AND ({table}.name=COALESCE(NULLIF(account_default.account, ''), NULLIF(group_default.account, ''), account_company.default_receivable_account)
			OR {table}.name=COALESCE(NULLIF(account_default.advance_account, ''), NULLIF(group_default.advance_account, ''), account_company.default_advance_received_account)))
			OR EXISTS (SELECT 1 FROM `tabGL Entry` ledger_account
			WHERE ledger_account.account={table}.name AND ledger_account.company={table}.company
			AND ledger_account.party_type='Customer' AND ledger_account.party IN ({customer_values})
			AND {customer_company_condition('ledger_account.party', 'ledger_account.company')}))"""
	if doctype == "YRP Partner":
		# Show only Customer memberships; non-Customer source memberships must
		# not open another customer's ledger or unrelated staff/source records.
		return f"({table}.reference_doctype='Customer' AND {table}.reference_name IN ({customers}))"
	conditions = []
	for fieldname, discriminator in customer_links(doctype):
		field = f"{table}.`{fieldname.replace('`', '``')}`"
		condition = f"{field} IN ({customers})"
		if discriminator:
			type_field = f"{table}.`{discriminator.replace('`', '``')}`"
			condition = f"({type_field}='Customer' AND {condition})"
		conditions.append(condition)
	return "(" + " OR ".join(conditions) + ")" if conditions else "1=0"
