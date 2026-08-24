"""Dimension-aware stock ledger engine for YRP.

How valuation and quantity tracking work together:
--------------------------------------------------
Stock dimensions can be marked as "in_valuation" (affects cost) or not (tracking only).

Example configuration:
  - Production Group: in_valuation = True  (each group has its own cost)
  - Stock State:      in_valuation = False (tracking only)

This means:
  - FIFO and valuation_rate are shared per valuation-dimension bucket.

  - Quantity is tracked separately across all configured dimensions.

The stock_queue stored on each SLE is a JSON array of [qty, rate] pairs:
  e.g. [[100, 50.0], [50, 45.0]] means 100 units at 50 and 50 units at 45.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, nowdate

from yrp.stock.dimensions import (
	assert_safe_fieldname,
	get_dimension_fieldnames,
	get_valuation_dimensions,
)
from yrp.stock.utils import (
	future_sle_exists,
	get_combine_datetime,
	get_incoming_outgoing_rate_for_cancel,
	get_or_make_bin,
)
from yrp.stock.valuation import FIFOValuation, MovingAverageValuation, round_off_if_near_zero


class NegativeStockError(frappe.ValidationError):
	pass


class StockValuationPeriodClosedError(frappe.ValidationError):
	pass


ACTIVE_REPOST_STATUSES = ("Queued", "In Progress", "Failed")
MAX_REPOST_RETRY_COUNT = 3


def get_last_stock_valuation_closing_date():
	"""Return the system-maintained stock cutoff, or None before first close."""
	settings_meta = frappe.get_meta("YRP Stock Settings")
	if not settings_meta.get_field("last_stock_valuation_closing_date"):
		return None
	value = frappe.db.get_single_value(
		"YRP Stock Settings",
		"last_stock_valuation_closing_date",
		cache=False,
	)
	closing_date = getdate(value) if value else None
	# Frappe casts a missing Single Date value to date.min (0001-01-01).
	# Treat that sentinel as the intended blank initial state.
	return None if not closing_date or closing_date.year <= 1 else closing_date


def validate_stock_valuation_period(posting_date, voucher_type=None, voucher_no=None):
	"""Block stock creation, cancellation, or repost inside a closed period."""
	if not posting_date:
		return
	closing_date = get_last_stock_valuation_closing_date()
	if not closing_date or getdate(posting_date) > closing_date:
		return

	voucher = " ".join(str(value) for value in (voucher_type, voucher_no) if value)
	if voucher:
		message = _(
			"Stock valuation is closed through {0}. {1} dated {2} cannot update stock. "
			"Cancel the latest Stock Valuation Closing or use a posting date after {0}."
		).format(formatdate(closing_date), voucher, formatdate(posting_date))
	else:
		message = _(
			"Stock valuation is closed through {0}. Stock dated {1} cannot be updated. "
			"Cancel the latest Stock Valuation Closing or use a posting date after {0}."
		).format(formatdate(closing_date), formatdate(posting_date))
	frappe.throw(
		message,
		title=_("Stock Valuation Period Closed"),
		exc=StockValuationPeriodClosedError,
	)


def _validate_sl_entries_period(sl_entries):
	from yrp.yrp_stock.doctype.stock_valuation_closing.stock_valuation_closing import (
		lock_stock_valuation_period,
	)

	lock_stock_valuation_period(shared=True)
	checked = set()
	for entry in sl_entries:
		key = (
			entry.get("posting_date"),
			entry.get("voucher_type"),
			entry.get("voucher_no"),
		)
		if key in checked:
			continue
		checked.add(key)
		validate_stock_valuation_period(*key)


def _validate_no_active_valuation_for_cancel(sl_entries):
	"""Do not remove ledger nodes owned by unfinished or unreversed valuation."""
	if not sl_entries or not frappe.db.exists("DocType", "Stock Valuation Adjustment"):
		return
	voucher_type = sl_entries[0].get("voucher_type")
	voucher_no = sl_entries[0].get("voucher_no")
	if not voucher_type or not voucher_no:
		return
	sle_names = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"is_cancelled": 0,
		},
		pluck="name",
	)
	if not sle_names:
		return
	blocking_condition = """
		(
			adjustment.status NOT IN ('Completed', 'Reversed')
			OR (
				adjustment.status = 'Completed'
				AND COALESCE(adjustment.reversal_of, '') = ''
			)
		)
	"""
	active = frappe.db.sql(
		"""
		SELECT DISTINCT adjustment.name, adjustment.status, adjustment.reversal_of
		FROM `tabStock Valuation Adjustment` adjustment
		INNER JOIN `tabStock Valuation Adjustment Source` source
			ON source.parent = adjustment.name
		WHERE adjustment.docstatus = 1
		  AND {blocking_condition}
		  AND (source.target_sle IN %(sle_names)s OR source.source_sle IN %(sle_names)s)
		ORDER BY adjustment.creation, adjustment.name
		LIMIT 1
		FOR UPDATE
		""".format(blocking_condition=blocking_condition),
		{"sle_names": tuple(sle_names)},
		as_dict=True,
	)
	if not active:
		active = frappe.db.sql(
			"""
			SELECT DISTINCT adjustment.name, adjustment.status, adjustment.reversal_of
			FROM `tabStock Valuation Adjustment` adjustment
			INNER JOIN `tabStock Valuation Propagation Entry` propagation
				ON propagation.adjustment = adjustment.name
			WHERE adjustment.docstatus = 1
			  AND {blocking_condition}
			  AND (propagation.target_sle IN %(sle_names)s OR propagation.source_sle IN %(sle_names)s)
			ORDER BY adjustment.creation, adjustment.name
			LIMIT 1
			FOR UPDATE
			""".format(blocking_condition=blocking_condition),
			{"sle_names": tuple(sle_names)},
			as_dict=True,
		)
	if active:
		adjustment = active[0]
		if adjustment.status == "Completed" and not adjustment.reversal_of:
			frappe.throw(
				_(
					"{0} {1} is part of completed Stock Valuation Adjustment {2}. "
					"Reverse that valuation and wait for the signed reversal to complete before cancelling this voucher."
				).format(voucher_type, voucher_no, adjustment.name),
				title=_("Completed Valuation Lineage"),
			)
		frappe.throw(
			_(
				"{0} {1} is part of unfinished Stock Valuation Adjustment {2}. "
				"Wait for its valuation or reversal to complete before cancelling this voucher."
			).format(voucher_type, voucher_no, adjustment.name),
			title=_("Valuation Update In Progress"),
		)


def _deactivate_production_links_for_cancel(sle_names):
	"""Retain production lineage audit but stop traversal through cancelled SLEs."""
	if not sle_names or not frappe.db.exists(
		"DocType", "Stock Valuation Production Link"
	):
		return
	links = frappe.db.sql(
		"""
		SELECT name
		FROM `tabStock Valuation Production Link`
		WHERE active=1
		  AND (consumption_sle IN %(sle_names)s OR output_receipt_sle IN %(sle_names)s)
		ORDER BY name
		FOR UPDATE
		""",
		{"sle_names": tuple(sle_names)},
		pluck=True,
	)
	for link_name in links:
		frappe.db.set_value(
			"Stock Valuation Production Link",
			link_name,
			"active",
			0,
			update_modified=False,
		)


def lock_valuation_bucket(bucket):
	"""Serialize every replay that shares one valuation-dimension bucket.

	A Bin row alone cannot protect a brand-new bucket: two transactions can
	create different tracking-dimension Bins inside the same valuation bucket
	without seeing one another's uncommitted rows.  The transaction-scoped
	advisory mutex closes that bootstrap gap; Bin row locks retain the normal
	database serialization once rows exist.
	"""
	_acquire_valuation_bucket_mutex(bucket)
	filters = {
		"item_code": bucket.get("item"),
		"warehouse": bucket.get("warehouse"),
	}
	for fieldname in get_valuation_dimensions():
		filters[fieldname] = bucket.get(fieldname)
	bin_names = frappe.get_all(
		"Bin", filters=filters, pluck="name", order_by="name asc"
	)
	for bin_name in bin_names:
		frappe.db.sql("SELECT name FROM `tabBin` WHERE name=%s FOR UPDATE", (bin_name,))


def _valuation_bucket_mutex_name(bucket):
	values = (
		frappe.conf.get("db_name") or frappe.local.site,
		str(bucket.get("item") or ""),
		str(bucket.get("warehouse") or ""),
		tuple(
			(fieldname, str(bucket.get(fieldname) or ""))
			for fieldname in get_valuation_dimensions()
		),
	)
	digest = hashlib.sha256(repr(values).encode()).hexdigest()[:40]
	return f"yrp-valuation-{digest}"


def _acquire_valuation_bucket_mutex(bucket):
	"""Hold a MariaDB named lock until the current transaction ends."""
	lock_name = _valuation_bucket_mutex_name(bucket)
	acquired = getattr(frappe.local, "yrp_valuation_bucket_mutexes", None)
	if acquired is None:
		acquired = set()
		frappe.local.yrp_valuation_bucket_mutexes = acquired
	if lock_name in acquired:
		return

	timeout = cint(frappe.conf.get("stock_valuation_lock_timeout")) or 30
	result = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
	if not result or cint(result[0][0]) != 1:
		frappe.throw(
			_("Could not lock the stock valuation bucket. Please retry the transaction."),
			title=_("Valuation Update Busy"),
		)
	acquired.add(lock_name)

	def release():
		try:
			# DO avoids opening a new consistent-read snapshot in the fresh
			# transaction that Frappe starts immediately after commit/rollback.
			frappe.db.sql("DO RELEASE_LOCK(%s)", (lock_name,))
		except Exception:
			frappe.logger("yrp").exception(
				"Unable to release stock valuation mutex %s", lock_name
			)
		finally:
			acquired.discard(lock_name)

	frappe.db.after_commit.add(release)
	frappe.db.after_rollback.add(release)


def _valuation_bucket_key(row, dimension_fields):
	return (
		str(row.get("item") or ""),
		str(row.get("warehouse") or ""),
		tuple(
			(fieldname, str(row.get(fieldname) or ""))
			for fieldname in dimension_fields
			if fieldname in get_valuation_dimensions()
		),
	)


def _lock_entry_buckets(rows, dimension_fields):
	"""Lock all touched valuation buckets in one deterministic order."""
	buckets = {}
	for row in rows or []:
		row = frappe._dict(row)
		if not row.item or not row.warehouse:
			continue
		bucket = {"item": row.item, "warehouse": row.warehouse}
		for fieldname in dimension_fields:
			bucket[fieldname] = row.get(fieldname)
		buckets[_valuation_bucket_key(bucket, dimension_fields)] = bucket
	for key in sorted(buckets):
		bucket = buckets[key]
		lock_valuation_bucket(bucket)


def _is_effective_sl_entry(row):
	"""Return whether a controller row can produce an SLE."""
	row = frappe._dict(row)
	if not row.item:
		return False
	parent_item = frappe.get_cached_value("Item Variant", row.item, "item")
	if not parent_item or not frappe.get_cached_value("Item", parent_item, "is_stock_item"):
		return False
	return bool(row.get("qty") or row.get("voucher_type") == "Stock Reconciliation")


def _lock_voucher_sles_for_cancel(sl_entries, dimension_fields):
	"""Freeze the voucher's ledger nodes before checking valuation ownership."""
	if not sl_entries:
		return []
	voucher_type = sl_entries[0].get("voucher_type")
	voucher_no = sl_entries[0].get("voucher_no")
	if not voucher_type or not voucher_no:
		return []
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"is_cancelled": 0,
		},
		fields=["name", "item", "warehouse", *dimension_fields],
	)
	_lock_entry_buckets(rows, dimension_fields)
	for name in sorted(row.name for row in rows):
		frappe.db.sql(
			"SELECT name FROM `tabStock Ledger Entry` WHERE name=%s FOR UPDATE",
			(name,),
		)
	return sorted(row.name for row in rows)


