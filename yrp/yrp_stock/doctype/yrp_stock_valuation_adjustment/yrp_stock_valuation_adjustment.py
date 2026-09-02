"""Auditable, restart-safe propagation of late stock-cost differences.

The source voucher creates one submitted Stock Valuation Adjustment.  The
worker applies a signed value overlay to an exact incoming SLE, replays only
that valuation bucket, and follows persisted transfer/production links when an
outgoing SLE's value changes.  Physical quantities and original incoming rates
are never rewritten by this module.
"""

from __future__ import annotations

import hashlib
import json
import time
import traceback

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, flt, getdate, now_datetime, nowdate

from yrp.stock.dimensions import get_dimension_fieldnames, get_valuation_dimensions


VALUE_TOLERANCE = 0.00001
CONSERVATION_TOLERANCE = 0.01
DEFAULT_CHUNK_SIZE = 25
DEFAULT_CHUNK_SECONDS = 45
MAX_AUTOMATIC_RETRIES = 3
ADJUSTMENT_JOB_TIMEOUT = 25 * 60
ACTIVE_STATUSES = (
	"Queued",
	"Calculating",
	"Applying",
	"Calculation Failed",
	"Apply Failed",
)


class YRPStockValuationAdjustment(Document):
	def validate(self):
		if not self.get("source_allocations"):
			frappe.throw(_("At least one source allocation is required."))
		self.total_source_difference = flt(
			sum(flt(row.difference) for row in self.source_allocations), 6
		)
		if abs(self.total_source_difference) <= VALUE_TOLERANCE:
			frappe.throw(_("The stock valuation difference is zero."))
		if not self.idempotency_key:
			self.idempotency_key = _hash_key(
				self.adjustment_type,
				self.source_doctype,
				self.source_name,
				self.source_revision or 1,
			)
		self.status = self.status or "Draft"

	def before_submit(self):
		self.status = "Queued"
		self.queued_at = now_datetime()
		self.progress = 0
		self.error_log = None

	def on_submit(self):
		if not self.flags.get("skip_valuation_enqueue"):
			enqueue_adjustment(self.name)

	def before_cancel(self):
		frappe.throw(
			_(
				"Stock Valuation Adjustments are immutable audit records. Cancel the source document to create a signed reversal."
			)
		)


def _hash_key(*parts):
	raw = "|".join(str(part or "") for part in parts)
	return hashlib.sha256(raw.encode()).hexdigest()


def is_stock_adjustment_enabled():
	"""Return the YRP Settings gate for creating new valuation adjustments."""
	field = frappe.get_meta('YRP YRP Settings').get_field("apply_stock_adjustment")
	if not field:
		# Preserve existing behavior during a rolling deploy until the DocType is
		# synchronized and the new checkbox is available on the site.
		return True
	value = frappe.db.get_single_value('YRP YRP Settings', "apply_stock_adjustment")
	return bool(cint(value))


def create_adjustment(
	*,
	adjustment_type,
	source_doctype,
	source_name,
	effective_date,
	allocations,
	idempotency_key=None,
	source_revision=1,
	reversal_of=None,
	enqueue=True,
):
	"""Create one submitted adjustment, returning its name.

	The idempotency key makes repeated source hooks harmless.  Zero-difference
	allocations are discarded; when every row is zero no document is created.
	"""
	all_rows = [frappe._dict(row) for row in (allocations or [])]
	rows = [
		row
		for row in all_rows
		if abs(flt(row.get("difference"))) > VALUE_TOLERANCE
	]
	if not rows:
		# A zero-value revision still participates in source serialization. For
		# example, a corrected PI at the original rate must not submit while the
		# cancelled PI's signed reversal is still pending. It creates no SVA, but
		# it must wait for every earlier revision owning the same receipt SLE.
		target_sles = sorted(
			{row.get("target_sle") for row in all_rows if row.get("target_sle")}
		)
		if target_sles:
			_lock_target_sles(target_sles)
			_lock_adjustment_creation()
			_validate_no_conflicting_adjustments(
				target_sles,
				exclude_adjustments=[reversal_of] if reversal_of else None,
			)
		return None
	idempotency_key = idempotency_key or _hash_key(
		adjustment_type,
		source_doctype,
		source_name,
		source_revision,
		reversal_of,
	)
	existing = frappe.db.get_value(
		'YRP Stock Valuation Adjustment', {"idempotency_key": idempotency_key}, "name"
	)
	if existing:
		return existing
	# The setting gates only new originals. Existing work and signed reversals
	# must always finish so an applied value can never be stranded.
	if not reversal_of and not is_stock_adjustment_enabled():
		return None

	from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
		lock_stock_valuation_period,
	)

	# Share the same gate used by Stock Valuation Closing. Source submission,
	# cancellation, and WO close therefore cannot slip an adjustment behind a
	# closing snapshot that was taken concurrently.
	lock_stock_valuation_period(shared=True)
	target_sles = sorted({row.get("target_sle") for row in rows if row.get("target_sle")})
	if len(target_sles) != len({row.get("target_sle") for row in rows}):
		frappe.throw(_("Every source allocation requires an affected receipt SLE."))
	_lock_target_sles(target_sles)
	_lock_adjustment_creation()
	# The current-read conflict query also recognizes an identical winner that
	# committed while this transaction waited for the receipt. This avoids a
	# missing-key next-gap lock on the global idempotency index.
	winner = _validate_no_conflicting_adjustments(
		target_sles,
		exclude_adjustments=[reversal_of] if reversal_of else None,
		idempotency_key=idempotency_key,
	)
	if winner:
		return winner

	# The adjustment becomes relevant at the first stock receipt it can change,
	# not merely on the later invoice/closing document date. This date is used by
	# Stock Valuation Closing to prevent closing a period while historical stock
	# in that period still has an unfinished propagation job.
	target_dates = []
	for target_sle in target_sles:
		target_dates.append(
			getdate(_validate_target_sle(target_sle, for_update=True).posting_date)
		)
	requested_date = getdate(effective_date) if effective_date else getdate(nowdate())
	effective_date = min([requested_date, *target_dates])

	doc = frappe.get_doc(
		{
			"doctype": 'YRP Stock Valuation Adjustment',
			"adjustment_type": adjustment_type,
			"source_doctype": source_doctype,
			"source_name": source_name,
			"source_revision": source_revision,
			"effective_date": effective_date,
			"posting_date": nowdate(),
			"idempotency_key": idempotency_key,
			"reversal_of": reversal_of,
		}
	)
	for row in rows:
		doc.append("source_allocations", row)
	doc.flags.ignore_permissions = True
	# A cancellation reversal intentionally audits the now-cancelled source
	# voucher. Frappe otherwise rejects that Dynamic Link during insert.
	doc.flags.ignore_links = bool(reversal_of)
	doc.flags.skip_valuation_enqueue = not enqueue
	try:
		doc.insert()
	except frappe.UniqueValidationError:
		# A malformed caller may reuse an idempotency key across disjoint target
		# sets, so target serialization alone cannot order those inserts. The
		# unique constraint decides the winner; return it with a current read.
		existing = frappe.db.sql(
			"""
			SELECT name
			FROM `tabYRP Stock Valuation Adjustment`
			WHERE idempotency_key=%s
			LIMIT 1
			FOR UPDATE
			""",
			(idempotency_key,),
		)
		if existing:
			return existing[0][0]
		raise
	doc.submit()
	for target_sle in target_sles:
		frappe.db.set_value(
			'YRP Stock Ledger Entry',
			target_sle,
			"valuation_is_stale",
			1,
			update_modified=False,
		)
	return doc.name


