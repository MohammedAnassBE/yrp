"""Capacity accounting between retail requests and native ERPNext Sales Orders.

Draft and submitted Sales Orders reserve capacity; cancellation/deletion release
it. Quantities remain in each source row's UOM. No prices, stock, GL entries or
native sales permissions are overridden here.
"""
from collections import defaultdict
import math

import frappe
from frappe import _
from frappe.utils import getdate, today

from yrp.yrp_retail.logic import finite_number, require
from yrp.yrp_retail.pricing import lock_pricing_policy

SOURCES = {
	"YRP Retail Order": {
		"header": "yrp_retail_order", "row": "yrp_retail_order_item",
		"child": "YRP Retail Order Item", "capacity": "qty",
	},
	"YRP Retail Order Summary": {
		"header": "yrp_retail_order_summary", "row": "yrp_retail_summary_item",
		"child": "YRP Retail Order Summary Item", "capacity": "company_qty",
	},
}


def _source_keys(doc):
	return [(doctype, doc.get(spec["header"])) for doctype, spec in SOURCES.items() if doc.get(spec["header"])]


def _lock_sources(keys):
	# Native pricing calculation also holds this mutex. Acquire it first to keep
	# mapping and ordinary Sales Order validation in the same lock order.
	lock_pricing_policy()
	for doctype, name in sorted(set(keys)):
		require(doctype in SOURCES, "Unsupported retail source.")
		frappe.db.sql(f"select name from `tab{doctype}` where name=%s for update", name)


def _source(doctype, name, check_permission=False):
	require(doctype in SOURCES, "Unsupported retail source.")
	_lock_sources([(doctype, name)])
	doc = frappe.get_doc(doctype, name, for_update=True)
	if check_permission:
		doc.check_permission("read")
	return doc


def _validate_eligible(source):
	if source.doctype == "YRP Retail Order":
		require(source.order_type == "Primary", "Only Primary Retail Orders can create Sales Orders directly.")
		require(not source.get("summary"), "A summarized Retail Order cannot create a Sales Order directly.")
	else:
		require(source.docstatus == 1, "A Retail Order Summary must be submitted before creating a Sales Order.")
		require(source.order_type == "Secondary", "Only Secondary Retail Order Summaries can create Sales Orders.")


def _allocations(source, exclude=None):
	"""Current locking read, including drafts; sum in Python to retain row locks."""
	spec = SOURCES[source.doctype]
	rows = frappe.db.sql(
		f"""select soi.`{spec['row']}` as source_row, soi.qty, so.name
		from `tabSales Order Item` soi join `tabSales Order` so on so.name=soi.parent
		where so.docstatus < 2 and so.`{spec['header']}`=%s
		and (%s is null or so.name != %s) order by so.name, soi.name for update""",
		(source.name, exclude, exclude), as_dict=True,
	)
	allocated = defaultdict(float)
	for row in rows:
		allocated[row.source_row] += finite_number(row.qty, "Allocated quantity")
	return allocated


def _factor(source, row):
	item = frappe.get_doc("Item", row.item_code, for_update=True)
	if row.uom == item.stock_uom:
		factor = 1
	else:
		matches = [entry for entry in item.uoms if entry.uom == row.uom]
		require(len(matches) == 1, "Source UOM must have one conversion on the Item.")
		factor = finite_number(matches[0].conversion_factor, "Source conversion factor")
	require(factor > 0, "Source conversion factor must be positive.")
	if source.doctype == "YRP Retail Order":
		stored = finite_number(row.conversion_factor, "Source conversion factor")
		require(math.isclose(stored, factor, rel_tol=1e-12, abs_tol=1e-9), "Retail source conversion factor differs from the current Item conversion.")
	return factor


def _check_quantity(qty, capacity, allocated):
	qty = finite_number(qty, "Sales Order quantity")
	capacity = finite_number(capacity, "Source quantity")
	allocated = finite_number(allocated, "Allocated quantity")
	require(qty > 0, "Retail-linked Sales Order quantities must be positive.")
	# Allow only float representation noise, never an extra business quantity.
	require(qty + allocated <= capacity or math.isclose(qty + allocated, capacity, rel_tol=1e-12, abs_tol=1e-9), "Sales Order quantity exceeds the remaining retail source quantity.")
	return qty


