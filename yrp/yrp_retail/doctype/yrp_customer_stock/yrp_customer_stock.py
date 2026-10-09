"""Unique Customer/item balance, writable only by the validated retail service."""
import frappe
from frappe.model.document import Document
from yrp.yrp_retail.customer_stock import permitted_write, validate_qty


def on_doctype_update():
	frappe.db.add_unique('YRP Customer Stock', ['customer', 'item_code'], constraint_name='unique_customer_stock_item')


class YRPCustomerStock(Document):
	def validate(self):
		if not permitted_write(self):
			frappe.throw('Update Customer stock through the retail stock API.', frappe.PermissionError)
		self.qty = validate_qty(self.qty, self.uom)

	def on_trash(self):
		frappe.throw('Customer stock balances cannot be deleted; report a new count instead.', frappe.PermissionError)

	def before_rename(self, *args, **kwargs):
		frappe.throw('Customer stock balances cannot be renamed.', frappe.PermissionError)