def _should_queue_repost(args):
	from yrp.stock.utils import future_sle_count

	threshold = (
		frappe.db.get_single_value("YRP Stock Settings", "backdated_repost_threshold") or 0
	)
	if threshold <= 0:
		return False
	return future_sle_count(args) > int(threshold)


def _is_retryable_repost(row):
	if row.status != "Failed":
		return True
	return cint(row.retry_count) < MAX_REPOST_RETRY_COUNT


def _get_matching_reposts(filters):
	rows = frappe.get_all(
		"Repost Item Valuation",
		filters={
			**filters,
			"docstatus": 1,
			"status": ["in", ACTIVE_REPOST_STATUSES],
		},
		fields=["name", "status", "retry_count", "posting_date", "posting_time"],
		order_by="posting_date asc, posting_time asc, creation asc",
	)
	return [
		frappe._dict(row)
		for row in rows
		if _is_retryable_repost(frappe._dict(row))
	]


def _dedupe_bucket_repost(values):
	"""Return an existing active bucket repost if it can cover this request.

	Queued/failed jobs for the same bucket are reused and widened to the
	earliest posting datetime. An in-progress job is reused only when it already
	started at or before the requested datetime.
	"""
	filters = {
		"based_on": "Item and Warehouse",
		"item": values.get("item"),
		"warehouse": values.get("warehouse"),
	}
	for fn in get_dimension_fieldnames():
		filters[fn] = values.get(fn)

	matches = _get_matching_reposts(filters)
	if not matches:
		return None

	requested_dt = get_combine_datetime(
		values.get("posting_date"),
		values.get("posting_time") or "00:00:00",
	)
	for row in matches:
		existing_dt = get_combine_datetime(row.posting_date, row.posting_time or "00:00:00")
		if existing_dt <= requested_dt:
			return row.name
		if row.status != "In Progress":
			frappe.db.set_value(
				"Repost Item Valuation",
				row.name,
				{
					"posting_date": values.get("posting_date"),
					"posting_time": values.get("posting_time") or "00:00:00",
					"voucher_type": values.get("voucher_type"),
					"voucher_no": values.get("voucher_no"),
				},
				update_modified=False,
			)
			return row.name
	return None


