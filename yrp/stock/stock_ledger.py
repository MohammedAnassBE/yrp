"""Dimension-aware stock ledger engine for YRP.

Adapted from production_api/mrp_stock/stock_ledger.py and trimmed to the core
paths used by Stock Entry, Stock Update, Stock Reconciliation, Stock
Reservation Entry. All grouping that referenced ``lot``/``received_type`` has
been replaced with the dimension list returned by
``yrp.stock.dimensions.get_dimension_fieldnames`` so adding a dimension in
YRP Stock Settings flows through every query automatically.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

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


# ----------------------------------------------------------------------
# make_sl_entries — main entry from doctype controllers
# ----------------------------------------------------------------------
def make_sl_entries(sl_entries, cancel=False, allow_negative_stock=False):
	if not sl_entries:
		return

	if cancel:
		_set_voucher_cancelled(sl_entries[0])

	dim_fields = get_dimension_fieldnames()

	for sle in sl_entries:
		item_variant = sle.get("item")
		if not item_variant:
			continue
		parent_item = frappe.get_cached_value("Item Variant", item_variant, "item")
		if not frappe.get_cached_value("Item", parent_item, "is_stock_item"):
			continue

		if cancel:
			sle["is_cancelled"] = 1
			if sle.get("qty") and sle["qty"] < 0 and not sle.get("outgoing_rate"):
				sle["outgoing_rate"] = get_incoming_outgoing_rate_for_cancel(
					sle["item"], sle["voucher_type"], sle["voucher_no"], sle.get("voucher_detail_no")
				)

		if not (sle.get("qty") or sle.get("voucher_type") == "Stock Reconciliation"):
			continue

		sle_doc = make_entry(sle)
		args = sle_doc.as_dict()
		args["posting_datetime"] = get_combine_datetime(args.posting_date, args.posting_time)

		bin_kwargs = {fn: args.get(fn) for fn in dim_fields}
		bin_name = get_or_make_bin(args["item"], args["warehouse"], **bin_kwargs)
		args["reserved_stock"] = flt(frappe.db.get_value("Bin", bin_name, "reserved_qty"))

		repost_current_voucher(args, allow_negative_stock=allow_negative_stock)
		from yrp.yrp_stock.doctype.bin.bin import update_qty as update_bin_qty

		update_bin_qty(bin_name, args)


def _set_voucher_cancelled(sl_entry):
	frappe.db.sql(
		"UPDATE `tabStock Ledger Entry` SET is_cancelled=1, modified=%s, modified_by=%s "
		"WHERE voucher_type=%s AND voucher_no=%s AND is_cancelled=0",
		(frappe.utils.now(), frappe.session.user, sl_entry["voucher_type"], sl_entry["voucher_no"]),
	)


def make_entry(args):
	doc = frappe.new_doc("Stock Ledger Entry")
	for key, value in args.items():
		if hasattr(doc, key):
			doc.set(key, value)
	doc.flags.ignore_permissions = 1
	doc.submit()
	return doc


# ----------------------------------------------------------------------
# repost_current_voucher — runs the engine for the current voucher only
# ----------------------------------------------------------------------
def repost_current_voucher(args, allow_negative_stock=False):
	if not (args.get("qty") or args.get("voucher_type") == "Stock Reconciliation"):
		return
	if not args.get("posting_date"):
		args["posting_date"] = nowdate()

	UpdateEntriesAfter(
		{
			"item": args.get("item"),
			"warehouse": args.get("warehouse"),
			**{fn: args.get(fn) for fn in get_dimension_fieldnames()},
			"posting_date": args.get("posting_date"),
			"posting_time": args.get("posting_time"),
			"voucher_type": args.get("voucher_type"),
			"voucher_no": args.get("voucher_no"),
			"sle_id": args.get("name"),
			"creation": args.get("creation"),
			"reserved_stock": args.get("reserved_stock"),
		},
		allow_negative_stock=allow_negative_stock,
	).run()


# ----------------------------------------------------------------------
# get_previous_sle — used by stock balance and engine init
# ----------------------------------------------------------------------
def get_previous_sle(args):
	"""Most-recent SLE for matching (item, warehouse, *dims) at or before the
	given posting_datetime. Used by ``get_stock_balance``."""
	dim_fields = get_dimension_fieldnames()
	sle = frappe.qb.DocType("Stock Ledger Entry")

	q = (
		frappe.qb.from_(sle)
		.select(sle.star)
		.where(sle.item == args.get("item"))
		.where(sle.warehouse == args.get("warehouse"))
		.where(sle.is_cancelled == 0)
	)
	for fn in dim_fields:
		val = args.get(fn)
		if val is not None:
			q = q.where(sle[fn] == val)
	if args.get("posting_datetime"):
		q = q.where(sle.posting_datetime <= args["posting_datetime"])
	if args.get("voucher_no"):
		q = q.where(sle.voucher_no != args["voucher_no"])

	q = q.orderby(sle.posting_datetime, order=frappe.qb.desc).orderby(sle.creation, order=frappe.qb.desc).limit(1)
	rows = q.run(as_dict=True)
	return frappe._dict(rows[0]) if rows else None


# ----------------------------------------------------------------------
# UpdateEntriesAfter — process SLEs forward, recompute valuation
# ----------------------------------------------------------------------
class UpdateEntriesAfter:
	"""Recompute qty_after_transaction, valuation_rate, stock_value, stock_queue
	for the given SLE and every SLE that comes after it for the same
	(item, warehouse, *dims) bucket."""

	def __init__(self, args, allow_negative_stock=False):
		self.args = frappe._dict(args)
		self.dim_fields = get_dimension_fieldnames()
		self.valuation_dim_fields = get_valuation_dimensions()
		self.valuation_method = (
			frappe.db.get_single_value("YRP Stock Settings", "default_valuation_method") or "FIFO"
		)
		self.allow_negative_stock = allow_negative_stock or frappe.db.get_single_value(
			"YRP Stock Settings", "allow_negative_stock"
		)
		self.previous_sle = None
		self.qty_after_transaction = 0.0
		self.stock_queue = []
		self.stock_value = 0.0
		self.valuation_rate = 0.0

	def run(self):
		self._init_previous()
		entries = self._get_entries_to_process()
		for sle in entries:
			self._process_sle(sle)

	# ----- previous state ------------------------------------------------
	def _init_previous(self):
		prev = get_previous_sle({**self.args, "posting_datetime": get_combine_datetime(self.args.posting_date, self.args.posting_time)})
		if prev:
			self.previous_sle = prev
			self.qty_after_transaction = flt(prev.qty_after_transaction)
			try:
				self.stock_queue = json.loads(prev.stock_queue or "[]")
			except Exception:
				self.stock_queue = []
			self.stock_value = flt(prev.stock_value)
			self.valuation_rate = flt(prev.valuation_rate)

	# ----- forward sweep -------------------------------------------------
	def _get_entries_to_process(self):
		sle = frappe.qb.DocType("Stock Ledger Entry")
		q = (
			frappe.qb.from_(sle)
			.select(sle.star)
			.where(sle.item == self.args.item)
			.where(sle.warehouse == self.args.warehouse)
			.where(sle.is_cancelled == 0)
		)
		for fn in self.dim_fields:
			val = self.args.get(fn)
			if val is not None:
				q = q.where(sle[fn] == val)
		posting_dt = get_combine_datetime(self.args.posting_date, self.args.posting_time)
		q = q.where(sle.posting_datetime >= posting_dt)
		q = q.orderby(sle.posting_datetime).orderby(sle.creation)
		return [frappe._dict(r) for r in q.run(as_dict=True)]

	# ----- single SLE ----------------------------------------------------
	def _process_sle(self, sle):
		valuation_cls = MovingAverageValuation if self.valuation_method == "Moving Average" else FIFOValuation
		valuator = valuation_cls(self.stock_queue or None)

		if sle.voucher_type == "Stock Reconciliation":
			# Set qty absolutely; derive qty diff
			target_qty = flt(sle.qty_after_transaction)
			diff = target_qty - self.qty_after_transaction
			if diff > 0:
				valuator.add_stock(diff, flt(sle.rate))
			elif diff < 0:
				valuator.remove_stock(abs(diff), flt(sle.outgoing_rate))
			self.qty_after_transaction = target_qty
		elif sle.qty > 0:
			valuator.add_stock(flt(sle.qty), flt(sle.rate))
			self.qty_after_transaction += flt(sle.qty)
		else:
			if not self.allow_negative_stock and (self.qty_after_transaction + flt(sle.qty)) < 0:
				frappe.throw(
					_("Insufficient stock for {0} at {1}: balance {2}, requested {3}").format(
						sle.item, sle.warehouse, self.qty_after_transaction, abs(sle.qty)
					),
					exc=NegativeStockError,
				)
			valuator.remove_stock(abs(flt(sle.qty)), flt(sle.outgoing_rate))
			self.qty_after_transaction += flt(sle.qty)

		total_qty, total_value = valuator.get_total_stock_and_value()
		self.stock_queue = valuator.state
		self.stock_value = total_value
		self.valuation_rate = (total_value / total_qty) if total_qty else 0.0
		stock_value_diff = total_value - flt(sle.stock_value or 0)

		frappe.db.set_value(
			"Stock Ledger Entry",
			sle.name,
			{
				"qty_after_transaction": self.qty_after_transaction,
				"stock_queue": json.dumps(self.stock_queue),
				"stock_value": self.stock_value,
				"valuation_rate": self.valuation_rate,
				"stock_value_difference": stock_value_diff,
				"posting_datetime": get_combine_datetime(sle.posting_date, sle.posting_time),
			},
			update_modified=False,
		)


# ----------------------------------------------------------------------
# Reposting future SLEs (called by Repost Item Valuation)
# ----------------------------------------------------------------------
def repost_future_sle(repost_doc):
	"""Recompute future SLE valuations for the bucket described by the given
	Repost Item Valuation document."""
	dim_fields = get_dimension_fieldnames()
	if repost_doc.based_on == "Transaction":
		buckets = get_items_to_be_repost(repost_doc.voucher_type, repost_doc.voucher_no)
	else:
		bucket = {"item": repost_doc.item, "warehouse": repost_doc.warehouse}
		for fn in dim_fields:
			bucket[fn] = repost_doc.get(fn)
		buckets = [bucket]

	repost_doc.db_set("total_reposting_count", len(buckets))

	for idx, bucket in enumerate(buckets):
		args = {
			"item": bucket["item"],
			"warehouse": bucket["warehouse"],
			"posting_date": repost_doc.posting_date,
			"posting_time": repost_doc.posting_time or "00:00",
			"voucher_type": repost_doc.voucher_type,
			"voucher_no": repost_doc.voucher_no,
		}
		for fn in dim_fields:
			args[fn] = bucket.get(fn)
		UpdateEntriesAfter(args, allow_negative_stock=repost_doc.allow_negative_stock).run()

		# Refresh Bin for this bucket
		from yrp.yrp_stock.doctype.bin.bin import update_qty as update_bin_qty

		bin_kwargs = {fn: bucket.get(fn) for fn in dim_fields}
		bin_name = get_or_make_bin(bucket["item"], bucket["warehouse"], **bin_kwargs)
		update_bin_qty(bin_name, args)

		repost_doc.db_set("current_index", idx + 1)


def get_items_to_be_repost(voucher_type, voucher_no):
	"""Distinct (item, warehouse, *dims) buckets touched by the given voucher."""
	dim_fields = get_dimension_fieldnames()
	fields = ["item", "warehouse"] + dim_fields
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_type": voucher_type, "voucher_no": voucher_no},
		fields=fields,
		distinct=True,
	)
