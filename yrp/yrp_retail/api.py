"""Narrow mobile APIs for retailer creation, visits, orders and demand summaries.

Every mutation derives its actor from the authenticated user's Partner membership,
accepts only business fields, and uses normal document validation in one transaction.
"""
import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import getdate

from yrp.yrp_retail.access import allow_write, require_customer, require_owned, salesperson


@contextmanager
def atomic():
	point = "retail_api_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(point)
	try:
		yield
	except Exception:
		frappe.db.rollback(save_point=point)
		raise


def _items(value):
	rows = frappe.parse_json(value) if isinstance(value, str) else value
	if not isinstance(rows, list) or not rows:
		frappe.throw(_("Provide at least one item."))
	for row in rows:
		if not isinstance(row, dict) or set(row) != {"item_code", "qty", "uom"}:
			frappe.throw(_("Each item must contain only item_code, qty and uom."))
	return rows


def _save(doc, actor):
	with atomic(), allow_write(doc, actor):
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
	return {"doctype": doc.doctype, "name": doc.name}


@frappe.whitelist(methods=["POST"])
def create_retailer(retailer_name, customer, sales_person=None, shop_name=None, primary_address=None):
	"""Create a retailer belonging to the authenticated actor and an assigned customer."""
	actor = salesperson(sales_person)
	require_customer(actor, customer)
	if not isinstance(retailer_name, str) or not retailer_name.strip():
		frappe.throw(_("Retailer name is required."))
	doc = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": retailer_name.strip(),
		"shop_name": shop_name, "primary_address": primary_address,
		"customer": customer, "sales_person": actor.name})
	return _save(doc, actor)


@frappe.whitelist(methods=["POST"])
def create_visit(visit_type, visit_datetime, latitude, longitude, customer=None, retailer=None, notes=None, sales_person=None):
	"""Record a point location; the controller enforces Primary/Secondary targets."""
	actor = salesperson(sales_person)
	try:
		coordinates = [float(longitude), float(latitude)]
	except (TypeError, ValueError):
		frappe.throw(_("Latitude and longitude must be numbers."))
	doc = frappe.get_doc({"doctype": "YRP Visit", "sales_person": actor.name,
		"visit_type": visit_type, "visit_datetime": visit_datetime, "customer": customer,
		"retailer": retailer, "notes": notes,
		"location": json.dumps({"type": "Point", "coordinates": coordinates})})
	return _save(doc, actor)


@frappe.whitelist(methods=["POST"])
def create_retail_order(visit, items, notes=None, sales_person=None):
	"""Create one draft order per visit; no Sales Order, stock or accounting posting."""
	actor = salesperson(sales_person)
	visit_doc = frappe.get_doc("YRP Visit", visit)
	require_owned(actor, visit_doc)
	doc = frappe.get_doc({"doctype": "YRP Retail Order", "visit": visit,
		"sales_person": actor.name, "customer": visit_doc.customer,
		"retailer": visit_doc.retailer, "order_type": visit_doc.visit_type,
		"order_date": getdate(visit_doc.visit_datetime), "items": _items(items), "notes": notes})
	return _save(doc, actor)


@frappe.whitelist(methods=["POST"])
def update_retail_order(name, items, notes=None, sales_person=None):
	"""Edit only the actor's unsummarized order items and notes."""
	actor = salesperson(sales_person)
	doc = frappe.get_doc("YRP Retail Order", name)
	require_owned(actor, doc)
	doc.set("items", _items(items))
	doc.notes = notes
	return _save(doc, actor)