def _lock_target_sles(target_sles):
	for target_sle in sorted(set(target_sles or [])):
		frappe.db.sql(
			"SELECT name FROM `tabYRP Stock Ledger Entry` WHERE name=%s FOR UPDATE",
			(target_sle,),
		)


def _lock_adjustment_creation():
	"""Serialize the short source-conflict/insert phase without gap deadlocks."""
	frappe.db.sql(
		"""
		SELECT name
		FROM `tabDocType`
		WHERE name='YRP Stock Valuation Adjustment'
		FOR UPDATE
		"""
	)


def _validate_no_conflicting_adjustments(
	target_sles, exclude_adjustments=None, idempotency_key=None
):
	if not target_sles:
		return
	excluded = tuple(name for name in (exclude_adjustments or []) if name)
	exclude_sql = ""
	params = {"target_sles": tuple(target_sles)}
	if excluded:
		exclude_sql = "AND adjustment.name NOT IN %(excluded)s"
		params["excluded"] = excluded
	rows = frappe.db.sql(
		f"""
		SELECT DISTINCT adjustment.name, adjustment.status, adjustment.idempotency_key
		FROM `tabYRP Stock Valuation Adjustment Source` source
		INNER JOIN `tabYRP Stock Valuation Adjustment` adjustment
			ON adjustment.name = source.parent
		WHERE source.target_sle IN %(target_sles)s
		  AND adjustment.docstatus = 1
		  AND adjustment.status NOT IN ('Completed', 'Reversed')
		  {exclude_sql}
		ORDER BY adjustment.creation, adjustment.name
		LIMIT 1
		FOR UPDATE
		""",
		params,
		as_dict=True,
	)
	if rows:
		if idempotency_key and rows[0].idempotency_key == idempotency_key:
			return rows[0].name
		frappe.throw(
			_(
				"Stock Valuation Adjustment {0} is still {1} for an affected receipt. "
				"Wait for it and any reversal to complete before creating the next valuation revision."
			).format(rows[0].name, rows[0].status),
			title=_("Previous Valuation Update Is Not Complete"),
		)
	return None


def create_purchase_invoice_adjustment(invoice):
	"""Allocate a PI's final-rate differences to its exact GRN receipt SLEs."""
	from yrp.yrp.doctype.yrp_purchase_invoice.yrp_purchase_invoice import (
		_get_po_material_rate,
		_get_wo_process_cost,
		_normal_json,
	)

	grn_names = list(
		dict.fromkeys(row.grn for row in (invoice.get("grn") or []) if row.grn)
	)
	if not grn_names:
		return None

	work_orders = {}
	candidates = []
	for grn_name in grn_names:
		grn = frappe.get_doc('YRP Goods Received Note', grn_name)
		work_order = None
		if grn.against == 'YRP Work Order':
			work_order = work_orders.setdefault(
				grn.against_id, frappe.get_doc('YRP Work Order', grn.against_id)
			)
		for grn_item in grn.get("items") or []:
			quantity = flt(grn_item.quantity)
			stock_quantity = flt(grn_item.stock_qty) or quantity
			if quantity <= 0 or stock_quantity <= 0:
				continue
			if work_order:
				stock_process_rate = _get_wo_process_cost(
					work_order,
					grn_item.item_variant,
					_normal_json(grn_item.get("set_combination")),
				)
				billing_rate = (
					flt(stock_process_rate) * stock_quantity / quantity
					if stock_process_rate is not None
					else flt(grn_item.amount) / quantity
				)
			else:
				billing_rate = _get_po_material_rate(grn, grn_item)
			target_sle = find_receipt_sle(grn.doctype, grn.name, grn_item.name)
			if not target_sle:
				frappe.throw(
					_("No active receipt SLE was found for GRN row {0}.").format(
						grn_item.name
					)
				)
			candidates.append(
				frappe._dict(
					grn=grn.name,
					grn_item=grn_item.name,
					item=grn_item.item_variant,
					uom=grn_item.uom,
					set_combination=_normal_json(grn_item.get("set_combination")),
					quantity=quantity,
					remaining_quantity=quantity,
					billing_rate=flt(billing_rate, 6),
					target_sle=target_sle,
				)
			)

	allocations = []
	for invoice_item in invoice.get("items") or []:
		requested = flt(invoice_item.qty)
		if requested <= 0:
			continue
		set_combination = _normal_json(invoice_item.get("set_combination"))
		matching = [
			row
			for row in candidates
			if row.item == invoice_item.item
			and row.uom == invoice_item.uom
			and row.set_combination == set_combination
			and flt(row.remaining_quantity) > VALUE_TOLERANCE
		]
		if not matching:
			frappe.throw(
				_("Purchase Invoice row {0} cannot be mapped to a selected GRN row.").format(
					invoice_item.idx
				)
			)

		source_rate = invoice_item.get("source_rate")
		if source_rate is None or source_rate == "":
			distinct_rates = {
				round(flt(row.billing_rate), 6) for row in matching
			}
			if len(distinct_rates) != 1:
				frappe.throw(
					_(
						"Purchase Invoice row {0} combines GRNs with different original rates. Fetch the GRNs again."
					).format(invoice_item.idx)
				)
			source_rate = distinct_rates.pop()
		source_rate = flt(source_rate, 6)
		matching = [
			row
			for row in matching
			if abs(flt(row.billing_rate) - source_rate) <= VALUE_TOLERANCE
		]
		remaining = requested
		for candidate in matching:
			if remaining <= VALUE_TOLERANCE:
				break
			take = min(remaining, flt(candidate.remaining_quantity))
			difference = take * (flt(invoice_item.rate) - source_rate)
			target = _get_sle(candidate.target_sle)
			allocations.append(
				{
					"source_row": invoice_item.name,
					"source_grn": candidate.grn,
					"source_grn_item": candidate.grn_item,
					"target_sle": candidate.target_sle,
					"item": candidate.item,
					"quantity": take,
					"old_rate": source_rate,
					"new_rate": flt(invoice_item.rate),
					"difference": flt(difference, 6),
					"allocation_weight": take,
					"stock_dimensions": frappe.as_json(
						{
							fieldname: target.get(fieldname)
							for fieldname in get_dimension_fieldnames()
						}
					),
				}
			)
			candidate.remaining_quantity -= take
			remaining -= take
		if remaining > VALUE_TOLERANCE:
			frappe.throw(
				_(
					"Purchase Invoice row {0} has {1} quantity, but only {2} can be allocated to selected GRNs at the original rate."
				).format(invoice_item.idx, requested, requested - remaining)
			)

	return create_adjustment(
		adjustment_type="Purchase Invoice Rate Difference",
		source_doctype=invoice.doctype,
		source_name=invoice.name,
		effective_date=invoice.posting_date,
		allocations=allocations,
		idempotency_key=_hash_key(invoice.doctype, invoice.name, "submit", 1),
	)


