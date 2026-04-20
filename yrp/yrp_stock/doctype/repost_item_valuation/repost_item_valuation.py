"""Repost Item Valuation — re-process SLEs forward to fix valuation."""

import traceback

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RepostItemValuation(Document):
	def validate(self):
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
		frappe.enqueue(
			"yrp.yrp_stock.doctype.repost_item_valuation.repost_item_valuation.repost",
			doc=self.name,
			queue="long",
			timeout=3600,
		)


def repost(doc):
	"""Background entry point — runs the repost for the given doc name."""
	from yrp.stock.stock_ledger import repost_future_sle

	rv = frappe.get_doc("Repost Item Valuation", doc)
	try:
		rv.db_set("status", "In Progress")
		repost_future_sle(rv)
		rv.db_set("status", "Completed")
		frappe.db.commit()
	except Exception:
		rv.db_set("status", "Failed")
		rv.db_set("retry_count", cint(rv.retry_count) + 1)
		rv.db_set("error_log", traceback.format_exc())
		frappe.db.commit()
		raise


MAX_RETRY_COUNT = 3


MAX_PER_RUN = 100  # Prevent runaway iteration if thousands are queued


def repost_entries():
	"""Hourly scheduler — picks up Queued and retryable Failed repost docs.

	Processes oldest first (by posting_date). Limited to MAX_PER_RUN per hour
	to prevent the scheduler from running indefinitely.
	"""
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
