"""Carton delivery progress on native Packing Slips and Delivery Notes.

Native Packing Slip qty uses its Delivery Note row's sales UOM (despite the
field being named stock_uom). A case range is delivered as one group; create one
slip per case for independent carton tracking. No stock/GL/native delivery status
is changed here. Delivery requires submitted DN and slips; delivered slips must
be unmarked before cancellation or deletion.
"""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe.utils import flt, now_datetime

from yrp.yrp_retail.logic import finite_number, require, validate_uom_quantity

_delivery_write = ContextVar("yrp_carton_delivery_write", default=None)


def lock_packing(doc=None, method=None):
	from yrp.yrp_retail.pricing import lock_pricing_policy
	lock_pricing_policy()
	frappe.db.sql("select name from `tabDocType` where name='Packing Slip' for update")


class PackingTrackingMixin:
	"""Take the carton mutex before Frappe locks an existing DN or Packing Slip."""
	def _save(self, *args, **kwargs):
		lock_packing()
		return super()._save(*args, **kwargs)


@contextmanager
def atomic():
	point = "carton_delivery_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(point)
	try:
		yield
	except Exception:
		frappe.db.rollback(save_point=point)
		raise


def delivery_note(name, *, write=False):
	doc = frappe.get_doc("Delivery Note", name, for_update=True)
	doc.check_permission("write" if write else "read")
	require(doc.docstatus != 2 and not doc.is_return, "Cartons require a non-cancelled, outward Delivery Note.")
	return doc


def slips_for(name, exclude=None):
	names = frappe.db.get_values("Packing Slip", {"delivery_note": name, "docstatus": ["!=", 2]},
		"name", pluck=True, for_update=True, order_by="name")
	return [frappe.get_doc("Packing Slip", value, for_update=True) for value in names if value != exclude]


def case_range(doc):
	first = finite_number(doc.from_case_no, "From Case No")
	last = finite_number(doc.to_case_no or first, "To Case No")
	require(first.is_integer() and last.is_integer() and 1 <= first <= last,
		"Carton numbers must be positive integers with To Case No at least From Case No.")
	return int(first), int(last)


def mapped_quantities(slip, dn, *, allow_bundles=False):
	"""Validate exact native child identity, not an ambiguous item-code lookup."""
	sources = {row.name: row for row in dn.items}
	quantities = defaultdict(float)
	require(slip.items, "A carton must contain at least one item.")
	for row in slip.items:
		if row.pi_detail:
			require(allow_bundles, "YRP carton delivery tracking does not support Product Bundle Packed Item rows.")
			require(not row.dn_detail, "A carton row cannot reference both a Delivery Note Item and Packed Item.")
			packed = next((value for value in dn.packed_items if value.name == row.pi_detail), None)
			require(packed and row.item_code == packed.item_code and row.stock_uom == packed.uom,
				"Packed Item must belong to this Delivery Note and match its Item and UOM.")
			qty = validate_uom_quantity(row.qty, row.stock_uom, "Carton quantity", row.precision("qty"))
			require(qty > 0, "Carton quantities must be greater than zero.")
			quantities["pi:" + row.pi_detail] += qty
			continue
		source = sources.get(row.dn_detail)
		require(source, "Every carton item must reference a row of this Delivery Note.")
		require(row.item_code == source.item_code, "Carton Item does not match its Delivery Note row.")
		require(row.stock_uom == source.uom, "Carton UOM must equal the Delivery Note row UOM; quantities are not converted.")
		qty = validate_uom_quantity(row.qty, row.stock_uom, "Carton quantity", row.precision("qty"))
		require(qty > 0, "Carton quantities must be greater than zero.")
		require(finite_number(source.qty, "Delivery Note quantity") > 0,
			"Cartons cannot reference zero or negative Delivery Note quantities.")
		factor = finite_number(source.conversion_factor, "Delivery Note conversion factor")
		require(factor > 0, "Delivery Note conversion factor must be greater than zero.")
		validate_uom_quantity(qty * factor, source.stock_uom,
			"Carton stock quantity", source.precision("stock_qty"))
		quantities[row.dn_detail] += qty
	return quantities


def validate_coverage(dn, slips):
	quantities = defaultdict(float)
	ranges = []
	for slip in slips:
		first, last = case_range(slip)
		for start, end in ranges:
			require(last < start or first > end, "Carton number ranges cannot overlap, including draft Packing Slips.")
		ranges.append((first, last))
		for source, qty in mapped_quantities(slip, dn, allow_bundles=True).items():
			quantities[source] += qty
	for row in dn.items:
		require(quantities[row.name] <= finite_number(row.qty, "Delivery Note quantity") + 1e-8,
			"Packed quantity, including draft cartons, cannot exceed its Delivery Note row quantity.")
	for row in dn.packed_items:
		require(quantities["pi:" + row.name] <= finite_number(row.qty, "Packed Item quantity") + 1e-8,
			"Packed quantity, including draft cartons, cannot exceed its Packed Item row quantity.")
	return quantities


