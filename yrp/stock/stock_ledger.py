"""Dimension-aware stock ledger engine for YRP.

How valuation and quantity tracking work together:
--------------------------------------------------
Stock dimensions can be marked as "in_valuation" (affects cost) or not (tracking only).

Example configuration:
  - Lot:           in_valuation = True   (each lot has its own cost)
  - Received Type: in_valuation = False  (just a label, doesn't affect cost)

This means:
  - The FIFO queue and valuation_rate are SHARED per (item, warehouse, lot).
    Lot-1/Fresh and Lot-1/Used share the same FIFO queue and cost per unit.

  - The qty_after_transaction is tracked SEPARATELY per (item, warehouse, lot, received_type).
    Lot-1/Fresh = 80 pieces, Lot-1/Used = 50 pieces — each has its own balance.

The stock_queue stored on each SLE is a JSON array of [qty, rate] pairs:
  e.g. [[100, 50.0], [50, 45.0]] means 100 units at 50 and 50 units at 45.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from yrp.stock.dimensions import get_dimension_fieldnames, get_valuation_dimensions
from yrp.stock.utils import (
	future_sle_exists,
	get_combine_datetime,
	get_incoming_outgoing_rate_for_cancel,
	get_or_make_bin,
)
from yrp.stock.valuation import FIFOValuation, MovingAverageValuation, round_off_if_near_zero


class NegativeStockError(frappe.ValidationError):
	pass


# ======================================================================
# make_sl_entries — called by Stock Entry, Stock Update, Stock
# Reconciliation controllers on submit and cancel
# ======================================================================
def make_sl_entries(sl_entries, cancel=False, allow_negative_stock=False):
	"""Create Stock Ledger Entries and recompute valuation.

	For each SLE dict in the list:
	  1. Create the SLE document
	  2. Get or create the Bin for this (item, warehouse, *dimensions)
	  3. Run the valuation engine to recompute forward
	  4. Update the Bin with fresh qty and rate
	"""
	if not sl_entries:
		return

	# When cancelling, first mark all existing SLEs for this voucher as cancelled
	if cancel:
		_set_voucher_cancelled(sl_entries[0])

	dim_fields = get_dimension_fieldnames()

	for sle in sl_entries:
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
		args = sle_doc.as_dict()
		args["posting_datetime"] = get_combine_datetime(args.posting_date, args.posting_time)

		# Step 2: Get or create Bin for this item + warehouse + dimensions
		dimension_values = {fn: args.get(fn) for fn in dim_fields}
		bin_name = get_or_make_bin(args["item"], args["warehouse"], **dimension_values)
		args["reserved_stock"] = flt(frappe.db.get_value("Bin", bin_name, "reserved_qty"))

		# Step 3: Recompute valuation from this SLE forward
		repost_current_voucher(args, allow_negative_stock=allow_negative_stock)

		# Step 4: Refresh the Bin with updated qty and rate
		from yrp.yrp_stock.doctype.bin.bin import update_qty as update_bin_qty
		update_bin_qty(bin_name, args)


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
def get_previous_sle(args, dim_fields=None):
	"""Find the most recent SLE at or before the given posting_datetime.

	Args:
		args: dict with item, warehouse, posting_datetime, and dimension values
		dim_fields: which dimension fields to filter by.
			- None (default) = ALL dimensions (used by get_stock_balance)
			- explicit list = only those (used by valuation engine for valuation-scoped queries)
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
		query = query.where(sle.posting_datetime <= args["posting_datetime"])

	# Exclude the current voucher (so it doesn't find itself as "previous")
	if args.get("voucher_no"):
		query = query.where(sle.voucher_no != args["voucher_no"])

	# Get the most recent one
	query = query.orderby(sle.posting_datetime, order=frappe.qb.desc)
	query = query.orderby(sle.creation, order=frappe.qb.desc)
	query = query.limit(1)

	rows = query.run(as_dict=True)
	return frappe._dict(rows[0]) if rows else None