def validate_reversal_allowed(source_doctype, source_name):
	"""Fail before a source voucher mutates if its value cannot be reversed."""
	target_sles = frappe.db.sql(
		"""
		SELECT DISTINCT source.target_sle
		FROM `tabYRP Stock Valuation Adjustment Source` source
		INNER JOIN `tabYRP Stock Valuation Adjustment` adjustment
			ON adjustment.name = source.parent
		WHERE adjustment.source_doctype = %s
		  AND adjustment.source_name = %s
		  AND adjustment.docstatus = 1
		  AND adjustment.adjustment_type != 'Reversal'
		  AND adjustment.status != 'Reversed'
		  AND COALESCE(source.target_sle, '') != ''
		ORDER BY source.target_sle
		""",
		(source_doctype, source_name),
		pluck=True,
	)
	if not target_sles:
		return
	from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
		lock_stock_valuation_period,
	)

	lock_stock_valuation_period(shared=True)
	for target_sle in target_sles:
		_validate_target_sle(target_sle)


def create_reversal(source_doctype, source_name, source_event="cancel"):
	"""Create a signed reversal for active adjustments owned by a voucher."""
	validate_reversal_allowed(source_doctype, source_name)
	adjustments = frappe.get_all(
		'YRP Stock Valuation Adjustment',
		filters={
			"source_doctype": source_doctype,
			"source_name": source_name,
			"docstatus": 1,
			"adjustment_type": ["!=", "Reversal"],
		},
		fields=["name", "effective_date", "source_revision"],
		order_by="creation asc",
	)
	created = []
	for adjustment in adjustments:
		rows = frappe.get_all(
			'YRP Stock Valuation Adjustment Source',
			filters={"parent": adjustment.name, "parenttype": 'YRP Stock Valuation Adjustment'},
			fields=[
				"source_row",
				"source_grn",
				"source_grn_item",
				"source_sle",
				"target_sle",
				"item",
				"quantity",
				"old_rate",
				"new_rate",
				"difference",
				"allocation_weight",
				"stock_dimensions",
			],
			order_by="idx asc",
		)
		# Apply workers lock bucket/target before updating their parent adjustment.
		# Reversal creation follows the compatible target-before-parent order so a
		# cancellation cannot hold the parent while waiting on the worker's target.
		_lock_target_sles([row.target_sle for row in rows if row.target_sle])
		locked_adjustment = _lock_adjustment(adjustment.name)
		adjustment.status = locked_adjustment.status
		existing = frappe.db.sql(
			"""
			SELECT name
			FROM `tabYRP Stock Valuation Adjustment`
			WHERE reversal_of=%s AND docstatus=1
			ORDER BY creation, name
			LIMIT 1
			FOR UPDATE
			""",
			(adjustment.name,),
		)
		if existing:
			created.append(existing[0][0])
			continue
		for row in rows:
			row["difference"] = -flt(row.difference)
			row["old_rate"], row["new_rate"] = row.new_rate, row.old_rate
		original_is_applied = adjustment.status in {"Completed", "Reversal Queued"}
		name = create_adjustment(
			adjustment_type="Reversal",
			source_doctype=source_doctype,
			source_name=source_name,
			effective_date=adjustment.effective_date,
			allocations=rows,
			source_revision=cint(adjustment.source_revision) + 1,
			reversal_of=adjustment.name,
			idempotency_key=_hash_key(adjustment.name, source_event, "reversal"),
			enqueue=True,
		)
		if name:
			created.append(name)
			if original_is_applied:
				_set_adjustment_values(adjustment.name, status="Reversal Queued")
	return created


def enqueue_adjustment(adjustment, retry=False):
	"""Queue a restart-safe worker only after the caller's transaction commits."""
	row = frappe.db.get_value(
		'YRP Stock Valuation Adjustment',
		adjustment,
		["status", "completed_entries", "retry_count"],
		as_dict=True,
	)
	if not row or row.status in {"Completed", "Reversed"}:
		return
	queue = frappe.conf.get("stock_valuation_queue") or "long"
	job_revision = f"{cint(row.retry_count)}-{cint(row.completed_entries)}"
	if retry:
		job_revision = f"retry-{cint(row.retry_count)}-{cint(row.completed_entries)}"
	frappe.enqueue(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.process_adjustment",
		adjustment=adjustment,
		queue=queue,
		timeout=ADJUSTMENT_JOB_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"valuation:{adjustment}:{job_revision}",
		deduplicate=True,
	)