def prepare_packing_slip(doc, method=None):
	lock_packing()
	dn = delivery_note(doc.delivery_note, write=True)
	old = doc.get_doc_before_save()
	if old:
		require(doc.delivery_note == old.delivery_note, "A Packing Slip cannot change its Delivery Note.")
	guard_packing_status(doc)
	# Native mapper supplies dn_detail; no guessing when identical Items repeat.
	sources = {row.name: row for row in dn.items}
	for row in doc.items:
		if row.dn_detail in sources and not row.stock_uom:
			row.stock_uom = sources[row.dn_detail].uom


def validate_packing_slip(doc, method=None):
	lock_packing()
	dn = delivery_note(doc.delivery_note, write=True)
	validate_coverage(dn, slips_for(dn.name, exclude=doc.name) + [doc])


def guard_packing_status(doc, method=None):
	old = doc.get_doc_before_save()
	old_time = old.get("yrp_delivered_at") if old else None
	changed = (bool(doc.get("yrp_delivered")) != bool(old.get("yrp_delivered") if old else False)
		or str(doc.get("yrp_delivered_at") or "") != str(old_time or ""))
	if changed and _delivery_write.get() is not doc:
		frappe.throw("Change carton delivery status through the delivery API.", frappe.PermissionError)


def validate_delivery_note(doc, method=None):
	"""Preserve carton row identity while allowing native sales/stock validation."""
	lock_packing()
	slips = slips_for(doc.name) if not doc.is_new() else []
	if slips:
		require(not doc.is_return, "A Delivery Note with cartons cannot become a return.")
		validate_coverage(doc, slips)
		old = doc.get_doc_before_save()
		if old:
			old_rows = {row.name: row for row in old.items}
			packed = {row.dn_detail for slip in slips for row in slip.items}
			for row in doc.items:
				if row.name in packed and row.name in old_rows:
					for field in ("item_code", "uom", "stock_uom", "conversion_factor", "against_sales_order", "so_detail"):
						require((row.get(field) or "") == (old_rows[row.name].get(field) or ""),
							"A packed Delivery Note row cannot change its Item, units or Sales Order reference.")
	set_progress(doc, slips)


def set_progress(dn, slips):
	"""Keep row quantities in sales UOM and weight progress by recorded stock qty.

	Like ERPNext picking progress, the document percentage aggregates each row's
	stock quantities. Different Items may have different stock UOMs: this is a
	document progress measure, not a common physical-unit measurement. Use the
	Delivery Note's conversion snapshot, never today's Item conversion settings.
	"""
	quantities = defaultdict(float)
	for slip in slips:
		if slip.docstatus == 1 and slip.get("yrp_delivered"):
			for name, qty in mapped_quantities(slip, dn).items():
				quantities[name] += qty
	delivered_stock_qty = total_stock_qty = 0.0
	for row in dn.items:
		row.yrp_delivered_qty = quantities[row.name]
		factor = finite_number(row.conversion_factor, "Delivery Note conversion factor")
		require(factor > 0, "Delivery Note conversion factor must be greater than zero.")
		delivered_stock_qty += flt(
			finite_number(row.yrp_delivered_qty * factor, "Delivered stock quantity"),
			row.precision("stock_qty"),
		)
		total_stock_qty += max(0, finite_number(row.stock_qty, "Delivery Note stock quantity"))
	dn.yrp_delivered_qty = delivered_stock_qty
	dn.yrp_per_delivered = 100 * delivered_stock_qty / total_stock_qty if total_stock_qty else 0
	return {"delivery_note": dn.name, "delivered_qty": dn.yrp_delivered_qty,
		"per_delivered": dn.yrp_per_delivered,
		"total_cartons": sum(case_range(slip)[1] - case_range(slip)[0] + 1 for slip in slips),
		"delivered_cartons": sum(case_range(slip)[1] - case_range(slip)[0] + 1 for slip in slips
			if slip.docstatus == 1 and slip.get("yrp_delivered"))}