def _dedupe_transaction_repost(values):
	filters = {
		"based_on": "Transaction",
		"voucher_type": values.get("voucher_type"),
		"voucher_no": values.get("voucher_no"),
	}
	matches = _get_matching_reposts(filters)
	return matches[0].name if matches else None


def _enqueue_backdated_repost(args):
	"""Create a Repost Item Valuation doc and submit it; the queue worker
	picks it up. Inline path is skipped — the new SLE is already inserted,
	so the bucket is in a consistent (if temporarily stale) state."""
	dim_fields = get_dimension_fieldnames()
	values = {
		"doctype": "Repost Item Valuation",
		"based_on": "Item and Warehouse",
		"item": args.get("item"),
		"warehouse": args.get("warehouse"),
		"posting_date": args.get("posting_date"),
		"posting_time": args.get("posting_time") or "00:00:00",
		"voucher_type": args.get("voucher_type"),
		"voucher_no": args.get("voucher_no"),
		"allow_negative_stock": 1,
		"allow_zero_rate": args.get("allow_zero_rate", 0),
	}
	for fn in dim_fields:
		values[fn] = args.get(fn)
	if _dedupe_bucket_repost(values):
		return
	doc = frappe.get_doc(values)
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()


def voucher_has_future_sles(voucher_type, voucher_no, posting_date, posting_time=None):
	"""True if any other voucher has SLEs strictly after this voucher's posting
	datetime in any (item, warehouse) bucket this voucher touched.

	"Future" means strictly greater than this voucher's posting_datetime — SLEs
	at the same datetime don't need repost (inline path during make_sl_entries
	already handled them via UpdateEntriesAfter).
	"""
	from yrp.stock.utils import get_combine_datetime

	if not posting_date:
		return False
	posting_dt = get_combine_datetime(posting_date, posting_time or "00:00:00")
	touched = frappe.db.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"is_cancelled": 0,
		},
		fields=["item", "warehouse"],
		distinct=True,
	)
	for bucket in touched:
		if frappe.db.exists(
			"Stock Ledger Entry",
			{
				"item": bucket.item,
				"warehouse": bucket.warehouse,
				"is_cancelled": 0,
				"posting_datetime": [">", posting_dt],
			},
		):
			return True
	return False


def enqueue_voucher_repost(doc):
	"""Explicit voucher-level Repost Item Valuation dispatch.

	Call from a stock voucher's on_submit and on_cancel after make_sl_entries.
	No-ops when there are no future SLEs in any touched bucket — typical
	non-backdated submits and most cancels return without queueing.

	Why this exists: make_sl_entries handles inline per-SLE repost via
	UpdateEntriesAfter, which fixes the current voucher's own bucket. For
	backdated submits where OTHER vouchers have later SLEs in the same
	buckets, those neighbouring SLEs need re-walking — that's what the queued
	RIV worker does. Stock-dimension Custom Fields on RIV are reqd=1
	unconditionally but only apply to based_on="Item and Warehouse", so we
	pass ignore_mandatory=True; RIV.validate() still enforces the correct
	check (voucher_type/voucher_no) for the Transaction path.
	"""
	if not voucher_has_future_sles(doc.doctype, doc.name, doc.posting_date, doc.posting_time):
		return
	riv = frappe.get_doc({
		"doctype": "Repost Item Valuation",
		"based_on": "Transaction",
		"voucher_type": doc.doctype,
		"voucher_no": doc.name,
		"posting_date": doc.posting_date,
		"posting_time": doc.posting_time or "00:00:00",
		"allow_negative_stock": 1,
	})
	if _dedupe_transaction_repost(riv.as_dict()):
		return
	riv.flags.ignore_permissions = True
	riv.insert(ignore_mandatory=True)
	riv.submit()