def process_adjustment(adjustment):
	"""RQ entry point. Calculate once, then apply a bounded propagation chunk."""
	phase = "Calculation"
	try:
		doc = _lock_adjustment(adjustment)
		if doc.docstatus != 1 or doc.status in {"Completed", "Reversed"}:
			return
		if doc.status == "Reversal Queued" and not doc.reversal_of:
			# A duplicate original job must advance the negative child, never
			# regress the already-applied positive adjustment to Queued/Completed.
			for reversal in frappe.get_all(
				'YRP Stock Valuation Adjustment',
				filters={
					"reversal_of": doc.name,
					"docstatus": 1,
					"status": ["not in", ["Completed", "Reversed"]],
				},
				pluck="name",
			):
				enqueue_adjustment(reversal, retry=True)
			return
		if doc.reversal_of:
			original = frappe.db.get_value(
				'YRP Stock Valuation Adjustment',
				doc.reversal_of,
				["status", "retry_count"],
				as_dict=True,
			)
			original_status = original.status if original else None
			if original_status not in {"Completed", "Reversal Queued"}:
				if (
					original_status in {"Calculation Failed", "Apply Failed"}
					and cint(original.retry_count) >= MAX_AUTOMATIC_RETRIES
				):
					_set_adjustment_values(
						doc.name,
						status="Apply Failed",
						retry_count=MAX_AUTOMATIC_RETRIES,
						last_heartbeat=now_datetime(),
						error_log=_(
							"Reversal is waiting for original adjustment {0}, which exhausted automatic retries. Repair and manually retry the original first."
						).format(doc.reversal_of),
					)
					frappe.db.commit()
					return
				# A cancellation may commit before the source adjustment's RQ job
				# finishes. Never apply the negative overlay first: keep this
				# reversal queued and let completion of the original enqueue it.
				_set_adjustment_values(
					doc.name,
					status="Queued",
					last_heartbeat=now_datetime(),
				)
				enqueue_adjustment(doc.reversal_of, retry=True)
				frappe.db.commit()
				return

		# total_entries and the propagation plan are committed atomically. Reading
		# this invariant from the locked parent avoids locking a child propagation
		# row while holding its parent, which would invert the apply worker's order.
		if not cint(doc.total_entries):
			_set_adjustment_values(
				doc.name,
				status="Calculating",
				started_at=doc.started_at or now_datetime(),
				last_heartbeat=now_datetime(),
				error_log=None,
			)
			_build_initial_entries(doc)
			_refresh_progress(doc.name, status="Applying")

		# Never enter target/bucket application while retaining the parent row
		# lock acquired at the start of this worker.  Reversal creation uses the
		# compatible target-before-parent order; releasing here prevents a retry
		# (where propagation rows already exist) from forming a target<->parent
		# deadlock with cancellation.
		frappe.db.commit()

		phase = "Apply"
		_apply_chunk(doc.name)
	except Exception:
		frappe.db.rollback()
		_mark_failed(adjustment, phase, traceback.format_exc())
		raise


def _lock_adjustment(adjustment):
	rows = frappe.db.sql(
		"SELECT * FROM `tabYRP Stock Valuation Adjustment` WHERE name=%s FOR UPDATE",
		(adjustment,),
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("Stock Valuation Adjustment {0} does not exist.").format(adjustment))
	return frappe._dict(rows[0])


def _build_initial_entries(doc):
	rows = frappe.get_all(
		'YRP Stock Valuation Adjustment Source',
		filters={"parent": doc.name, "parenttype": 'YRP Stock Valuation Adjustment'},
		fields=["name", "source_sle", "target_sle", "difference"],
		order_by="idx asc",
	)
	if not rows:
		frappe.throw(_("Stock Valuation Adjustment {0} has no source rows.").format(doc.name))
	for row in rows:
		_validate_target_sle(row.target_sle)
		_queue_propagation_entry(
			adjustment=doc.name,
			entry_type="Source",
			target_sle=row.target_sle,
			difference=row.difference,
			source_sle=row.source_sle,
			idempotency_seed=f"source:{row.name}",
		)


def _validate_target_sle(target_sle, *, for_update=False):
	from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
		lock_stock_valuation_period,
	)

	lock_stock_valuation_period(shared=True)
	if for_update:
		rows = frappe.db.sql(
			"""
			SELECT name, qty, is_cancelled, posting_date, voucher_type, voucher_no
			FROM `tabYRP Stock Ledger Entry`
			WHERE name=%s
			FOR UPDATE
			""",
			(target_sle,),
			as_dict=True,
		)
		row = frappe._dict(rows[0]) if rows else None
	else:
		row = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			target_sle,
			[
				"name",
				"qty",
				"is_cancelled",
				"posting_date",
				"voucher_type",
				"voucher_no",
			],
			as_dict=True,
		)
	if not row:
		frappe.throw(_("Affected receipt Stock Ledger Entry {0} does not exist.").format(target_sle))
	if row.is_cancelled or flt(row.qty) <= 0:
		frappe.throw(_("Stock Ledger Entry {0} is not an active incoming receipt.").format(target_sle))
	from yrp.stock.stock_ledger import validate_stock_valuation_period

	validate_stock_valuation_period(row.posting_date, row.voucher_type, row.voucher_no)
	return row


def _queue_propagation_entry(
	*,
	adjustment,
	entry_type,
	target_sle,
	difference,
	idempotency_seed,
	parent_entry=None,
	source_sle=None,
):
	difference = flt(difference, 6)
	if abs(difference) <= VALUE_TOLERANCE:
		return None
	if parent_entry and _target_in_ancestry(parent_entry, target_sle):
		frappe.throw(
			_("Valuation lineage cycle detected at receipt SLE {0}.").format(target_sle)
		)
	key = _hash_key(adjustment, idempotency_seed, target_sle)
	target = frappe.db.get_value(
		'YRP Stock Ledger Entry',
		target_sle,
		["item", "warehouse", *get_dimension_fieldnames()],
		as_dict=True,
	)
	if not target:
		frappe.throw(_("Affected receipt Stock Ledger Entry {0} does not exist.").format(target_sle))
	dimensions = {fieldname: target.get(fieldname) for fieldname in get_dimension_fieldnames()}
	if entry_type != "Source":
		# Revalidate a newly discovered destination under its row lock before the
		# child propagation becomes visible. If cancellation won first, the whole
		# source-entry apply rolls back instead of leaving an unusable Pending row.
		locked_target = frappe.db.sql(
			"""
			SELECT name, qty, is_cancelled
			FROM `tabYRP Stock Ledger Entry`
			WHERE name=%s
			FOR UPDATE
			""",
			(target_sle,),
			as_dict=True,
		)
		if (
			not locked_target
			or locked_target[0].is_cancelled
			or flt(locked_target[0].qty) <= 0
		):
			frappe.throw(
				_("Downstream receipt Stock Ledger Entry {0} is no longer active.").format(
					target_sle
				)
			)
	doc = frappe.get_doc(
		{
			"doctype": 'YRP Stock Valuation Propagation Entry',
			"adjustment": adjustment,
			"parent_entry": parent_entry,
			"entry_type": entry_type,
			"status": "Pending",
			"source_sle": source_sle,
			"target_sle": target_sle,
			"item": target.item,
			"warehouse": target.warehouse,
			"difference": difference,
			"stock_dimensions": frappe.as_json(dimensions),
			"idempotency_key": key,
		}
	)
	doc.flags.ignore_permissions = True
	try:
		doc.insert()
	except frappe.UniqueValidationError:
		# Idempotency is a unique insert contract. Query only after the duplicate
		# proves a committed winner exists; this avoids missing-key gap locks.
		existing = frappe.db.sql(
			"""
			SELECT name
			FROM `tabYRP Stock Valuation Propagation Entry`
			WHERE idempotency_key=%s
			LIMIT 1
			FOR UPDATE
			""",
			(key,),
		)
		if existing:
			return existing[0][0]
		raise
	if entry_type != "Source":
		# Source targets were marked stale atomically when their parent SVA was
		# created. Repeating that write while holding the parent would invert the
		# target-before-parent order used by reversal creation.
		frappe.db.set_value(
			'YRP Stock Ledger Entry',
			target_sle,
			"valuation_is_stale",
			1,
			update_modified=False,
		)
	return doc.name


