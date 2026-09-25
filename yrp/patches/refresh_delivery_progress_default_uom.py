"""Recompute existing physical-delivery counters in recorded Item default units.

Only derived YRP fields change. No document lifecycle, stock, accounting or
modified timestamps are touched, so an open user form remains valid.
"""

import frappe
from frappe.database.schema import DEFAULT_DECIMAL_PRECISION
from frappe.utils import cint, flt

from yrp.yrp_retail.packing import lock_packing, set_progress, slips_for


def execute():
	fields = {
		"Delivery Note": ("yrp_delivered_qty", "yrp_per_delivered"),
		"Delivery Note Item": ("yrp_delivered_qty",),
		"Packing Slip": ("yrp_delivered",),
	}
	for doctype, names in fields.items():
		if not frappe.db.exists("DocType", doctype):
			return
		meta = frappe.get_meta(doctype)
		if any(not meta.has_field(name) or not frappe.db.has_column(doctype, name) for name in names):
			return

	lock_packing()
	for name in _candidate_names():
		dn = frappe.get_doc("Delivery Note", name, for_update=True)
		if dn.docstatus == 2:
			continue
		previous = {field: dn.get(field) for field in fields["Delivery Note"]}
		previous_rows = {row.name: row.get("yrp_delivered_qty") for row in dn.items}
		set_progress(dn, slips_for(name))
		for row in dn.items:
			_update_changed(row, {"yrp_delivered_qty": previous_rows[row.name]})
		_update_changed(dn, previous)


def _candidate_names():
	"""Keyset pagination stays stable as repaired zero counters leave the query."""
	after = ""
	while True:
		rows = frappe.db.sql("""
			SELECT dn.name FROM `tabDelivery Note` dn
			WHERE dn.docstatus < 2 AND dn.name > %s AND (
				COALESCE(dn.yrp_delivered_qty, 0) != 0
				OR COALESCE(dn.yrp_per_delivered, 0) != 0
				OR EXISTS (
					SELECT 1 FROM `tabPacking Slip` ps
					WHERE ps.delivery_note = dn.name AND ps.docstatus < 2
				)
				OR EXISTS (
					SELECT 1 FROM `tabDelivery Note Item` item
					WHERE item.parent = dn.name AND item.parenttype = 'Delivery Note'
					AND item.parentfield = 'items' AND COALESCE(item.yrp_delivered_qty, 0) != 0
				)
			)
			ORDER BY dn.name LIMIT 200
		""", (after,), pluck=True)
		if not rows:
			return
		yield from rows
		after = rows[-1]


def _update_changed(doc, previous):
	# Compare at database precision, not display precision: small genuine
	# corrections must be saved, while repeating percentages remain idempotent.
	changed = {}
	for field, value in previous.items():
		precision = doc.meta.get_field(field).precision
		precision = cint(precision) if precision not in (None, "") else DEFAULT_DECIMAL_PRECISION
		if flt(value, precision) != flt(doc.get(field), precision):
			changed[field] = doc.get(field)
	if changed:
		frappe.db.set_value(doc.doctype, doc.name, changed, update_modified=False)
