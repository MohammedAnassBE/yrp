"""Stock utilities — dimension-aware wrappers around SLE/Bin queries.

All public functions accept ``**dimension_filters`` so callers can pass any
combination of configured stock dimensions (lot, received_type, batch, ...).
"""

import datetime

import frappe
from frappe import _
from frappe.utils import flt, get_time, getdate, now_datetime, nowdate, nowtime

from yrp.stock.dimensions import (
	assert_safe_fieldname,
	get_dimension_fieldnames,
	get_stock_dimensions,
	get_valuation_dimensions,
)


# ----------------------------------------------------------------------
# Date / time helpers
# ----------------------------------------------------------------------
def get_combine_datetime(posting_date, posting_time):
	"""Combine date and time into a datetime object.

	Handles multiple input types because Frappe returns dates/times
	in different formats depending on context (form, query, API).
	"""
	# Normalize posting_date to a date object
	if isinstance(posting_date, str):
		posting_date = getdate(posting_date)

	# Normalize posting_time to a time object
	if isinstance(posting_time, str):
		posting_time = get_time(posting_time)
	elif isinstance(posting_time, datetime.timedelta):
		# MariaDB TIME columns sometimes return timedelta instead of time
		posting_time = (datetime.datetime.min + posting_time).time()

	return datetime.datetime.combine(posting_date, posting_time).replace(microsecond=0)


# ----------------------------------------------------------------------
# Item / UOM
# ----------------------------------------------------------------------
def get_conversion_factor(item_variant, uom):
	variant_of = frappe.db.get_value("Item Variant", item_variant, "item", cache=True)
	if not variant_of:
		frappe.throw(_("Item Variant {0} not found").format(item_variant))
	conv = frappe.db.get_value("UOM Conversion Detail", {"parent": variant_of, "uom": uom}, "conversion_factor")
	return {
		"conversion_factor": conv or 1.0,
		"stock_uom": frappe.db.get_value("Item", variant_of, "default_unit_of_measure", cache=True),
	}


# ----------------------------------------------------------------------
# Bin
# ----------------------------------------------------------------------
def get_last_sle_rate(item, warehouse=None, **dimension_filters):
	"""Return the valuation rate of the last uncancelled SLE for this bucket.

	Filters by item + (optional) warehouse + every `in_valuation=1` dimension
	value present in dimension_filters (Gap #17). If no SLE matches the
	bucket, falls back to the last SLE for the item across any warehouse;
	the caller can detect the fallback because we also return the bucket
	flag.

	Returns: (rate: float, matched_bucket: bool)
	"""
	val_dim_fields = get_valuation_dimensions()

	def _query(use_bucket):
		conds = ["is_cancelled = 0", "item = %s"]
		values = [item]
		if use_bucket and warehouse:
			conds.append("warehouse = %s")
			values.append(warehouse)
		if use_bucket:
			for fn in val_dim_fields:
				val = dimension_filters.get(fn)
				if val is None:
					continue
				assert_safe_fieldname(fn)
				conds.append(f"`{fn}` = %s")
				values.append(val)
		row = frappe.db.sql(
			f"""
			SELECT valuation_rate FROM `tabStock Ledger Entry`
			WHERE {' AND '.join(conds)}
			ORDER BY posting_datetime DESC, creation DESC
			LIMIT 1
			""",
			tuple(values),
		)
		return flt(row[0][0]) if row else None

	rate = _query(use_bucket=True)
	if rate is not None:
		return rate, True
	rate = _query(use_bucket=False)
	return (rate or 0.0), False