def _target_in_ancestry(parent_entry, target_sle):
	current = parent_entry
	visited = set()
	while current and current not in visited:
		visited.add(current)
		row = frappe.db.get_value(
			'YRP Stock Valuation Propagation Entry',
			current,
			["target_sle", "parent_entry"],
			as_dict=True,
		)
		if not row:
			return False
		if row.target_sle == target_sle:
			return True
		current = row.parent_entry
	return bool(current)


def _apply_chunk(adjustment):
	started = time.monotonic()
	chunk_size = cint(frappe.conf.get("stock_valuation_chunk_size")) or DEFAULT_CHUNK_SIZE
	soft_seconds = cint(frappe.conf.get("stock_valuation_chunk_seconds")) or DEFAULT_CHUNK_SECONDS
	processed = 0

	while processed < chunk_size and time.monotonic() - started < soft_seconds:
		name = frappe.db.get_value(
			'YRP Stock Valuation Propagation Entry',
			{"adjustment": adjustment, "status": ["in", ["Pending", "Failed"]]},
			"name",
			order_by="creation asc, name asc",
		)
		if not name:
			break
		_apply_propagation_entry(name)
		processed += 1
		_refresh_progress(adjustment, status="Applying")
		frappe.db.commit()

	pending = frappe.db.count(
		'YRP Stock Valuation Propagation Entry',
		{"adjustment": adjustment, "status": ["in", ["Pending", "Failed"]]},
	)
	if pending:
		enqueue_adjustment(adjustment)
		return
	if not _complete_adjustment(adjustment):
		frappe.db.commit()
		return
	frappe.db.commit()


def _apply_propagation_entry(entry_name):
	# Resolve immutable routing only as a lock hint, then end that read snapshot.
	# All valuation reads must start after the bucket mutex is acquired; otherwise
	# a worker that waited here could replay an overlay from its older RR snapshot.
	routing = frappe.db.get_value(
		'YRP Stock Valuation Propagation Entry', entry_name, ["target_sle"], as_dict=True
	)
	if not routing:
		return
	target_hint = _get_sle(routing.target_sle)
	if not target_hint:
		frappe.throw(
			_("Affected receipt Stock Ledger Entry {0} does not exist.").format(
				routing.target_sle
			)
		)
	frappe.db.commit()

	from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
		lock_stock_valuation_period,
	)

	# Universal order: period -> bucket -> target SLE -> propagation -> parent.
	# GET_LOCK waits before lock_valuation_bucket opens the new read snapshot.
	lock_stock_valuation_period(shared=True)
	_lock_valuation_bucket(target_hint)
	targets = frappe.db.sql(
		"SELECT * FROM `tabYRP Stock Ledger Entry` WHERE name=%s FOR UPDATE",
		(routing.target_sle,),
		as_dict=True,
	)
	if not targets:
		frappe.throw(
			_("Affected receipt Stock Ledger Entry {0} does not exist.").format(
				routing.target_sle
			)
		)
	target = frappe._dict(targets[0])
	if target.is_cancelled or flt(target.qty) <= 0:
		frappe.throw(
			_("Stock Ledger Entry {0} is not an active incoming receipt.").format(
				target.name
			)
		)
	from yrp.stock.stock_ledger import validate_stock_valuation_period

	validate_stock_valuation_period(
		target.posting_date, target.voucher_type, target.voucher_no
	)
	entries = frappe.db.sql(
		"""
		SELECT *
		FROM `tabYRP Stock Valuation Propagation Entry`
		WHERE name=%s
		FOR UPDATE
		""",
		(entry_name,),
		as_dict=True,
	)
	if not entries:
		return
	entry = frappe._dict(entries[0])
	if entry.status not in {"Pending", "Failed"}:
		return
	frappe.db.set_value(
		'YRP Stock Valuation Propagation Entry',
		entry.name,
		"status",
		"Applying",
		update_modified=False,
	)
	old_outgoing = _get_outgoing_snapshot(target)
	old_bucket_value = _get_bucket_stock_value(target)

	new_overlay = flt(target.valuation_adjustment_value) + flt(entry.difference)
	if flt(target.qty) * flt(target.rate) + new_overlay < -VALUE_TOLERANCE:
		frappe.throw(
			_(
				"Adjustment {0} would make receipt SLE {1} carry a negative value."
			).format(entry.adjustment, target.name)
		)
	frappe.db.set_value(
		'YRP Stock Ledger Entry',
		target.name,
		{
			"valuation_adjustment_value": flt(new_overlay, 6),
			"valuation_is_stale": 1,
		},
		update_modified=False,
	)
	_replay_target_bucket(target)
	_update_receipt_current_value(target)
	new_outgoing = _get_outgoing_snapshot(target)
	new_bucket_value = _get_bucket_stock_value(target)

	propagated = 0.0
	terminal = 0.0
	for sle_name in sorted(set(old_outgoing) | set(new_outgoing)):
		old_value = flt(old_outgoing.get(sle_name))
		new_value = flt(new_outgoing.get(sle_name))
		outgoing_difference = flt(-(new_value - old_value), 6)
		if abs(outgoing_difference) <= VALUE_TOLERANCE:
			continue
		result = _propagate_outgoing_difference(entry, sle_name, outgoing_difference)
		propagated += flt(result["propagated"])
		terminal += flt(result["terminal"])

	remaining = flt(entry.difference) - propagated - terminal
	frappe.db.set_value(
		'YRP Stock Valuation Propagation Entry',
		entry.name,
		{
			"status": "Applied",
			"remaining_difference": flt(remaining, 6),
			"terminal_difference": flt(terminal, 6),
			"old_stock_value": flt(old_bucket_value, 6),
			"new_stock_value": flt(new_bucket_value, 6),
			"applied_at": now_datetime(),
			"error_log": None,
		},
		update_modified=False,
	)