def _item_allows_negative_stock(item_variant):
	"""Per-Item negative-stock flag (D-009). Resolves Item Variant -> parent Item."""
	if not item_variant:
		return False
	parent = frappe.get_cached_value("Item Variant", item_variant, "item")
	if not parent:
		return False
	return bool(frappe.get_cached_value("Item", parent, "allow_negative_stock"))


# ======================================================================
# make_sl_entries — called by Stock Entry, Stock Update, Stock
# Reconciliation controllers on submit and cancel
# ======================================================================
def make_sl_entries(
	sl_entries,
	cancel=False,
	allow_negative_stock=False,
	return_details=False,
	force_inline=False,
):
	"""Create Stock Ledger Entries and recompute valuation.

	For each SLE dict in the list:
	  1. Create the SLE document
	  2. Get or create the Bin for this (item, warehouse, *dimensions)
	  3. Run the valuation engine to recompute forward
	  4. Update the Bin with fresh qty and rate

	Paired internal movements may include private ``_transfer_key`` and
	``_transfer_role`` markers. After valuing the outgoing side, its actual
	value reduction becomes the incoming rate. This guarantees that an
	internal movement cannot create or destroy stock value.

	Production consumers may add a private ``_result_key`` marker and request
	``return_details=True``. The returned detail contains the exact value that
	FIFO or Moving Average removed for that outgoing entry. ``force_inline`` is
	used by compound production receipts whose incoming output value depends on
	the consumed value in the same database transaction.
	"""
	if not sl_entries:
		return {"transfer_rates": {}, "entries": {}} if return_details else {}
	# This must run before cancellation marks the voucher's existing SLEs as
	# cancelled. A closed-period rejection therefore leaves the ledger untouched.
	_validate_sl_entries_period(sl_entries)
	dim_fields = get_dimension_fieldnames()
	# Multi-row vouchers lock every bucket globally before creating/replaying any
	# SLE. Two transactions touching A+B in opposite row order therefore cannot
	# deadlock one another while each holds half of the voucher's buckets.
	effective_entries = [row for row in sl_entries if _is_effective_sl_entry(row)]
	_lock_entry_buckets(effective_entries, dim_fields)

	# When cancelling, first mark all existing SLEs for this voucher as cancelled
	if cancel:
		# The ownership query must be protected by the same SLE locks acquired by
		# adjustment creation. A new SVA can no longer commit between a clean guard
		# result and cancellation of its target receipt.
		cancelled_sles = _lock_voucher_sles_for_cancel(sl_entries, dim_fields)
		_validate_no_active_valuation_for_cancel(sl_entries)
		_deactivate_production_links_for_cancel(cancelled_sles)
		_set_voucher_cancelled(sl_entries[0])

	transfer_rates = {}
	transfer_sles = {}
	result_details = {}

	for raw_sle in sl_entries:
		# Controller-only transfer markers must not be persisted on the SLE.
		sle = dict(raw_sle)
		transfer_key = sle.pop("_transfer_key", None)
		transfer_role = sle.pop("_transfer_role", None)
		result_key = sle.pop("_result_key", None)
		if (
			not cancel
			and transfer_role == "incoming"
			and transfer_key in transfer_rates
		):
			sle["rate"] = transfer_rates[transfer_key]

		# Skip non-stock items
		item_variant = sle.get("item")
		if not item_variant:
			continue
		parent_item = frappe.get_cached_value("Item Variant", item_variant, "item")
		if not parent_item or not frappe.get_cached_value("Item", parent_item, "is_stock_item"):
			continue

		# For cancellation, mark entry and derive outgoing rate if needed
		if cancel:
			sle["is_cancelled"] = 1
			if sle.get("qty") and sle["qty"] < 0 and not sle.get("outgoing_rate"):
				sle["outgoing_rate"] = get_incoming_outgoing_rate_for_cancel(
					sle["item"], sle["voucher_type"], sle["voucher_no"], sle.get("voucher_detail_no")
				)

		# Skip entries with zero qty (except Stock Reconciliation which uses qty=0)
		if not (sle.get("qty") or sle.get("voucher_type") == "Stock Reconciliation"):
			continue

		# Step 1: Create the SLE document
		sle_doc = _create_sle_document(sle)
		if not cancel and transfer_key:
			if transfer_role == "outgoing":
				transfer_sles[transfer_key] = sle_doc.name
			elif transfer_role == "incoming" and transfer_key in transfer_sles:
				outgoing_sle = transfer_sles[transfer_key]
				frappe.db.set_value(
					"Stock Ledger Entry",
					outgoing_sle,
					"paired_stock_ledger_entry",
					sle_doc.name,
					update_modified=False,
				)
				frappe.db.set_value(
					"Stock Ledger Entry",
					sle_doc.name,
					"paired_stock_ledger_entry",
					outgoing_sle,
					update_modified=False,
				)
		args = sle_doc.as_dict()
		args["posting_datetime"] = get_combine_datetime(args.posting_date, args.posting_time)

		# Step 2: Get or create Bin for this item + warehouse + dimensions
		dimension_values = {fn: args.get(fn) for fn in dim_fields}
		bin_name = get_or_make_bin(args["item"], args["warehouse"], **dimension_values)

		# Lock every Bin sharing the valuation dimensions before replay. This is
		# also the lock order used by SVA and RIV workers.
		lock_valuation_bucket(args)

		# Reservation no longer mutates Bin in the new design (D-008).
		# Compute reserved-stock fresh from active SREs.
		from yrp.stock.utils import get_sre_reserved_qty
		args["reserved_stock"] = get_sre_reserved_qty(
			item_code=args["item"], warehouse=args["warehouse"], **dimension_values
		)

		# Step 3: Recompute valuation from this SLE forward.
		# D.1 (Gap #18): if more than `backdated_repost_threshold` future SLEs
		# exist in this bucket, queue a background repost instead of running
		# the engine inline. Inline path is preferred for the common case
		# (no/few future SLEs) because it commits the new state atomically.
		queued_repost = False if force_inline else _should_queue_repost(args)
		if queued_repost:
			_enqueue_backdated_repost(args)
		else:
			repost_current_voucher(args, allow_negative_stock=allow_negative_stock)

		if not cancel and transfer_role == "outgoing" and transfer_key:
			if queued_repost:
				# The actual historical value will be available only after the
				# queued repost. Use the source rate captured by the voucher so
				# both legs still carry the same provisional value.
				transfer_rate = flt(sle.get("outgoing_rate"))
			else:
				stock_value_difference = flt(
					frappe.db.get_value(
						"Stock Ledger Entry", sle_doc.name, "stock_value_difference"
					)
				)
				transfer_rate = (
					abs(stock_value_difference) / abs(flt(sle.get("qty")))
					if flt(sle.get("qty"))
					else 0.0
				)
				# Keep the source SLE audit field aligned with the cost that
				# was actually removed by FIFO or Moving Average.
				frappe.db.set_value(
					"Stock Ledger Entry",
					sle_doc.name,
					"outgoing_rate",
					transfer_rate,
					update_modified=False,
				)
			transfer_rates[transfer_key] = transfer_rate

		if not cancel and result_key:
			stock_value_difference = flt(
				frappe.db.get_value(
					"Stock Ledger Entry", sle_doc.name, "stock_value_difference"
				)
			)
			qty = abs(flt(sle.get("qty")))
			result_details[result_key] = {
				"sle": sle_doc.name,
				"qty": qty,
				"value": abs(stock_value_difference),
				"rate": abs(stock_value_difference) / qty if qty else 0.0,
				"queued_repost": queued_repost,
			}

		# Step 4: Refresh the Bin with updated qty and rate
		from yrp.yrp_stock.doctype.bin.bin import update_qty as update_bin_qty
		update_bin_qty(bin_name, args)

	if return_details:
		return {"transfer_rates": transfer_rates, "entries": result_details}
	return transfer_rates


