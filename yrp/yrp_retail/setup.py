"""Sales Person assignments; static integration fields are installed as fixtures."""
import frappe
from frappe import _


def validate_sales_person(doc, method=None):
	"""A leaf belongs to one partner and may cover that partner's Customers only."""
	rows = doc.get("yrp_customers", [])
	customers = [row.customer for row in rows]
	if len(customers) != len(set(customers)):
		frappe.throw("A Customer can only be assigned once to this Sales Person.")
	if doc.is_group and customers:
		frappe.throw("Assign Customers to individual Sales Persons, not groups.")
	if doc.is_group:
		return
	partner = doc.get("yrp_sales_partner")
	if not partner:
		frappe.throw(_("Select a Sales Partner for this Sales Person before assigning Customers."))
	if not customers:
		return
	if not all(customers):
		frappe.throw(_("Select a Customer in every Assigned Customers row."))
	matching = set(frappe.get_all("Customer", filters={
		"name": ["in", customers], "default_sales_partner": partner,
	}, pluck="name"))
	for row in rows:
		if row.customer not in matching:
			frappe.throw(_("Row {0}: Customer {1} does not belong to Sales Partner {2}.").format(
				row.idx, row.customer, partner))


def assigned_customers(sales_person):
	"""Read current valid assignments, including the live Customer partner link.

	A Customer or Sales Person may have been reassigned since a child row was saved.
	Stale rows never grant API access; independent direct partner grants are handled
	by the permission layer. Read the database rather than a cached actor document.
	"""
	if not sales_person or not frappe.get_meta("Sales Person").has_field("yrp_sales_partner"):
		return set()
	return set(frappe.db.sql("""
		SELECT c.name FROM `tabYRP Sales Person Customers` a
		JOIN `tabSales Person` sp ON sp.name=a.parent
		JOIN `tabCustomer` c ON c.name=a.customer
		WHERE a.parenttype='Sales Person' AND a.parentfield='yrp_customers'
		AND sp.name=%s AND sp.enabled=1 AND sp.is_group=0
		AND COALESCE(sp.yrp_sales_partner, '') != ''
		AND c.default_sales_partner=sp.yrp_sales_partner
	""", sales_person, pluck=True))