def _get_sle(name):
	fields = [
		"name",
		"item",
		"warehouse",
		"posting_date",
		"posting_time",
		"posting_datetime",
		"voucher_type",
		"voucher_no",
		"voucher_detail_no",
		"qty",
		"rate",
		"stock_value",
		"valuation_adjustment_value",
		"paired_stock_ledger_entry",
		*get_dimension_fieldnames(),
	]
	return frappe._dict(frappe.db.get_value('YRP Stock Ledger Entry', name, fields, as_dict=True))


def _update_receipt_current_value(target):
	"""Expose the effective receipt value without overwriting submitted rate."""
	if target.voucher_type != 'YRP Goods Received Note' or not target.voucher_detail_no:
		return
	meta = frappe.get_meta('YRP Goods Received Note Item')
	if not (
		meta.get_field("current_valuation_rate")
		and meta.get_field("current_valuation_value")
	):
		return
	qty = flt(target.qty)
	current_value = qty * flt(target.rate) + flt(
		frappe.db.get_value(
			'YRP Stock Ledger Entry', target.name, "valuation_adjustment_value"
		)
	)
	frappe.db.set_value(
		'YRP Goods Received Note Item',
		target.voucher_detail_no,
		{
			"current_valuation_rate": current_value / qty if qty else 0,
			"current_valuation_value": current_value,
		},
		update_modified=False,
	)


def _bucket_filters(target):
	filters = {
		"item": target.item,
		"warehouse": target.warehouse,
		"is_cancelled": 0,
		"posting_datetime": [">=", target.posting_datetime],
	}
	for fieldname in get_valuation_dimensions():
		filters[fieldname] = target.get(fieldname)
	return filters


def _lock_valuation_bucket(target):
	from yrp.stock.stock_ledger import lock_valuation_bucket

	lock_valuation_bucket(target)


def _get_outgoing_snapshot(target):
	rows = frappe.get_all(
		'YRP Stock Ledger Entry',
		filters={**_bucket_filters(target), "qty": ["<", 0]},
		fields=["name", "stock_value_difference"],
		order_by="posting_datetime asc, creation asc, name asc",
	)
	return {row.name: flt(row.stock_value_difference) for row in rows}


def _get_bucket_stock_value(target):
	return flt(
		frappe.db.get_value(
			'YRP Stock Ledger Entry',
			_bucket_filters(target),
			"stock_value",
			order_by="posting_datetime desc, creation desc, name desc",
		)
	)


def _replay_target_bucket(target):
	from yrp.stock.stock_ledger import UpdateEntriesAfter, _update_bins_in_valuation_bucket

	dim_fields = get_dimension_fieldnames()
	args = {
		"item": target.item,
		"warehouse": target.warehouse,
		"posting_date": target.posting_date,
		"posting_time": target.posting_time,
		"voucher_type": target.voucher_type,
		"voucher_no": None,
		"allow_zero_rate": 1,
	}
	for fieldname in dim_fields:
		args[fieldname] = target.get(fieldname)
	UpdateEntriesAfter(args, allow_negative_stock=True).run()
	bucket = {"item": target.item, "warehouse": target.warehouse}
	for fieldname in dim_fields:
		bucket[fieldname] = target.get(fieldname)
	_update_bins_in_valuation_bucket(bucket, get_valuation_dimensions(), dim_fields)


def _propagate_outgoing_difference(entry, outgoing_sle, difference):
	links = frappe.get_all(
		'YRP Stock Valuation Production Link',
		filters={"consumption_sle": outgoing_sle, "active": 1},
		fields=["name", "output_receipt_sle", "allocation_weight"],
		order_by="creation asc, name asc",
	)
	if links:
		weights = sum(max(flt(link.allocation_weight), 0) for link in links)
		if weights <= 0:
			frappe.throw(
				_("Production links for consumption SLE {0} have no allocation weight.").format(
					outgoing_sle
				)
			)
		assigned = 0.0
		for index, link in enumerate(links):
			share = (
				flt(difference) - assigned
				if index == len(links) - 1
				else flt(difference) * flt(link.allocation_weight) / weights
			)
			assigned += share
			_queue_propagation_entry(
				adjustment=entry.adjustment,
				entry_type="Production",
				parent_entry=entry.name,
				source_sle=outgoing_sle,
				target_sle=link.output_receipt_sle,
				difference=share,
				idempotency_seed=f"production:{entry.name}:{link.name}",
			)
		return {"propagated": flt(difference), "terminal": 0.0}

	paired = frappe.db.get_value(
		'YRP Stock Ledger Entry', outgoing_sle, "paired_stock_ledger_entry"
	)
	if paired:
		paired_row = frappe.db.get_value(
			'YRP Stock Ledger Entry', paired, ["qty", "is_cancelled"], as_dict=True
		)
		if paired_row and not paired_row.is_cancelled and flt(paired_row.qty) > 0:
			_queue_propagation_entry(
				adjustment=entry.adjustment,
				entry_type="Transfer",
				parent_entry=entry.name,
				source_sle=outgoing_sle,
				target_sle=paired,
				difference=difference,
				idempotency_seed=f"transfer:{entry.name}:{outgoing_sle}:{paired}",
			)
			return {"propagated": flt(difference), "terminal": 0.0}

	if _requires_persisted_lineage(outgoing_sle):
		frappe.throw(
			_(
				"Stock Ledger Entry {0} is a transfer or production consumption, but its valuation lineage is missing. Rebuild or map the legacy transaction before retrying."
			).format(outgoing_sle)
		)

	return {"propagated": 0.0, "terminal": flt(difference)}


