"""Repeatable Retail integration fields and assignment validation."""
import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup_retail():
	"""Install the single partner link before the existing Customer assignment grid.

	Groups remain native Sales Person tree containers. A leaf's partner is explicit;
	upgrades must not guess it or rewrite existing Customer assignments.
	"""
	if frappe.db.exists("DocType", "YRP Sales Person Customers"):
		create_custom_fields({"Sales Person": [
			{
				"fieldname": "yrp_sales_partner", "label": "Sales Partner", "fieldtype": "Link",
				"options": "Sales Partner", "module": "YRP Retail", "insert_after": "enabled",
				"mandatory_depends_on": "eval:!doc.is_group", "depends_on": "eval:!doc.is_group",
				"description": "Select the Sales Partner before assigning Customers.",
			},
			{
				"fieldname": "yrp_customers", "label": "Assigned Customers", "fieldtype": "Table",
				"options": "YRP Sales Person Customers", "module": "YRP Retail",
				"insert_after": "yrp_sales_partner", "depends_on": "eval:!doc.is_group",
				"read_only_depends_on": "eval:!doc.yrp_sales_partner",
				"description": "Only Customers belonging to the selected Sales Partner can be assigned.",
			},
		]})


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


def setup_sales_flow():
	"""Repeatable trace links and delivery counters on native ERPNext documents."""
	def field(name, kind, **kwargs):
		return dict(fieldname=name, fieldtype=kind, label=name.replace('_', ' ').title(), module='YRP Retail', **kwargs)
	create_custom_fields({
		'Sales Order': [field('yrp_retail_order','Link',options='YRP Retail Order'),field('yrp_retail_order_summary','Link',options='YRP Retail Order Summary')],
		'Sales Order Item': [field('yrp_retail_order_item','Link',options='YRP Retail Order Item',read_only=1),field('yrp_retail_summary_item','Link',options='YRP Retail Order Summary Item',read_only=1)],
		'Packing Slip': [field('yrp_delivered','Check',read_only=1,allow_on_submit=1),field('yrp_delivered_at','Datetime',read_only=1,allow_on_submit=1)],
		'Delivery Note': [field('yrp_delivered_qty','Float',read_only=1,allow_on_submit=1),field('yrp_per_delivered','Percent',read_only=1,allow_on_submit=1)],
		'Delivery Note Item': [field('yrp_delivered_qty','Float',read_only=1,allow_on_submit=1)],
	})