def _set_voucher_cancelled(sl_entry):
	"""Mark all existing SLEs for this voucher as cancelled."""
	frappe.db.sql(
		"UPDATE `tabStock Ledger Entry` SET is_cancelled=1, modified=%s, modified_by=%s "
		"WHERE voucher_type=%s AND voucher_no=%s AND is_cancelled=0",
		(frappe.utils.now(), frappe.session.user, sl_entry["voucher_type"], sl_entry["voucher_no"]),
	)


def _create_sle_document(args):
	"""Create and submit a Stock Ledger Entry from a dict of field values."""
	doc = frappe.new_doc("Stock Ledger Entry")
	for key, value in args.items():
		if hasattr(doc, key):
			doc.set(key, value)
	doc.flags.ignore_permissions = 1
	doc.submit()
	return doc


# ======================================================================
# repost_current_voucher — runs the valuation engine for one voucher
# ======================================================================
def repost_current_voucher(args, allow_negative_stock=False):
	"""Recompute valuation starting from this voucher's posting datetime."""
	if not (args.get("qty") or args.get("voucher_type") == "Stock Reconciliation"):
		return
	if not args.get("posting_date"):
		args["posting_date"] = nowdate()
	validate_stock_valuation_period(
		args.get("posting_date"), args.get("voucher_type"), args.get("voucher_no")
	)

	engine_args = {
		"item": args.get("item"),
		"warehouse": args.get("warehouse"),
		"posting_date": args.get("posting_date"),
		"posting_time": args.get("posting_time"),
		"voucher_type": args.get("voucher_type"),
		"voucher_no": args.get("voucher_no"),
		"sle_id": args.get("name"),
		"creation": args.get("creation"),
		"reserved_stock": args.get("reserved_stock"),
	}
	# Include all dimension values
	for fn in get_dimension_fieldnames():
		engine_args[fn] = args.get(fn)

	UpdateEntriesAfter(engine_args, allow_negative_stock=allow_negative_stock).run()


# ======================================================================
# get_previous_sle — find the most recent SLE before a given datetime
# ======================================================================
def get_previous_sle(args, dim_fields=None, strictly_before=False):
	"""Find the most recent SLE at or before the given posting_datetime.

	Args:
		args: dict with item, warehouse, posting_datetime, and dimension values
		dim_fields: which dimension fields to filter by.
			- None (default) = ALL dimensions (used by get_stock_balance)
			- explicit list = only those (used by valuation engine for valuation-scoped queries)
		strictly_before: use ``<`` rather than ``<=``. Forward valuation uses
			this so every SLE at the starting timestamp is processed exactly
			once; point-in-time balance lookups retain inclusive semantics.
	"""
	if dim_fields is None:
		dim_fields = get_dimension_fieldnames()

	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(sle)
		.select(sle.star)
		.where(sle.item == args.get("item"))
		.where(sle.warehouse == args.get("warehouse"))
		.where(sle.is_cancelled == 0)
	)

	# Filter by the requested dimensions
	for fn in dim_fields:
		val = args.get(fn)
		if val is not None:
			query = query.where(sle[fn] == val)

	# Only look at SLEs at or before the requested datetime
	if args.get("posting_datetime"):
		if strictly_before:
			query = query.where(sle.posting_datetime < args["posting_datetime"])
		else:
			query = query.where(sle.posting_datetime <= args["posting_datetime"])

	# Exclude the current voucher (so it doesn't find itself as "previous")
	if args.get("voucher_no"):
		query = query.where(sle.voucher_no != args["voucher_no"])

	# Get the most recent one
	query = query.orderby(sle.posting_datetime, order=frappe.qb.desc)
	query = query.orderby(sle.creation, order=frappe.qb.desc)
	query = query.orderby(sle.name, order=frappe.qb.desc)
	query = query.limit(1)

	rows = query.run(as_dict=True)
	return frappe._dict(rows[0]) if rows else None