def _requires_persisted_lineage(outgoing_sle):
	row = frappe.db.get_value(
		'YRP Stock Ledger Entry',
		outgoing_sle,
		["voucher_type", "voucher_no"],
		as_dict=True,
	)
	if not row:
		return False
	if row.voucher_type in {'YRP Delivery Challan', 'YRP Goods Received Note', 'YRP Work Order'}:
		return True
	if row.voucher_type == 'YRP Stock Entry':
		purpose = frappe.db.get_value('YRP Stock Entry', row.voucher_no, "purpose")
		return purpose in {
			"Send to Warehouse",
			"Receive at Warehouse",
			"DC Completion",
			"GRN Completion",
		}
	return False


def _refresh_progress(adjustment, status=None):
	total = frappe.db.count('YRP Stock Valuation Propagation Entry', {"adjustment": adjustment})
	completed = frappe.db.count(
		'YRP Stock Valuation Propagation Entry',
		{"adjustment": adjustment, "status": ["in", ["Applied", "Terminal"]]},
	)
	progress = (completed * 100 / total) if total else 0
	values = {
		"total_entries": total,
		"completed_entries": completed,
		"progress": flt(progress, 2),
		"last_heartbeat": now_datetime(),
	}
	if status:
		values["status"] = status
	_set_adjustment_values(adjustment, **values)


def _complete_adjustment(adjustment):
	reversal_of = frappe.db.get_value(
		'YRP Stock Valuation Adjustment', adjustment, "reversal_of"
	)
	stale_owners = [adjustment, reversal_of] if reversal_of else [adjustment]
	stale_targets, locked_entries = _lock_stale_targets(stale_owners)
	# Target locks prevent a new owner/reversal from slipping in. All following
	# reads are locking current reads so an older REPEATABLE READ snapshot cannot
	# hide work that committed while we waited for those targets.
	doc = _lock_adjustment(adjustment)
	current_entries = [row for row in locked_entries if row.adjustment == adjustment]
	if not current_entries or any(
		row.status not in {"Applied", "Terminal"} for row in current_entries
	):
		# A duplicate worker reached completion from an older candidate snapshot
		# while another entry was still advancing. The locked current rows are the
		# authority; leave the parent active and schedule the next safe delivery.
		enqueue_adjustment(adjustment, retry=True)
		return False
	values = [row for row in current_entries if row.status == "Applied"]
	stock_difference = flt(sum(flt(row.remaining_difference) for row in values), 6)
	terminal_difference = flt(sum(flt(row.terminal_difference) for row in values), 6)
	unexplained = flt(doc.total_source_difference) - stock_difference - terminal_difference
	if abs(unexplained) > CONSERVATION_TOLERANCE:
		frappe.throw(
			_(
				"Valuation conservation failed for {0}: source {1}, stock {2}, terminal {3}."
			).format(
				doc.name,
				flt(doc.total_source_difference, 6),
				stock_difference,
				terminal_difference,
			)
		)
	waiting_reversals = []
	if not doc.reversal_of:
		waiting_reversals = frappe.db.sql(
			"""
			SELECT name
			FROM `tabYRP Stock Valuation Adjustment`
			WHERE reversal_of=%s
			  AND docstatus=1
			  AND status NOT IN ('Completed', 'Reversed')
			ORDER BY creation, name
			FOR UPDATE
			""",
			(doc.name,),
			pluck=True,
		)
	status = "Reversal Queued" if waiting_reversals else "Completed"
	_set_adjustment_values(
		doc.name,
		status=status,
		progress=100,
		propagated_stock_difference=stock_difference,
		terminal_difference=terminal_difference,
		completed_at=now_datetime(),
		last_heartbeat=now_datetime(),
		error_log=None,
	)
	if doc.reversal_of:
		frappe.db.set_value(
			'YRP Stock Valuation Adjustment',
			doc.reversal_of,
			"status",
			"Reversed",
			update_modified=False,
		)
		_clear_stale_flags([doc.name, doc.reversal_of], stale_targets)
	elif waiting_reversals:
		for reversal in waiting_reversals:
			enqueue_adjustment(reversal)
	else:
		_clear_stale_flags([doc.name], stale_targets)
	return True


def _lock_stale_targets(adjustments):
	"""Lock a stable, current SVA target set bucket-first for completion."""
	adjustments = tuple(name for name in adjustments if name)
	dimension_fields = get_dimension_fieldnames()
	from yrp.stock.stock_ledger import _lock_entry_buckets

	for attempt in range(5):
		targets = set(
			frappe.get_all(
				'YRP Stock Valuation Propagation Entry',
				filters={"adjustment": ["in", adjustments]},
				pluck="target_sle",
				distinct=True,
			)
		)
		if not targets:
			return [], []
		rows = frappe.get_all(
			'YRP Stock Ledger Entry',
			filters={"name": ["in", targets]},
			fields=["name", "item", "warehouse", *dimension_fields],
		)
		if {row.name for row in rows} != targets:
			frappe.db.rollback()
			if attempt == 4:
				frappe.throw(_("A valuation propagation target no longer exists."))
			continue
		_lock_entry_buckets(rows, dimension_fields)
		_lock_target_sles(targets)
		current_entries = frappe.db.sql(
			"""
			SELECT name, adjustment, target_sle, status,
			       remaining_difference, terminal_difference
			FROM `tabYRP Stock Valuation Propagation Entry`
			WHERE adjustment IN %(adjustments)s
			ORDER BY target_sle, name
			FOR UPDATE
			""",
			{"adjustments": adjustments},
			as_dict=True,
		)
		current_targets = {row.target_sle for row in current_entries if row.target_sle}
		if current_targets == targets:
			return sorted(targets), current_entries
		# A worker discovered and committed another downstream target while this
		# transaction waited. Release the partial lock set and reacquire the full
		# sorted graph; never add a bucket while holding the parent adjustment.
		frappe.db.rollback()
	frappe.throw(_("Valuation lineage kept changing during completion. Please retry."))


def _clear_stale_flags(adjustments, targets):
	"""Clear a locked receipt only when no other unfinished SVA owns it."""
	for target_sle in targets:
		other_active = frappe.db.sql(
			"""
			SELECT propagation.name
			FROM `tabYRP Stock Valuation Propagation Entry` propagation
			INNER JOIN `tabYRP Stock Valuation Adjustment` adjustment
				ON adjustment.name = propagation.adjustment
			WHERE propagation.target_sle = %(target_sle)s
			  AND propagation.adjustment NOT IN %(adjustments)s
			  AND adjustment.docstatus = 1
			  AND adjustment.status NOT IN ('Completed', 'Reversed')
			LIMIT 1
			FOR UPDATE
			""",
			{"target_sle": target_sle, "adjustments": tuple(adjustments)},
		)
		if not other_active:
			frappe.db.set_value(
				'YRP Stock Ledger Entry',
				target_sle,
				"valuation_is_stale",
				0,
				update_modified=False,
			)


