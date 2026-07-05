"""Stock Ledger Entry — immutable audit trail of every stock movement.

Dimensions (lot, received_type, etc.) are added as Custom Fields by
yrp/patches/create_stock_dimension_fields.py based on YRP Stock Settings.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, nowtime

from yrp.stock.dimensions import get_stock_dimensions


class StockLedgerEntry(Document):
	def validate(self):
		self.scrub_posting_time()
		self.set_posting_datetime()
		self.validate_mandatory()

	def on_submit(self):
		self.set_posting_datetime(save=True)

	def on_cancel(self):
		frappe.throw(_("Stock Ledger Entry cannot be cancelled directly. Cancel the parent voucher instead."))

	def scrub_posting_time(self):
		if not self.posting_time or self.posting_time == "00:0":
			self.posting_time = "00:00"

	def set_posting_datetime(self, save=False):
		if not self.posting_date:
			self.posting_date = getdate()
		if not self.posting_time:
			self.posting_time = nowtime()
		self.posting_datetime = get_datetime(f"{self.posting_date} {self.posting_time}")
		if save:
			self.db_set("posting_datetime", self.posting_datetime, update_modified=False)

	def validate_mandatory(self):
		mandatory = ["item", "warehouse", "posting_date", "voucher_type", "voucher_no"]
		for field in mandatory:
			if not self.get(field):
				frappe.throw(_("{0} is mandatory for Stock Ledger Entry").format(_(field.replace("_", " ").title())))

		# Validate mandatory dimensions from YRP Stock Settings
		for dim in get_stock_dimensions():
			if dim.get("mandatory") and not self.get(dim["fieldname"]):
				frappe.throw(_("Stock dimension {0} is mandatory").format(_(dim["label"])))