# ======================================================================
# UpdateEntriesAfter — the core valuation engine
# ======================================================================
class UpdateEntriesAfter:
	"""Recompute valuation for a (item, warehouse, *valuation_dims) bucket.

	Example with Lot (in_valuation=True) and Received Type (in_valuation=False):

	  Config:
	    Item = "T-Shirt Blue", Warehouse = "WH-1", Lot = "LOT-001"
	    SLEs exist for received_type = "Fresh" and "Used"

	  What this engine does:
	    1. Fetches ALL SLEs for (T-Shirt Blue, WH-1, LOT-001) — both Fresh and Used
	    2. Processes them in chronological order through one shared FIFO queue
	    3. Writes back:
	       - valuation_rate = shared rate (same for Fresh and Used within LOT-001)
	       - qty_after_transaction = per (Fresh) or per (Used) running balance
	"""

	def __init__(self, args, allow_negative_stock=False):
		self.args = frappe._dict(args)

		# All dimensions (e.g., ["lot", "received_type"]) — for qty tracking
		self.dim_fields = get_dimension_fieldnames()

		# Only valuation dimensions (e.g., ["lot"]) — for FIFO queue scoping
		self.valuation_dim_fields = get_valuation_dimensions()

		self.valuation_method = (
			frappe.db.get_single_value("YRP Stock Settings", "default_valuation_method") or "FIFO"
		)
		self.allow_negative_stock = allow_negative_stock or frappe.db.get_single_value(
			"YRP Stock Settings", "allow_negative_stock"
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
		"""Load the running qty for each (lot, received_type) combination
		from SLEs that occurred BEFORE our processing window.

		This tells us: "Before we start reprocessing, how much stock exists
		per each full-dimension combination?"

		Example result in self.qty_by_dims:
		  {("LOT-001", "Fresh"): 100.0, ("LOT-001", "Used"): 50.0}
		"""
		if not self.dim_fields:
			return

		sle = frappe.qb.DocType("Stock Ledger Entry")
		from frappe.query_builder.functions import Sum

		posting_dt = get_combine_datetime(self.args.posting_date, self.args.posting_time)

		# We need: SUM(qty) grouped by each dimension combination
		# SELECT lot, received_type, SUM(qty) as total_qty
		# FROM `tabStock Ledger Entry`
		# WHERE item=X AND warehouse=Y AND lot=L1 AND posting_datetime < start
		# GROUP BY lot, received_type

		select_fields = [Sum(sle.qty).as_("total_qty")]
		group_fields = []
		for fn in self.dim_fields:
			select_fields.append(sle[fn])
			group_fields.append(sle[fn])

		query = (
			frappe.qb.from_(sle)
			.where(sle.item == self.args.item)
			.where(sle.warehouse == self.args.warehouse)
			.where(sle.is_cancelled == 0)
			.where(sle.posting_datetime < posting_dt)
		)

		# Stay within our valuation bucket (filter by lot, not received_type)
		for fn in self.valuation_dim_fields:
			val = self.args.get(fn)
			if val is not None:
				query = query.where(sle[fn] == val)

		for field in select_fields:
			query = query.select(field)
		for field in group_fields:
			query = query.groupby(field)

		rows = query.run(as_dict=True)
		for row in rows:
			key = self._dim_key(row)
			self.qty_by_dims[key] = flt(row.total_qty)

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
		"""Handle Stock Reconciliation — snaps qty to the target value.

		Reconciliation works by setting qty_after_transaction directly.
		The diff between target and current qty is added/removed from the FIFO queue.
		"""
		target_qty = flt(sle.qty_after_transaction)
		diff = target_qty - current_dim_qty

		if diff > 0:
			# Need more stock — add the difference at the reconciled rate
			valuator.add_stock(diff, flt(sle.rate))
		elif diff < 0:
			# Have excess stock — remove the difference
			valuator.remove_stock(abs(diff), flt(sle.outgoing_rate))
		else:
			# Qty is correct but rate may need correction
			# Reset the queue to the reconciled rate
			if flt(sle.rate) and target_qty > 0:
				valuator.queue.clear()
				valuator.queue.append([target_qty, flt(sle.rate)])

		return target_qty

	def _handle_incoming(self, sle, valuator, current_dim_qty):
		"""Handle incoming stock (qty > 0) — add to FIFO queue."""
		if not self.allow_zero_rate and not flt(sle.rate):
			frappe.throw(
				_("Valuation rate is zero for incoming stock of {0} at {1}").format(
					sle.item, sle.warehouse
				)
			)

		valuator.add_stock(flt(sle.qty), flt(sle.rate))
		return current_dim_qty + flt(sle.qty)

	def _handle_outgoing(self, sle, valuator, current_dim_qty):
		"""Handle outgoing stock (qty < 0) — remove from FIFO queue."""
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
