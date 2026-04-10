"""Repost Item Valuation — re-process SLEs forward to fix valuation."""

import traceback

import frappe
from frappe.model.document import Document


class RepostItemValuation(Document):
	def validate(self):
		if self.based_on == "Transaction" and not (self.voucher_type and self.voucher_no):
			frappe.throw("Voucher Type/No required for Transaction-based repost")
		if self.based_on == "Item and Warehouse" and not (self.item and self.warehouse):
			frappe.throw("Item and Warehouse required")

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
	except Exception:
		rv.db_set("status", "Failed")
		rv.db_set("error_log", traceback.format_exc())
		raise


def repost_entries():
	"""Hourly scheduler — picks up Queued repost docs."""
	for name in frappe.get_all("Repost Item Valuation", filters={"status": "Queued", "docstatus": 1}, pluck="name"):
		try:
			repost(name)
		except Exception:
			frappe.log_error(title=f"Repost {name} failed")
