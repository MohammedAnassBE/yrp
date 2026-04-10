"""Stock utilities — dimension-aware wrappers around SLE/Bin queries.

All public functions accept ``**dimension_filters`` so callers can pass any
combination of configured stock dimensions (lot, received_type, batch, ...).
"""

import datetime

import frappe
from frappe.utils import flt, get_time, getdate, nowdate, nowtime

from yrp.stock.dimensions import get_dimension_fieldnames, get_stock_dimensions


# ----------------------------------------------------------------------
# Date / time helpers
# ----------------------------------------------------------------------
def get_combine_datetime(posting_date, posting_time):
	if isinstance(posting_date, str):
		posting_date = getdate(posting_date)
	if isinstance(posting_time, str):
		posting_time = get_time(posting_time)
	if isinstance(posting_time, datetime.timedelta):
		posting_time = (datetime.datetime.min + posting_time).time()
	return datetime.datetime.combine(posting_date, posting_time).replace(microsecond=0)


# ----------------------------------------------------------------------
# Item / UOM
# ----------------------------------------------------------------------
def get_conversion_factor(item_variant, uom):
	variant_of = frappe.db.get_value("Item Variant", item_variant, "item", cache=True)
	conv = frappe.db.get_value("UOM Conversion Detail", {"parent": variant_of, "uom": uom}, "conversion_factor")
	return {
		"conversion_factor": conv or 1.0,
		"stock_uom": frappe.db.get_value("Item", variant_of, "default_unit_of_measure", cache=True),
	}


# ----------------------------------------------------------------------
# Bin
# ----------------------------------------------------------------------
def get_or_make_bin(item_code, warehouse, **dimension_filters):
	"""Return Bin name for (item, warehouse, *dimensions); create if missing."""
	filters = {"item_code": item_code, "warehouse": warehouse}
	for fn in get_dimension_fieldnames():
		filters[fn] = dimension_filters.get(fn)
	bin_name = frappe.db.get_value("Bin", filters, "name")
	if not bin_name:
		bin_doc = frappe.new_doc("Bin")
		bin_doc.item_code = item_code
		bin_doc.warehouse = warehouse
		for fn, val in filters.items():
			if fn not in ("item_code", "warehouse"):
				bin_doc.set(fn, val)
		bin_doc.insert(ignore_permissions=True)
		bin_name = bin_doc.name
	return bin_name


# ----------------------------------------------------------------------
# Stock balance
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_stock_balance(
	item,
	warehouse,
	posting_date=None,
	posting_time=None,
	with_valuation_rate=False,
	uom=None,
	**dimension_filters,
):
	"""Stock balance for (item, warehouse) at a point in time, with dimensions."""
	from yrp.stock.stock_ledger import get_previous_sle

	if posting_date is None:
		posting_date = nowdate()
	if posting_time is None:
		posting_time = nowtime()

	args = frappe._dict({
		"item": item,
		"warehouse": warehouse,
		"posting_date": posting_date,
		"posting_time": posting_time,
		"posting_datetime": get_combine_datetime(posting_date, posting_time),
	})
	for fn in get_dimension_fieldnames():
		args[fn] = dimension_filters.get(fn)

	last = get_previous_sle(args)
	qty = flt(last.qty_after_transaction) if last else 0.0
	rate = flt(last.valuation_rate) if last else 0.0

	if uom:
		cd = get_conversion_factor(item, uom)
		cf = flt(cd["conversion_factor"]) or 1.0
		qty = qty / cf
		rate = rate * cf

	return (qty, rate) if with_valuation_rate else qty


# ----------------------------------------------------------------------
# Reservation
# ----------------------------------------------------------------------
def get_sre_reserved_qty(filters):
	"""Sum of reserved_qty - delivered_qty across submitted SRE rows matching filters."""
	conds = {"docstatus": 1, "status": ["not in", ["Delivered", "Cancelled"]]}
	conds.update({k: v for k, v in filters.items() if v is not None})
	rows = frappe.get_all(
		"Stock Reservation Entry",
		filters=conds,
		fields=["sum(reserved_qty - delivered_qty) as qty"],
	)
	return flt(rows[0].qty) if rows else 0.0


def future_sle_exists(args):
	"""Quick check: does any non-cancelled SLE exist after the given posting_datetime
	for matching item/warehouse/dimensions, excluding the current voucher."""
	conds = {
		"item": args.get("item"),
		"warehouse": args.get("warehouse"),
		"is_cancelled": 0,
		"posting_datetime": [">=", args.get("posting_datetime")],
		"voucher_no": ["!=", args.get("voucher_no") or ""],
	}
	for fn in get_dimension_fieldnames():
		if args.get(fn):
			conds[fn] = args.get(fn)
	return bool(frappe.db.exists("Stock Ledger Entry", conds))


# ----------------------------------------------------------------------
# Cancellation rate lookup
# ----------------------------------------------------------------------
def get_incoming_outgoing_rate_for_cancel(item, voucher_type, voucher_no, voucher_detail_no):
	rows = frappe.db.sql(
		"""SELECT CASE WHEN qty = 0 THEN 0 ELSE abs(stock_value_difference / qty) END
		FROM `tabStock Ledger Entry`
		WHERE voucher_type=%s AND voucher_no=%s AND item=%s AND voucher_detail_no=%s
		ORDER BY creation DESC LIMIT 1""",
		(voucher_type, voucher_no, item, voucher_detail_no),
	)
	return rows[0][0] if rows else 0.0