def get_or_make_bin(item_code, warehouse, **dimension_filters):
	"""Return Bin name for (item, warehouse, *dimensions); create if missing.

	Handles concurrent requests safely: if two requests try to create the
	same Bin simultaneously, the second insert will hit the unique constraint
	and we fall back to re-querying.

	Mandatory dimensions are validated up-front: if any dimension flagged
	mandatory in YRP Stock Settings is missing/blank in dimension_filters,
	the call throws. This prevents Bins from being silently created with
	NULL dimension values and becoming orphans (Gap #19).
	"""
	if not item_code:
		frappe.throw(_("Cannot create Bin without item_code"))
	if not warehouse:
		frappe.throw(_("Cannot create Bin without warehouse"))

	dims = get_stock_dimensions()
	for dim in dims:
		if not dim.get("mandatory"):
			continue
		fn = dim["fieldname"]
		if not dimension_filters.get(fn):
			frappe.throw(
				_("Cannot create Bin — dimension '{0}' is mandatory but was not provided.").format(
					dim.get("label") or fn
				)
			)

	filters = {"item_code": item_code, "warehouse": warehouse}
	for fn in get_dimension_fieldnames():
		filters[fn] = dimension_filters.get(fn)

	bin_name = frappe.db.get_value("Bin", filters, "name")
	if bin_name:
		return bin_name

	# Bug F (r-009 D-005 #1): under MariaDB REPEATABLE READ, a concurrent
	# inserter may commit the Bin between our get_value and insert. The
	# insert hits the unique constraint; the retry get_value would normally
	# return None because our snapshot still pre-dates the winner's commit.
	# Loop with savepoints so each attempt opens a fresh visibility window.
	import time

	for attempt in range(5):
		bin_doc = frappe.new_doc("Bin")
		bin_doc.item_code = item_code
		bin_doc.warehouse = warehouse
		for fn, val in filters.items():
			if fn not in ("item_code", "warehouse"):
				bin_doc.set(fn, val)
		savepoint = f"get_or_make_bin_{attempt}"
		try:
			frappe.db.savepoint(savepoint)
			bin_doc.insert(ignore_permissions=True)
			return bin_doc.name
		except frappe.DuplicateEntryError:
			frappe.db.rollback(save_point=savepoint)
			# Snapshot may now see the winner's row; if not, sleep + retry.
			existing = frappe.db.get_value("Bin", filters, "name")
			if existing:
				return existing
			time.sleep(0.05 * (attempt + 1))
	# Final fallback — by this point the row should be visible.
	existing = frappe.db.get_value("Bin", filters, "name")
	if existing:
		return existing
	frappe.throw(
		_("Could not get or create Bin for {0} at {1} — concurrency retry exhausted.").format(
			item_code, warehouse
		)
	)


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
	with_stale=False,
	uom=None,
	**dimension_filters,
):
	"""Stock balance for (item, warehouse) at a point in time, with dimensions.

	with_stale=True returns a dict: {actual_qty, valuation_rate, stale,
	stale_reason}. Useful for forms/reports that should warn the user when
	a Repost Item Valuation is in progress for the bucket (Gap #22).
	"""
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

	if with_stale:
		stale = has_pending_repost(item, warehouse)
		return {
			"actual_qty": qty,
			"valuation_rate": rate,
			"stale": stale,
			"stale_reason": "Repost Item Valuation in progress" if stale else None,
		}

	return (qty, rate) if with_valuation_rate else qty


# ----------------------------------------------------------------------
# Reservation
# ----------------------------------------------------------------------
def get_available_stock(
	item_code,
	warehouse,
	exclude_voucher_type=None,
	exclude_voucher_name=None,
	**dimension_filters,
):
	"""Return actual_qty - reserved_qty (exclude-self), the qty truly available
	for this voucher to consume. Reservation is always honored; the
	per-Item allow_negative_stock flag does NOT bypass reservation (Gap #1).
	"""
	actual = get_stock_balance(item_code, warehouse, **dimension_filters)
	reserved = get_sre_reserved_qty(
		item_code=item_code,
		warehouse=warehouse,
		exclude_voucher_type=exclude_voucher_type,
		exclude_voucher_name=exclude_voucher_name,
		**dimension_filters,
	)
	return flt(actual) - flt(reserved)


def close_voucher_reservations(voucher_type, voucher_name):
	"""Cancel every active Stock Reservation Entry tied to a voucher.

	H.3: generic API. Called by voucher close paths (e.g., Work Order close
	once that doctype exists). Today usable on any voucher type that links
	to SREs via voucher_type/voucher_name.
	"""
	srs = frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"voucher_type": voucher_type,
			"voucher_no": voucher_name,
			"docstatus": 1,
			"status": ("not in", ["Delivered", "Closed", "Cancelled"]),
		},
		pluck="name",
	)
	for sre in srs:
		doc = frappe.get_doc("Stock Reservation Entry", sre)
		doc.flags.ignore_permissions = True
		doc.cancel()


