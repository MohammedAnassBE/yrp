"""Audited stock-valuation period closing for base YRP."""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, formatdate, getdate, now, today

from yrp.stock.dimensions import (
	assert_safe_fieldname,
	get_dimension_fieldnames,
	get_valuation_dimensions,
)


ACTIVE_REPOST_STATUSES = ("Queued", "In Progress", "Failed")
QTY_TOLERANCE = 0.000001


class YRPStockValuationClosing(Document):
	def validate(self):
		self.set_period_boundaries()
		self.refresh_closing_snapshot()

	def before_submit(self):
		_lock_closing_state()
		self.set_period_boundaries()
		self.refresh_closing_snapshot()
		if self.validation_status == "Blocked":
			summary = _parse_summary(self.validation_summary)
			frappe.throw(
				summary.get("blocking_issues") or [_('Stock valuation is not ready to close.')],
				as_list=True,
				title=_("Stock Valuation Closing Blocked"),
			)
		self.closed_by = frappe.session.user
		self.closed_on = now()

	def on_submit(self):
		_set_settings_cutoff(self.closing_through_date)

	def before_cancel(self):
		_lock_closing_state()
		latest = get_latest_submitted_closing()
		if not latest or latest.name != self.name:
			frappe.throw(
				_("Only the latest submitted Stock Valuation Closing can be cancelled."),
				title=_("Cancel Latest Closing First"),
			)

	def on_cancel(self):
		previous = get_latest_submitted_closing(exclude_name=self.name)
		_set_settings_cutoff(previous.closing_through_date if previous else None)

	def set_period_boundaries(self):
		if not self.closing_through_date:
			return
		closing_date = getdate(self.closing_through_date)
		if closing_date > getdate(today()):
			frappe.throw(_("Stock valuation cannot be closed through a future date."))

		latest = get_latest_submitted_closing(exclude_name=self.name)
		previous_date = getdate(latest.closing_through_date) if latest else None
		if previous_date and closing_date <= previous_date:
			frappe.throw(
				_("Closing Through Date must be after the current closing date {0}.").format(
					formatdate(previous_date)
				)
			)

		self.previous_closing_date = previous_date
		if previous_date:
			self.period_start_date = add_days(previous_date, 1)
		else:
			earliest = frappe.db.sql(
				"""
				SELECT MIN(posting_date)
				FROM `tabYRP Stock Ledger Entry`
				WHERE is_cancelled = 0 AND posting_date <= %s
				""",
				(closing_date,),
			)[0][0]
			self.period_start_date = earliest or closing_date

	def refresh_closing_snapshot(self):
		if not self.closing_through_date:
			self.validation_status = "Pending"
			return

		snapshot = get_closing_snapshot(self.closing_through_date)
		self.active_stock_ledger_entries = snapshot["active_stock_ledger_entries"]
		self.closing_stock_value = snapshot["closing_stock_value"]
		self.active_repost_count = snapshot["active_repost_count"]
		self.active_valuation_adjustment_count = snapshot.get(
			"active_valuation_adjustment_count", 0
		)
		self.negative_stock_bucket_count = snapshot["negative_stock_bucket_count"]
		self.zero_valuation_bucket_count = snapshot["zero_valuation_bucket_count"]
		self.validation_status = "Blocked" if snapshot["blocking_issues"] else "Ready"
		self.validation_summary = json.dumps(snapshot, indent=2, default=str)


def _lock_closing_state():
	"""Serialize submit/cancel so two users cannot move the cutoff concurrently."""
	lock_stock_valuation_period()


def lock_stock_valuation_period(*, shared=False):
	"""Coordinate stock/valuation work with period closing.

	Ordinary stock and valuation transactions take a shared lock, so they may
	run concurrently with each other.  Closing submit/cancel takes the default
	exclusive lock.  The close therefore waits for already-running valuation
	work, while new work waits until the cutoff has been committed and can be
	validated against the latest value.
	"""
	lock_clause = "LOCK IN SHARE MODE" if shared else "FOR UPDATE"
	frappe.db.sql(
		f"SELECT name FROM `tabDocType` WHERE name=%s {lock_clause}",
		('YRP Stock Valuation Closing',),
	)


def get_latest_submitted_closing(exclude_name=None):
	filters = {"docstatus": 1}
	if exclude_name:
		filters["name"] = ["!=", exclude_name]
	rows = frappe.get_all(
		'YRP Stock Valuation Closing',
		filters=filters,
		fields=["name", "closing_through_date"],
		order_by="closing_through_date desc, creation desc",
		limit=1,
	)
	return frappe._dict(rows[0]) if rows else None


def _set_settings_cutoff(closing_date):
	if not closing_date:
		frappe.db.delete(
			"Singles",
			filters={
				"doctype": 'YRP YRP Stock Settings',
				"field": "last_stock_valuation_closing_date",
			},
		)
		frappe.clear_document_cache('YRP YRP Stock Settings', 'YRP YRP Stock Settings')
		frappe.db.value_cache.get('YRP YRP Stock Settings', {}).pop(
			"last_stock_valuation_closing_date", None
		)
		return
	frappe.db.set_single_value(
		'YRP YRP Stock Settings',
		"last_stock_valuation_closing_date",
		getdate(closing_date),
	)