def validate_sales_order(doc, method=None):
	keys = _source_keys(doc)
	require(len(keys) <= 1, "A Sales Order can reference only one retail source.")
	old = doc.get_doc_before_save()
	if old and _source_keys(old):
		require(keys == _source_keys(old), "A linked Sales Order cannot change or remove its retail source.")
	all_keys = keys + (_source_keys(old) if old else [])
	if all_keys:
		_lock_sources(all_keys)
	if not keys:
		require(not any(row.get(spec["row"]) for row in doc.items for spec in SOURCES.values()), "Retail source row links require a retail source header.")
		return
	source = _source(*keys[0], check_permission=True)
	_validate_eligible(source)
	require(doc.customer == source.customer, "Sales Order Customer must match its retail source.")
	spec = SOURCES[source.doctype]
	other_field = next(value["row"] for key, value in SOURCES.items() if key != source.doctype)
	by_name = {row.name: row for row in source.items}
	allocated = _allocations(source, exclude=doc.name)
	requested = defaultdict(float)
	require(doc.items, "A retail-linked Sales Order requires items.")
	for row in doc.items:
		require(not row.get(other_field), "Sales Order row references the wrong retail source type.")
		reference = row.get(spec["row"])
		require(reference in by_name, "Every Sales Order row must reference a row belonging to its retail source.")
		original = by_name[reference]
		require(row.item_code == original.item_code and row.uom == original.uom, "Sales Order Item and UOM must match the source row.")
		factor = finite_number(row.conversion_factor, "Sales Order conversion factor")
		require(math.isclose(factor, _factor(source, original), rel_tol=1e-12, abs_tol=1e-9), "Sales Order conversion factor must match its source.")
		qty = finite_number(row.qty, "Sales Order quantity")
		require(qty > 0, "Retail-linked Sales Order quantities must be positive.")
		requested[reference] += qty
	for reference, qty in requested.items():
		_check_quantity(qty, by_name[reference].get(spec["capacity"]), allocated[reference])


def refresh_progress(doc, method=None):
	keys = _source_keys(doc)
	old = doc.get_doc_before_save()
	if old:
		keys += _source_keys(old)
	if not keys:
		return
	_lock_sources(keys)
	for doctype, name in sorted(set(keys)):
		source = _source(doctype, name)
		spec = SOURCES[doctype]
		allocated = _allocations(source, exclude=doc.name if method in ("on_trash", "after_delete") else None)
		total, capacity = 0.0, 0.0
		for row in source.items:
			qty = allocated[row.name]
			frappe.db.set_value(spec["child"], row.name, "ordered_qty", qty, update_modified=False)
			total += qty
			capacity += finite_number(row.get(spec["capacity"]), "Source quantity")
		frappe.db.set_value(doctype, name, {"ordered_qty": total, "per_ordered": total / capacity * 100 if capacity > 0 else 0}, update_modified=False)