# ======================================================================
# UpdateEntriesAfter — the core valuation engine
# ======================================================================
class UpdateEntriesAfter:
	"""Recompute valuation for a (item, warehouse, *valuation_dims) bucket.

	Example with one valuation dimension and one tracking-only dimension:

	  Config:
	    Item = "T-Shirt Blue", Warehouse = "WH-1", Production Group = "GROUP-001"
	    SLEs exist for stock_state = "Fresh" and "Used"

	  What this engine does:
	    1. Fetches all SLEs for the valuation bucket — both Fresh and Used
	    2. Processes them in chronological order through one shared FIFO queue
	    3. Writes back:
	       - valuation_rate = shared rate within the valuation bucket
	       - qty_after_transaction = per (Fresh) or per (Used) running balance
	"""

	def __init__(self, args, allow_negative_stock=False):
		self.args = frappe._dict(args)

		# All configured dimensions — for quantity tracking
		self.dim_fields = get_dimension_fieldnames()

		# Only valuation dimensions (e.g., ["lot"]) — for FIFO queue scoping
		self.valuation_dim_fields = get_valuation_dimensions()

		self.valuation_method = (
			frappe.db.get_single_value("YRP Stock Settings", "default_valuation_method") or "FIFO"
		)
		# D-009: negative stock is per-Item now. The legacy
		# YRP Stock Settings.allow_negative_stock flag is ignored.
		self.allow_negative_stock = allow_negative_stock or _item_allows_negative_stock(
			self.args.get("item")
		)
		self.allow_zero_rate = self.args.get("allow_zero_rate", False)

		# Previous SLE document (for reference only)
		self.previous_sle = None

		# Shared valuation state — one FIFO queue per valuation bucket
		# stock_queue example: [[100, 50.0], [50, 45.0]]
		self.stock_queue = []
		self.stock_value = 0.0
		self.valuation_rate = 0.0

		# Qty tracked per full dimension combination
		# Example: {("LOT-001", "Fresh"): 80.0, ("LOT-001", "Used"): 50.0}
		self.qty_by_dims = {}

	def run(self):
		"""Main execution: load previous state, then process each SLE forward."""
		self._init_previous()
		entries = self._get_entries_to_process()
		for sle in entries:
			self._process_sle(sle)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------
	def _dim_key(self, row):
		"""Build a unique key from ALL dimension values on a row.

		This key identifies a specific (lot, received_type) combination
		for qty tracking. Example: ("LOT-001", "Fresh")
		"""
		key_parts = []
		for fieldname in self.dim_fields:
			value = row.get(fieldname) or ""
			key_parts.append(value)
		return tuple(key_parts)

	# ------------------------------------------------------------------
	# Load previous state
	# ------------------------------------------------------------------
	def _init_previous(self):
		"""Load the starting state before we begin processing SLEs.

		Two things are loaded:
		  1. The shared valuation state (FIFO queue, rate) from the last SLE
		     in this valuation bucket
		  2. The per-dimension qty totals from all SLEs before our start point
		"""
		posting_dt = get_combine_datetime(self.args.posting_date, self.args.posting_time)

		# Load shared valuation state from the most recent SLE (valuation dims only)
		previous_sle = get_previous_sle(
			{**self.args, "posting_datetime": posting_dt},
			dim_fields=self.valuation_dim_fields,
			strictly_before=True,
		)
		if previous_sle:
			self.previous_sle = previous_sle
			# Parse the FIFO queue from JSON
			try:
				self.stock_queue = json.loads(previous_sle.stock_queue or "[]")
			except json.JSONDecodeError:
				self.stock_queue = []
			self.stock_value = flt(previous_sle.stock_value)
			self.valuation_rate = flt(previous_sle.valuation_rate)

		# Load per-dimension qty totals
		self._load_prior_dim_qtys()

	def _load_prior_dim_qtys(self):
		"""Load the latest running qty for each full dimension combination.

		Stock Reconciliation rows are absolute balance snaps. The source of
		truth before a processing window is therefore the latest prior
		qty_after_transaction, not SUM(qty).
		"""
		posting_dt = get_combine_datetime(self.args.posting_date, self.args.posting_time)
		dim_fields = list(self.dim_fields)
		for fn in dim_fields + list(self.valuation_dim_fields):
			assert_safe_fieldname(fn)

		conditions = [
			"item = %s",
			"warehouse = %s",
			"is_cancelled = 0",
			"posting_datetime < %s",
		]
		values = [self.args.item, self.args.warehouse, posting_dt]
		for fn in self.valuation_dim_fields:
			val = self.args.get(fn)
			if val is not None:
				conditions.append(f"`{fn}` = %s")
				values.append(val)

		dim_select = ", ".join(f"`{fn}`" for fn in dim_fields)
		partition_by = ", ".join(f"`{fn}`" for fn in dim_fields) or "item, warehouse"
		select_prefix = f"{dim_select}, " if dim_select else ""
		rows = frappe.db.sql(
			f"""
			SELECT {select_prefix}qty_after_transaction
			FROM (
				SELECT
					{select_prefix}qty_after_transaction,
					ROW_NUMBER() OVER (
						PARTITION BY {partition_by}
						ORDER BY posting_datetime DESC, creation DESC, name DESC
					) AS rn
				FROM `tabStock Ledger Entry`
				WHERE {" AND ".join(conditions)}
			) latest
			WHERE rn = 1
			""",
			values,
			as_dict=True,
		)
		for row in rows:
			key = self._dim_key(row)
			self.qty_by_dims[key] = flt(row.qty_after_transaction)

	# ------------------------------------------------------------------
	# Fetch SLEs to process
	# ------------------------------------------------------------------
	def _get_entries_to_process(self):
		"""Fetch all SLEs from posting_datetime onwards for this valuation bucket.

		Filters by VALUATION dimensions only (e.g., lot) so that SLEs for
		different non-valuation dimensions (e.g., Fresh and Used) are fetched
		together and processed through one shared FIFO queue.
		"""
		sle = frappe.qb.DocType("Stock Ledger Entry")
		query = (
			frappe.qb.from_(sle)
			.select(sle.star)
			.where(sle.item == self.args.item)
			.where(sle.warehouse == self.args.warehouse)
			.where(sle.is_cancelled == 0)
		)

		# Only filter by valuation dimensions (NOT all dimensions)
		for fn in self.valuation_dim_fields:
			val = self.args.get(fn)
			if val is not None:
				query = query.where(sle[fn] == val)

		posting_dt = get_combine_datetime(self.args.posting_date, self.args.posting_time)
		query = query.where(sle.posting_datetime >= posting_dt)
		query = query.orderby(sle.posting_datetime).orderby(sle.creation)
		query = query.orderby(sle.name)

		return [frappe._dict(r) for r in query.run(as_dict=True)]

	# ------------------------------------------------------------------
	# Process a single SLE
	# ------------------------------------------------------------------
	def _process_sle(self, sle):
		"""Process one SLE: adjust the FIFO queue and qty, then write back."""
		# Create a fresh valuator from current queue state
		if self.valuation_method == "Moving Average":
			valuator = MovingAverageValuation(self.stock_queue)
		else:
			valuator = FIFOValuation(self.stock_queue)

		# Get the running qty for THIS SLE's specific dimension combination
		dim_key = self._dim_key(sle)
		current_dim_qty = self.qty_by_dims.get(dim_key, 0.0)

		# Route to the appropriate handler based on transaction type
		if sle.voucher_type == "Stock Reconciliation":
			new_dim_qty = self._handle_reconciliation(sle, valuator, current_dim_qty)
		elif sle.qty > 0:
			new_dim_qty = self._handle_incoming(sle, valuator, current_dim_qty)
		else:
			new_dim_qty = self._handle_outgoing(sle, valuator, current_dim_qty)

		# Update the per-dimension qty
		self.qty_by_dims[dim_key] = new_dim_qty

		# Update the shared valuation state and write back to the SLE
		self._update_valuation_and_write(sle, valuator, dim_key)

	def _handle_reconciliation(self, sle, valuator, current_dim_qty):
		"""Handle Stock Reconciliation — snaps the per-dimension qty to target.

		Diff-based: the difference between target and current qty is added or
		removed from the shared FIFO queue. This preserves consistency
		between the queue total and the sum of qty_by_dims when sibling dims
		(e.g., another received_type within the same valuation bucket) hold
		non-zero balances.

		I.7 (D-009) — recon while bucket is negative: the diff-based add still
		correctly absorbs the negative entry (FIFOValuation.add_stock handles
		negative absorption when the last bin has negative qty). MA's
		add_stock handles it via the weighted-average formula. So no special
		"wipe" branch is needed — and a wipe would corrupt sibling-dim qty
		invariants.
		"""
		target_qty = flt(sle.qty_after_transaction)
		rate = flt(sle.rate)

		diff = target_qty - current_dim_qty
		if diff > 0:
			valuator.add_stock(diff, rate)
		elif diff < 0:
			valuator.remove_stock(abs(diff), flt(sle.outgoing_rate))
		else:
			# Qty is correct but rate may need correction. Only safe to
			# rebuild the queue when no sibling dims have a stake — i.e.,
			# when this dim's qty equals the entire queue total.
			if (
				rate
				and target_qty > 0
				and isinstance(valuator, FIFOValuation)
				and abs(valuator.get_total_stock_and_value()[0] - target_qty) < 0.001
			):
				valuator.queue.clear()
				valuator.queue.append([target_qty, rate])

		return target_qty

	def _handle_incoming(self, sle, valuator, current_dim_qty):
		"""Handle incoming stock (qty > 0) — add to FIFO queue.

		I.8: zero-rate is surfaced as a form warning, not a hard block.
		"""
		if not self.allow_zero_rate and not flt(sle.rate):
			frappe.msgprint(
				_("Warning: incoming stock of {0} at {1} has zero valuation rate.").format(
					sle.item, sle.warehouse
				),
				indicator="orange",
				alert=True,
			)

		qty = flt(sle.qty)
		# Late-cost propagation keeps the voucher's original incoming rate as an
		# immutable audit value.  Applied Stock Valuation Propagation Entries add
		# a signed value overlay to this exact receipt SLE; dividing by the receipt
		# quantity yields its effective replay rate without changing stock qty.
		effective_rate = flt(sle.rate)
		if qty:
			effective_rate += flt(sle.get("valuation_adjustment_value")) / qty
		valuator.add_stock(qty, effective_rate)
		return current_dim_qty + flt(sle.qty)

	def _handle_outgoing(self, sle, valuator, current_dim_qty):
		"""Handle outgoing stock (qty < 0) — remove from FIFO queue.

		I.6: negative stock is allowed only when the per-Item flag is set.
		Reservation check is enforced separately at the voucher layer (H.2).
		"""
		new_qty = current_dim_qty + flt(sle.qty)  # sle.qty is negative

		if not self.allow_negative_stock and new_qty < 0:
			frappe.throw(
				_("Insufficient stock for {0} at {1}: balance {2}, requested {3}").format(
					sle.item, sle.warehouse, current_dim_qty, abs(sle.qty)
				),
				exc=NegativeStockError,
			)

		valuator.remove_stock(abs(flt(sle.qty)), flt(sle.outgoing_rate))
		return new_qty

	def _update_valuation_and_write(self, sle, valuator, dim_key):
		"""Update shared valuation state and write computed values to the SLE.

		Written to each SLE:
		  - qty_after_transaction: per THIS dimension combination (e.g., per Fresh or Used)
		  - stock_queue, stock_value, valuation_rate: shared across the valuation bucket
		  - stock_value_difference: how much this SLE changed the total stock value
		"""
		total_qty, total_value = valuator.get_total_stock_and_value()
		self.stock_queue = valuator.state

		# stock_value_difference = new total value - previous total value
		stock_value_diff = total_value - self.stock_value

		# Update running state for next SLE
		self.stock_value = total_value
		self.valuation_rate = (total_value / total_qty) if total_qty else 0.0

		# Write computed values back to the SLE document
		frappe.db.set_value(
			"Stock Ledger Entry",
			sle.name,
			{
				"qty_after_transaction": self.qty_by_dims[dim_key],
				"stock_queue": json.dumps(self.stock_queue),
				"stock_value": self.stock_value,
				"valuation_rate": self.valuation_rate,
				"stock_value_difference": stock_value_diff,
				"posting_datetime": get_combine_datetime(sle.posting_date, sle.posting_time),
			},
			update_modified=False,
		)