def get_sre_reserved_qty(
	item_code=None,
	warehouse=None,
	exclude_voucher_type=None,
	exclude_voucher_name=None,
	filters=None,
	**dimension_filters,
):
	"""Sum of reserved_qty - delivered_qty - closed_qty across active SRE rows.

	Two calling styles are supported for back-compat:
	  - Modern:  get_sre_reserved_qty(item_code=..., warehouse=..., **dim_filters,
	                                  exclude_voucher_type=..., exclude_voucher_name=...)
	  - Legacy:  get_sre_reserved_qty(filters={...})  — single dict of column:value.

	Behavior:
	  - Active = docstatus=1 AND status NOT IN ('Delivered', 'Closed', 'Cancelled').
	    'Closed' SREs have been manually closed-short; their leftover is
	    released and must stop counting as live reserved.
	  - Dimension filters use NULL-matches-any (Gap #28): for each dim in
	    `dimension_filters`, an SRE row whose dim is either equal to the value
	    OR NULL is counted. This is conservative over-reservation — a NULL SRE
	    on a freshly-added dimension still blocks issues against any value of
	    that dimension.
	  - Exclude-self (Gap #2): when the calling voucher consumes its own
	    reservation, pass exclude_voucher_type/name to subtract that voucher's
	    own SRE rows from the total.
	"""
	# Normalize legacy dict arg.
	if filters is not None:
		merged = dict(filters)
		if item_code is None:
			item_code = merged.pop("item_code", None)
		if warehouse is None:
			warehouse = merged.pop("warehouse", None)
		# Anything else in filters is treated as a dimension filter.
		for k, v in merged.items():
			dimension_filters.setdefault(k, v)

	conds = ["docstatus = 1", "status NOT IN ('Delivered', 'Closed', 'Cancelled')"]
	values = []

	if item_code:
		conds.append("item_code = %s")
		values.append(item_code)
	if warehouse:
		conds.append("warehouse = %s")
		values.append(warehouse)

	dim_fieldnames = set(get_dimension_fieldnames())
	for fn, val in dimension_filters.items():
		if val is None:
			continue
		assert_safe_fieldname(fn)
		if fn in dim_fieldnames:
			# NULL-matches-any: SRE row with NULL dim is conservatively counted.
			conds.append(f"(`{fn}` = %s OR `{fn}` IS NULL OR `{fn}` = '')")
			values.append(val)
		else:
			conds.append(f"`{fn}` = %s")
			values.append(val)

	if exclude_voucher_type and exclude_voucher_name:
		conds.append("NOT (voucher_type = %s AND voucher_no = %s)")
		values.extend([exclude_voucher_type, exclude_voucher_name])

	where_sql = " AND ".join(conds)
	# Per-row floor at 0: a row whose delivered/closed total ever exceeds its
	# reserved_qty (e.g. excess delivery, 2026-07-10) must count as 0 remaining
	# — a raw SUM would let that negative remainder INFLATE the apparent
	# availability contributed by other reservations.
	row = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(GREATEST(reserved_qty - delivered_qty - COALESCE(closed_qty, 0), 0)), 0) AS qty
		FROM `tabStock Reservation Entry`
		WHERE {where_sql}
		""",
		tuple(values),
		as_dict=True,
	)
	return flt(row[0]["qty"]) if row else 0.0


def future_sle_count(args):
	"""Count uncancelled SLEs in the same bucket whose posting_datetime
	is strictly after `args.posting_datetime` (excluding the current voucher).

	Used to decide between inline repost and queued repost (Gap #18).
	"""
	conds = ["is_cancelled = 0", "item = %s", "warehouse = %s", "posting_datetime > %s"]
	values = [args.get("item"), args.get("warehouse"), args.get("posting_datetime")]
	for fn in get_dimension_fieldnames():
		val = args.get(fn)
		if val is None:
			continue
		assert_safe_fieldname(fn)
		conds.append(f"`{fn}` = %s")
		values.append(val)
	if args.get("voucher_no"):
		conds.append("voucher_no != %s")
		values.append(args.get("voucher_no"))
	row = frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE {' AND '.join(conds)}",
		tuple(values),
	)
	return int(row[0][0]) if row else 0


def has_pending_repost(item, warehouse):
	"""True if any Repost Item Valuation for this (item, warehouse) is queued
	or in progress (Gap #22)."""
	return bool(
		frappe.db.exists(
			"Repost Item Valuation",
			{
				"item": item,
				"warehouse": warehouse,
				"docstatus": 1,
				"status": ("in", ["Queued", "In Progress"]),
			},
		)
	)


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
		val = args.get(fn)
		if val is not None:
			conds[fn] = val
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


def apply_posting_datetime(doc):
	"""ERPNext ``set_posting_time`` semantics for the stock vouchers (DC / GRN / Stock Entry).

	Unless "Edit Posting Date and Time" is ticked, ``posting_date``/``posting_time``
	are stamped to NOW on every validate (draft saves and submit) so a stale client
	value can never backdate a voucher silently. Data import / restore auto-tick the
	flag so migrated documents keep their source dates (mirrors erpnext
	``TransactionBase.validate_posting_time``).
	"""
	if (frappe.flags.in_import or doc.flags.get("from_restore")) and doc.get("posting_date"):
		doc.edit_posting_date_and_time = 1

	if not doc.get("edit_posting_date_and_time"):
		now = now_datetime()
		doc.posting_date = now.strftime("%Y-%m-%d")
		doc.posting_time = now.strftime("%H:%M:%S.%f")
	elif doc.get("posting_time"):
		try:
			get_time(doc.posting_time)
		except ValueError:
			frappe.throw(_("Invalid Posting Time"))