def protect_retail_source(doc, method=None):
	if doc.doctype not in SOURCES:
		return
	spec = SOURCES[doc.doctype]
	allocated = defaultdict(float)
	if not doc.is_new():
		_lock_sources([(doc.doctype, doc.name)])
		allocated = _allocations(doc)
	# Read-only fields are a UI hint, so overwrite forged incoming values too.
	total, capacity = 0.0, 0.0
	for row in doc.items:
		row.ordered_qty = allocated[row.name]
		total += row.ordered_qty
		capacity += finite_number(row.get(spec["capacity"]), "Source quantity")
	doc.ordered_qty = total
	doc.per_ordered = total / capacity * 100 if capacity > 0 else 0
	if doc.is_new():
		return
	used = frappe.db.sql(f"select name from `tabSales Order` where `{spec['header']}`=%s and docstatus<2 limit 1 for update", doc.name)
	if not used:
		return
	if method in ("before_cancel", "on_cancel", "on_trash"):
		frappe.throw(_("Cancel or delete the linked Sales Orders before cancelling or deleting this retail source."))
	old = doc.get_doc_before_save()
	if not old:
		old = frappe.get_doc(doc.doctype, doc.name, for_update=True)
	for field in ("customer", "sales_person", "order_type", "visit", "summary", "order_date", "from_date", "to_date"):
		require(str(doc.get(field) or "") == str(old.get(field) or ""), "A retail source used by a Sales Order cannot change its header.")
	fields = ("name", "item_code", "uom", "qty", "conversion_factor", "stock_qty", "requested_qty", "customer_stock_qty", "company_qty")
	fingerprint = lambda document: sorted(tuple(str(row.get(field) or "") for field in fields) for row in document.items)
	require(fingerprint(doc) == fingerprint(old), "A retail source used by a Sales Order cannot change its items.")


@frappe.whitelist(methods=["POST"])
def make_sales_order(source_doctype, source_name, company, delivery_date, items=None, selling_price_list=None):
	"""Atomically create a draft; even caught direct-call failures roll back."""
	point = "retail_sales_order_" + frappe.generate_hash(length=12)
	frappe.db.savepoint(point)
	try:
		return _make_sales_order(source_doctype, source_name, company, delivery_date, items, selling_price_list)
	except Exception:
		frappe.db.rollback(save_point=point)
		raise


def _make_sales_order(source_doctype, source_name, company, delivery_date, items=None, selling_price_list=None):
	"""Insert one draft native Sales Order, optionally selecting source rows/qty.

	`items` is a JSON/list of {source_row: source-child-name, qty: positive-number}.
	Omitting it selects each row's remaining capacity. Native ERPNext determines
	prices, taxes, accounts, stock UOM and defaults, then validates the draft.
	"""
	frappe.has_permission("Sales Order", ptype="create", throw=True)
	require(company and delivery_date, "Company and Delivery Date are required.")
	require(getdate(delivery_date) >= getdate(today()), "Delivery Date cannot be in the past.")
	source = _source(source_doctype, source_name, check_permission=True)
	_validate_eligible(source)
	spec = SOURCES[source_doctype]
	allocated = _allocations(source)
	by_name = {row.name: row for row in source.items}
	if items is None:
		selected = [{"source_row": row.name, "qty": finite_number(row.get(spec["capacity"]), "Source quantity") - allocated[row.name]} for row in source.items]
		selected = [row for row in selected if row["qty"] > 0]
	else:
		selected = frappe.parse_json(items) if isinstance(items, str) else items
		require(isinstance(selected, list), "Selected items must be a list.")
	require(selected, "There is no remaining quantity to order.")
	seen = set()
	doc = frappe.new_doc("Sales Order")
	doc.update({"company": company, "customer": source.customer, "transaction_date": today(), "delivery_date": delivery_date, spec["header"]: source.name})
	# new_doc may inherit currency/rates from a different default Company.
	# Let native Customer/Company defaults resolve them for this explicit Company.
	for field in ("currency", "conversion_rate", "price_list_currency", "plc_conversion_rate"):
		doc.set(field, None)
	if selling_price_list:
		doc.selling_price_list = selling_price_list
	for selection in selected:
		require(isinstance(selection, dict) and set(selection) == {"source_row", "qty"}, "Selected items require only source_row and qty.")
		reference = selection["source_row"]
		require(reference in by_name and reference not in seen, "Invalid or duplicate source row selection.")
		seen.add(reference)
		row = by_name[reference]
		qty = _check_quantity(selection["qty"], row.get(spec["capacity"]), allocated[reference])
		doc.append("items", {"item_code": row.item_code, "uom": row.uom, "qty": qty, "delivery_date": delivery_date, "conversion_factor": _factor(source, row), spec["row"]: reference})
	doc.set_missing_values()
	doc.insert()
	return doc.as_dict()