# ======================================================================
# Reposting — background reprocessing of historical SLEs
# ======================================================================
def repost_future_sle(repost_doc):
	"""Recompute SLE valuations for the bucket described by the Repost Item Valuation doc.

	Called as a background job from repost_item_valuation.py.
	Processes one valuation bucket at a time, commits after each, and supports
	resuming from where it left off if a previous run failed.
	"""
	validate_stock_valuation_period(
		repost_doc.posting_date,
		repost_doc.voucher_type or repost_doc.doctype,
		repost_doc.voucher_no or repost_doc.name,
	)
	dim_fields = get_dimension_fieldnames()
	val_dim_fields = get_valuation_dimensions()

	# Determine which valuation buckets need reprocessing
	if repost_doc.based_on == "Transaction":
		# Find all distinct (item, warehouse, *valuation_dims) touched by this voucher
		buckets = get_items_to_be_repost(repost_doc.voucher_type, repost_doc.voucher_no, val_dim_fields)
	else:
		# Single bucket specified directly on the repost doc
		bucket = {"item": repost_doc.item, "warehouse": repost_doc.warehouse}
		for fn in dim_fields:
			bucket[fn] = repost_doc.get(fn)
		buckets = [bucket]

	repost_doc.db_set("total_reposting_count", len(buckets))

	# Resume from last completed bucket if retrying after a failure
	start_index = cint(repost_doc.current_index) or 0

	for idx, bucket in enumerate(buckets):
		if idx < start_index:
			continue

		# Build args for the valuation engine
		args = {
			"item": bucket["item"],
			"warehouse": bucket["warehouse"],
			"posting_date": repost_doc.posting_date,
			"posting_time": repost_doc.posting_time or "00:00",
			"voucher_type": repost_doc.voucher_type,
			"voucher_no": repost_doc.voucher_no,
		}
		# Include ALL dimension values for qty tracking
		for fn in dim_fields:
			args[fn] = bucket.get(fn)
		args["allow_zero_rate"] = repost_doc.allow_zero_rate

		# Run the engine for this bucket
		from yrp.yrp_stock.doctype.stock_valuation_closing.stock_valuation_closing import (
			lock_stock_valuation_period,
		)

		lock_stock_valuation_period(shared=True)
		validate_stock_valuation_period(
			repost_doc.posting_date,
			repost_doc.voucher_type or repost_doc.doctype,
			repost_doc.voucher_no or repost_doc.name,
		)
		lock_valuation_bucket(bucket)
		UpdateEntriesAfter(args, allow_negative_stock=repost_doc.allow_negative_stock).run()

		# Refresh ALL Bins in this valuation bucket (e.g., both Fresh and Used Bins for LOT-001)
		_update_bins_in_valuation_bucket(bucket, val_dim_fields, dim_fields)

		# Commit after each bucket so progress is saved
		repost_doc.db_set("current_index", idx + 1)
		frappe.db.commit()