def _set_adjustment_values(name, **values):
	frappe.db.set_value(
		'YRP Stock Valuation Adjustment', name, values, update_modified=False
	)


def _mark_failed(adjustment, phase, error_log):
	if not frappe.db.exists('YRP Stock Valuation Adjustment', adjustment):
		return
	status = "Calculation Failed" if phase == "Calculation" else "Apply Failed"
	retry_count = cint(
		frappe.db.get_value('YRP Stock Valuation Adjustment', adjustment, "retry_count")
	) + 1
	_set_adjustment_values(
		adjustment,
		status=status,
		retry_count=retry_count,
		last_heartbeat=now_datetime(),
		error_log=error_log,
	)
	frappe.db.commit()
	frappe.log_error(
		message=error_log,
		title=_("Stock Valuation Adjustment {0} failed").format(adjustment),
	)


@frappe.whitelist()
def retry_adjustment(adjustment):
	doc = frappe.get_doc('YRP Stock Valuation Adjustment', adjustment)
	doc.check_permission("read")
	if doc.status not in {"Calculation Failed", "Apply Failed", "Queued", "Applying"}:
		frappe.throw(_("Only a queued, applying, or failed adjustment can be retried."))
	_set_adjustment_values(
		doc.name,
		status="Queued",
		queued_at=now_datetime(),
		error_log=None,
	)
	enqueue_adjustment(doc.name, retry=True)
	return doc.name


def recover_stalled_adjustments():
	"""Scheduler safety net for lost RQ jobs and dead workers."""
	stale_before = add_to_date(now_datetime(), minutes=-15)
	rows = frappe.get_all(
		'YRP Stock Valuation Adjustment',
		filters={
			"docstatus": 1,
			"status": ["in", ACTIVE_STATUSES],
		},
		fields=["name", "status", "last_heartbeat", "queued_at", "retry_count"],
		order_by="creation asc",
		limit=100,
	)
	for row in rows:
		if row.status in {"Calculation Failed", "Apply Failed"} and cint(
			row.retry_count
		) >= MAX_AUTOMATIC_RETRIES:
			continue
		last_seen = row.last_heartbeat or row.queued_at
		if row.status == "Queued" or not last_seen or last_seen < stale_before:
			_set_adjustment_values(row.name, status="Queued", queued_at=now_datetime())
			enqueue_adjustment(row.name, retry=True)


def register_production_links(source_doctype, source_name, links):
	"""Persist causal consumption-SLE -> output-receipt-SLE relationships."""
	created = []
	for row in links or []:
		row = frappe._dict(row)
		if not row.consumption_sle or not row.output_receipt_sle:
			frappe.throw(_("Both consumption and output receipt SLE are required."))
		_lock_target_sles([row.consumption_sle, row.output_receipt_sle])
		locked_sles = frappe.db.sql(
			"""
			SELECT name, qty, is_cancelled
			FROM `tabYRP Stock Ledger Entry`
			WHERE name IN %(sle_names)s
			FOR UPDATE
			""",
			{"sle_names": (row.consumption_sle, row.output_receipt_sle)},
			as_dict=True,
		)
		locked_by_name = {sle.name: sle for sle in locked_sles}
		consumption = locked_by_name.get(row.consumption_sle)
		output = locked_by_name.get(row.output_receipt_sle)
		if (
			not consumption
			or consumption.is_cancelled
			or flt(consumption.qty) >= 0
			or not output
			or output.is_cancelled
			or flt(output.qty) <= 0
		):
			frappe.throw(_("Production valuation links require active outgoing and incoming SLEs."))
		# A Work Order close records unreturned excess after its output receipt exists.
		# The exact SLE link carries causality; posting order does not define it.
		weight = flt(row.allocation_weight or row.input_quantity)
		if weight <= 0:
			frappe.throw(_("Production-link allocation weight must be greater than zero."))
		key = _hash_key(
			source_doctype,
			source_name,
			row.get("source_row"),
			row.consumption_sle,
			row.output_receipt_sle,
		)
		existing = frappe.db.get_value(
			'YRP Stock Valuation Production Link', {"idempotency_key": key}, "name"
		)
		if existing:
			frappe.db.set_value(
				'YRP Stock Valuation Production Link',
				existing,
				{
					"input_quantity": flt(row.input_quantity),
					"allocation_weight": weight,
					"active": 1,
				},
				update_modified=False,
			)
			created.append(existing)
			continue
		doc = frappe.get_doc(
			{
				"doctype": 'YRP Stock Valuation Production Link',
				"consumption_sle": row.consumption_sle,
				"output_receipt_sle": row.output_receipt_sle,
				"source_doctype": source_doctype,
				"source_name": source_name,
				"source_row": row.get("source_row"),
				"input_quantity": flt(row.input_quantity),
				"allocation_weight": weight,
				"stock_dimensions": row.get("stock_dimensions") or "{}",
				"active": 1,
				"idempotency_key": key,
			}
		)
		doc.flags.ignore_permissions = True
		try:
			doc.insert()
		except frappe.UniqueValidationError:
			existing = frappe.db.sql(
				"""
				SELECT name
				FROM `tabYRP Stock Valuation Production Link`
				WHERE idempotency_key=%s
				LIMIT 1
				FOR UPDATE
				""",
				(key,),
				pluck=True,
			)
			if not existing:
				raise
			frappe.db.set_value(
				'YRP Stock Valuation Production Link',
				existing[0],
				{
					"input_quantity": flt(row.input_quantity),
					"allocation_weight": weight,
					"active": 1,
				},
				update_modified=False,
			)
			created.append(existing[0])
		else:
			created.append(doc.name)
	return created


def deactivate_production_links(source_doctype, source_name):
	frappe.db.set_value(
		'YRP Stock Valuation Production Link',
		{"source_doctype": source_doctype, "source_name": source_name, "active": 1},
		"active",
		0,
		update_modified=False,
	)


def find_receipt_sle(voucher_type, voucher_no, voucher_detail_no):
	return frappe.db.get_value(
		'YRP Stock Ledger Entry',
		{
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"voucher_detail_no": voucher_detail_no,
			"qty": [">", 0],
			"is_cancelled": 0,
		},
		"name",
		order_by="creation desc, name desc",
	)


StockValuationAdjustment = YRPStockValuationAdjustment
