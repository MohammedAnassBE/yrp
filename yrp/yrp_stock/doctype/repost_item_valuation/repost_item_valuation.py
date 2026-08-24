"""Repost Item Valuation — re-process SLEs forward to fix valuation."""

import traceback

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RepostItemValuation(Document):
	def validate(self):
		from yrp.stock.stock_ledger import validate_stock_valuation_period

		validate_stock_valuation_period(
			self.posting_date,
			self.voucher_type or self.doctype,
			self.voucher_no or self.name,
		)
		if self.based_on == "Transaction" and not (self.voucher_type and self.voucher_no):
			frappe.throw(_("Voucher Type/No required for Transaction-based repost"))
		if self.based_on == "Item and Warehouse":
			if not (self.item and self.warehouse):
				frappe.throw(_("Item and Warehouse required"))
			self.validate_mandatory_dimensions()

	def validate_mandatory_dimensions(self):
		from yrp.stock.dimensions import get_mandatory_dimensions

		for dim in get_mandatory_dimensions():
			if not self.get(dim["fieldname"]):
				frappe.throw(
					_("Stock dimension {0} is mandatory for Item and Warehouse repost").format(dim["label"])
				)

	def on_submit(self):
		self.db_set("status", "Queued")
		queue = frappe.conf.get("stock_valuation_queue") or "long"
		frappe.enqueue(
			"yrp.yrp_stock.doctype.repost_item_valuation.repost_item_valuation.repost",
			doc=self.name,
			queue=queue,
			timeout=3600,
			enqueue_after_commit=True,
			job_id=f"repost-valuation:{self.name}",
			deduplicate=True,
		)


def repost(doc):
	"""Background entry point — runs the repost for the given doc name."""
	from yrp.stock.stock_ledger import repost_future_sle

	frappe.db.sql(
		"SELECT name FROM `tabRepost Item Valuation` WHERE name=%s FOR UPDATE",
		(doc,),
	)
	rv = frappe.get_doc("Repost Item Valuation", doc)
	if rv.docstatus != 1 or rv.status in {"Completed", "In Progress"}:
		return
	try:
		rv.db_set("status", "In Progress")
		frappe.db.commit()
		repost_future_sle(rv)
		rv.db_set("status", "Completed")
		frappe.db.commit()
	except Exception:
		error_log = traceback.format_exc()
		# A bucket replay is atomic. Roll back every SLE/Bin mutation made since
		# the last per-bucket checkpoint before persisting the failure state;
		# otherwise the failure commit would also commit a half-replayed bucket.
		frappe.db.rollback()
		rv = frappe.get_doc("Repost Item Valuation", doc)
		rv.db_set("status", "Failed")
		rv.db_set("retry_count", cint(rv.retry_count) + 1)
		rv.db_set("error_log", error_log)
		frappe.db.commit()
		raise


MAX_RETRY_COUNT = 3


MAX_PER_RUN = 100  # Prevent runaway iteration if thousands are queued


def repost_entries():
	"""Hourly scheduler — picks up Queued and retryable Failed repost docs.

	Bug C (r-010 Critical #6): before picking up new work, reset any
	'In Progress' row whose worker hasn't touched it for >2 hours. Workers
	have a 1-hour timeout, so 2 hours is a safe staleness threshold. We
	reset to 'Queued' (not 'Failed') so the retry counter is preserved —
	infrastructure crashes shouldn't burn retry budget.

	Processes oldest first (by posting_date). Limited to MAX_PER_RUN per hour
	to prevent the scheduler from running indefinitely.
	"""
	from frappe.utils import add_to_date, now_datetime

	stale_threshold = add_to_date(now_datetime(), hours=-2)
	frappe.db.sql(
		"""
		UPDATE `tabRepost Item Valuation`
		SET status = 'Queued'
		WHERE status = 'In Progress'
		  AND modified < %s
		  AND docstatus = 1
		""",
		stale_threshold,
	)
	frappe.db.commit()

	riv = frappe.qb.DocType("Repost Item Valuation")
	names = (
		frappe.qb.from_(riv)
		.select(riv.name)
		.where(riv.docstatus == 1)
		.where(
			(riv.status == "Queued")
			| ((riv.status == "Failed") & (riv.retry_count < MAX_RETRY_COUNT))
		)
		.orderby(riv.posting_date)
		.orderby(riv.posting_time)
		.limit(MAX_PER_RUN)
		.run(pluck="name")
	)
	for name in names:
		try:
			repost(name)
		except Exception:
			frappe.log_error(title=_("Repost {0} failed").format(name))