@frappe.whitelist(methods=["POST"])
def create_summary(orders, customer_stock=None, sales_person=None):
	"""Group complete source orders; the controller derives demand and validates stock allocation."""
	actor = salesperson(sales_person)
	names = frappe.parse_json(orders) if isinstance(orders, str) else orders
	if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
		frappe.throw(_("Provide a list of retail order names."))
	if len(names) != len(set(names)):
		frappe.throw(_("An order cannot appear twice."))
	docs = [frappe.get_doc("YRP Retail Order", name) for name in names]
	for doc in docs:
		require_owned(actor, doc)
	allocations = frappe.parse_json(customer_stock) if isinstance(customer_stock, str) else customer_stock
	allocations = allocations or []
	if not isinstance(allocations, list) or any(not isinstance(row, dict) or set(row) != {"item_code", "uom", "customer_stock_qty"} for row in allocations):
		frappe.throw(_("Stock allocations must contain only item_code, uom and customer_stock_qty."))
	doc = frappe.get_doc({"doctype": "YRP Retail Order Summary", "sales_person": actor.name,
		"customer": docs[0].customer, "order_type": docs[0].order_type,
		"from_date": min(getdate(row.order_date) for row in docs),
		"to_date": max(getdate(row.order_date) for row in docs),
		"source_orders": [{"order": name} for name in names], "items": allocations})
	return _save(doc, actor)


@frappe.whitelist()
def list_records(doctype, sales_person=None, start=0, page_length=20):
	"""List assigned Customers' shops or the actor's transactions, with read permissions."""
	actor = salesperson(sales_person)
	if doctype not in {"YRP Retailer", "YRP Visit", "YRP Retail Order", "YRP Retail Order Summary"}:
		frappe.throw(_("Unsupported retail document type."), frappe.PermissionError)
	try:
		start, page_length = int(start), int(page_length)
	except (TypeError, ValueError):
		frappe.throw(_("Pagination must use integers."))
	if start < 0 or not 1 <= page_length <= 100:
		frappe.throw(_("Page length must be between 1 and 100."))
	filters = {"customer": ["in", [row.customer for row in actor.get("yrp_customers", [])]]} if doctype == "YRP Retailer" else {"sales_person": actor.name}
	return frappe.get_list(doctype, filters=filters, fields=["name", "modified", "docstatus"],
		start=start, page_length=page_length, order_by="modified desc, name desc")


@frappe.whitelist(methods=["POST"])
def update_summary(name, customer_stock, sales_person=None):
	"""Adjust customer supply on an actor-owned draft; source demand stays immutable."""
	actor = salesperson(sales_person)
	doc = frappe.get_doc("YRP Retail Order Summary", name)
	require_owned(actor, doc)
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft summary can be changed."))
	rows = frappe.parse_json(customer_stock) if isinstance(customer_stock, str) else customer_stock
	if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != {"item_code", "uom", "customer_stock_qty"} for row in rows):
		frappe.throw(_("Stock allocations must contain only item_code, uom and customer_stock_qty."))
	doc.set("items", rows)
	return _save(doc, actor)


@frappe.whitelist(methods=["POST"])
def submit_summary(name, sales_person=None):
	"""Finalize an actor-owned summary without creating a Sales Order or posting stock."""
	actor = salesperson(sales_person)
	doc = frappe.get_doc("YRP Retail Order Summary", name)
	require_owned(actor, doc)
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft summary can be submitted."))
	with atomic(), allow_write(doc, actor, operation="submit"):
		doc.flags.ignore_permissions = True
		doc.submit()
	return {"doctype": doc.doctype, "name": doc.name, "docstatus": doc.docstatus}


@frappe.whitelist(methods=["POST"])
def update_customer_stock(customer, item_code, qty, sales_person=None):
	"""Replace an assigned Customer's item count in the configured retail UOM."""
	from yrp.yrp_retail.customer_stock import report
	actor = salesperson(sales_person)
	require_customer(actor, customer)
	with atomic():
		return report(customer, item_code, qty)


@frappe.whitelist()
def get_customer_stock(customer, item_code, sales_person=None):
	"""Return the latest remaining balance, distinguishing unreported from zero."""
	from yrp.yrp_retail.customer_stock import configured_uom, payload, validate_item
	actor = salesperson(sales_person)
	require_customer(actor, customer)
	uom = configured_uom()
	validate_item(item_code)
	name = frappe.db.get_value('YRP Customer Stock', {'customer':customer, 'item_code':item_code}, 'name')
	if name:
		return payload(frappe.get_doc('YRP Customer Stock', name))
	return {'customer':customer, 'item_code':item_code, 'uom':uom, 'qty':0,
		'recorded':False, 'last_counted_at':None, 'last_counted_by':None}