def _parse_summary(value):
	if not value:
		return {}
	if isinstance(value, dict):
		return value
	try:
		return json.loads(value)
	except (TypeError, ValueError):
		return {}


def get_closing_snapshot(closing_through_date):
	closing_date = getdate(closing_through_date)
	active_repost_count = frappe.db.count(
		'YRP Repost Item Valuation',
		filters={
			"docstatus": 1,
			"status": ["in", ACTIVE_REPOST_STATUSES],
			"posting_date": ["<=", closing_date],
		},
	)
	active_valuation_adjustment_count = frappe.db.count(
		'YRP Stock Valuation Adjustment',
		filters={
			"docstatus": 1,
			"status": ["not in", ["Completed", "Reversed"]],
			"effective_date": ["<=", closing_date],
		},
	)
	negative_count = _get_negative_stock_bucket_count(closing_date)
	latest_valuation_rows = _get_latest_valuation_rows(closing_date)
	zero_valuation_count = sum(
		1 for row in latest_valuation_rows if _has_positive_zero_valuation_stock(row)
	)
	blocking_issues = []
	if active_repost_count:
		blocking_issues.append(
			_("{0} pending or failed valuation repost(s) affect this period.").format(
				active_repost_count
			)
		)
	if active_valuation_adjustment_count:
		blocking_issues.append(
			_("{0} pending or failed late-cost adjustment(s) affect this period.").format(
				active_valuation_adjustment_count
			)
		)
	if negative_count:
		blocking_issues.append(
			_("{0} stock bucket(s) are negative at the closing date.").format(negative_count)
		)
	if zero_valuation_count:
		blocking_issues.append(
			_("{0} positive stock bucket(s) have zero valuation at the closing date.").format(
				zero_valuation_count
			)
		)

	return {
		"closing_through_date": str(closing_date),
		"checked_at": str(now()),
		"active_stock_ledger_entries": frappe.db.count(
			'YRP Stock Ledger Entry',
			filters={"is_cancelled": 0, "posting_date": ["<=", closing_date]},
		),
		"closing_stock_value": flt(sum(flt(row.stock_value) for row in latest_valuation_rows), 6),
		"active_repost_count": active_repost_count,
		"active_valuation_adjustment_count": active_valuation_adjustment_count,
		"negative_stock_bucket_count": negative_count,
		"zero_valuation_bucket_count": zero_valuation_count,
		"blocking_issues": blocking_issues,
	}


def _get_latest_valuation_rows(closing_date):
	partition_fields = ["item", "warehouse", *get_valuation_dimensions()]
	for fieldname in partition_fields:
		assert_safe_fieldname(fieldname)
	partition = ", ".join(f"`{fieldname}`" for fieldname in partition_fields)
	return frappe.db.sql(
		f"""
		SELECT stock_value, valuation_rate, stock_queue
		FROM (
			SELECT stock_value, valuation_rate, stock_queue,
				ROW_NUMBER() OVER (
					PARTITION BY {partition}
					ORDER BY posting_date DESC, posting_time DESC, creation DESC, name DESC
				) AS row_number
			FROM `tabYRP Stock Ledger Entry`
			WHERE is_cancelled = 0 AND posting_date <= %s
		) latest
		WHERE row_number = 1
		""",
		(closing_date,),
		as_dict=True,
	)


def _get_negative_stock_bucket_count(closing_date):
	partition_fields = ["item", "warehouse", *get_dimension_fieldnames()]
	for fieldname in partition_fields:
		assert_safe_fieldname(fieldname)
	partition = ", ".join(f"`{fieldname}`" for fieldname in partition_fields)
	return frappe.db.sql(
		f"""
		SELECT COUNT(*)
		FROM (
			SELECT qty_after_transaction,
				ROW_NUMBER() OVER (
					PARTITION BY {partition}
					ORDER BY posting_date DESC, posting_time DESC, creation DESC, name DESC
				) AS row_number
			FROM `tabYRP Stock Ledger Entry`
			WHERE is_cancelled = 0 AND posting_date <= %s
		) latest
		WHERE row_number = 1 AND qty_after_transaction < %s
		""",
		(closing_date, -QTY_TOLERANCE),
	)[0][0]


def _has_positive_zero_valuation_stock(row):
	try:
		queue = json.loads(row.stock_queue or "[]")
	except (TypeError, ValueError):
		queue = []
	quantity = sum(flt(layer[0]) for layer in queue if isinstance(layer, (list, tuple)) and layer)
	return quantity > QTY_TOLERANCE and abs(flt(row.valuation_rate)) <= QTY_TOLERANCE


@frappe.whitelist()
def check_readiness(name):
	doc = frappe.get_doc('YRP Stock Valuation Closing', name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft Stock Valuation Closing can be checked."))
	doc.save()
	return {
		"validation_status": doc.validation_status,
		"validation_summary": _parse_summary(doc.validation_summary),
	}


StockValuationClosing = YRPStockValuationClosing