def refresh_progress(doc, method=None):
	"""Internal hook/service; computed progress never changes native ledger status."""
	lock_packing()
	name = doc if isinstance(doc, str) else doc.delivery_note if doc.doctype == "Packing Slip" else doc.name
	dn = frappe.get_doc("Delivery Note", name, for_update=True)
	result = set_progress(dn, slips_for(name))
	for row in dn.items:
		frappe.db.set_value("Delivery Note Item", row.name, "yrp_delivered_qty", row.yrp_delivered_qty, update_modified=False)
	frappe.db.set_value("Delivery Note", name, {"yrp_delivered_qty": dn.yrp_delivered_qty,
		"yrp_per_delivered": dn.yrp_per_delivered})
	return result


def prevent_delivered_cancellation(doc, method=None):
	lock_packing()
	if doc.doctype == "Packing Slip":
		current = frappe.get_doc("Packing Slip", doc.name, for_update=True)
		require(not current.get("yrp_delivered"), "Unmark carton delivery before cancelling or deleting this Packing Slip.")
	else:
		require(not any(slip.get("yrp_delivered") for slip in slips_for(doc.name)),
			"Unmark carton deliveries before cancelling or deleting this Delivery Note.")


def _set_delivered(doc, delivered):
	if bool(doc.get("yrp_delivered")) == delivered:
		return
	doc.yrp_delivered = int(delivered)
	doc.yrp_delivered_at = now_datetime() if delivered else None
	token = _delivery_write.set(doc)
	try:
		doc.save()
	finally:
		_delivery_write.reset(token)


@frappe.whitelist(methods=["POST"])
def mark_packing_slip_delivered(name, delivered=True):
	"""Deliver/unmark a submitted carton or complete case range, idempotently."""
	if delivered not in (True, False, 1, 0, "1", "0", "true", "false"):
		frappe.throw("Delivered must be true or false.")
	delivered = delivered in (True, 1, "1", "true")
	with atomic():
		lock_packing()
		doc = frappe.get_doc("Packing Slip", name, for_update=True)
		doc.check_permission("write")
		dn = delivery_note(doc.delivery_note, write=True)
		require(doc.docstatus == 1 and dn.docstatus == 1,
			"Submit both the Packing Slip and Delivery Note before recording delivery.")
		validate_coverage(dn, slips_for(dn.name))
		mapped_quantities(doc, dn)
		_set_delivered(doc, delivered)
		return refresh_progress(dn.name)


@frappe.whitelist(methods=["POST"])
def mark_delivery_note_delivered(name):
	"""Deliver every submitted carton only when they fully cover each DN row."""
	with atomic():
		lock_packing()
		dn = delivery_note(name, write=True)
		require(dn.docstatus == 1, "Submit the Delivery Note before recording delivery.")
		slips = slips_for(name)
		require(slips and all(slip.docstatus == 1 for slip in slips), "Submit every Packing Slip before marking the Delivery Note delivered.")
		quantities = validate_coverage(dn, slips)
		for slip in slips:
			mapped_quantities(slip, dn)
		for row in dn.items:
			require(abs(quantities[row.name] - row.qty) < 1e-8,
				"Submitted cartons must fully cover every Delivery Note row before marking it delivered.")
		for slip in slips:
			slip.check_permission("write")
		for slip in slips:
			_set_delivered(slip, True)
		return refresh_progress(name)


@frappe.whitelist(methods=["POST"])
def create_carton(delivery_note_name, case_no, items):
	"""Create one draft carton from explicit DN row names and quantities."""
	rows = frappe.parse_json(items) if isinstance(items, str) else items
	require(isinstance(rows, list) and rows, "Provide carton items.")
	with atomic():
		lock_packing()
		dn = delivery_note(delivery_note_name, write=True)
		require(dn.docstatus == 0, "Native Packing Slips must be created while the Delivery Note is draft.")
		sources = {row.name: row for row in dn.items}
		doc = frappe.get_doc({"doctype": "Packing Slip", "delivery_note": dn.name,
			"from_case_no": case_no, "to_case_no": case_no, "items": []})
		for row in rows:
			require(isinstance(row, dict) and set(row) == {"dn_detail", "qty"}, "Each carton item requires only dn_detail and qty.")
			source = sources.get(row["dn_detail"])
			require(source, "Carton item must reference this Delivery Note.")
			require(not frappe.db.exists("Product Bundle", {"new_item_code": source.item_code, "disabled": 0}),
				"Use native Packing Slips for Product Bundles; YRP carton delivery tracking does not support them.")
			doc.append("items", {"dn_detail": source.name, "item_code": source.item_code,
				"item_name": source.item_name, "description": source.description,
				"stock_uom": source.uom, "qty": row["qty"]})
		doc.insert()
		return {"name": doc.name, "delivery_note": dn.name, "case_no": doc.from_case_no}