def _update_bins_in_valuation_bucket(bucket, val_dim_fields, dim_fields):
	"""After reposting, update every Bin that shares this valuation bucket.

	Example: After reposting LOT-001, update both:
	  - Bin(item, warehouse, lot=LOT-001, received_type=Fresh)
	  - Bin(item, warehouse, lot=LOT-001, received_type=Used)
	"""
	from yrp.yrp_stock.doctype.bin.bin import update_qty as update_bin_qty

	# Find all Bins matching the valuation dimensions
	bin_filters = {"item_code": bucket["item"], "warehouse": bucket["warehouse"]}
	for fn in val_dim_fields:
		bin_filters[fn] = bucket.get(fn)

	bin_names = frappe.get_all("Bin", filters=bin_filters, pluck="name")
	for bin_name in bin_names:
		update_bin_qty(bin_name, bucket)


def get_items_to_be_repost(voucher_type, voucher_no, val_dim_fields=None):
	"""Find distinct valuation buckets touched by a voucher.

	Uses valuation dims only, so:
	  (item=X, warehouse=Y, lot=L1, received_type=Fresh)
	  (item=X, warehouse=Y, lot=L1, received_type=Used)
	collapse into one bucket: (item=X, warehouse=Y, lot=L1)
	"""
	if val_dim_fields is None:
		val_dim_fields = get_valuation_dimensions()

	fields = ["item", "warehouse"] + val_dim_fields
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_type": voucher_type, "voucher_no": voucher_no, "is_cancelled": 0},
		fields=fields,
		distinct=True,
	)
