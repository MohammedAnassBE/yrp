"""One retail UOM; existing balance values must never silently change units."""
import frappe
from frappe.model.document import Document
from yrp.yrp_retail.logic import require


class YRPRetailSettings(Document):
	def validate(self):
		from yrp.yrp_partner.permissions import is_partner
		if is_partner():
			frappe.throw("Partner users cannot change Retail Settings.", frappe.PermissionError)
		frappe.db.sql("select name from `tabDocType` where name='YRP Retail Settings' for update")
		rows = frappe.db.sql("select value from `tabSingles` where doctype='YRP Retail Settings' and field='stock_uom' for update")
		current = rows[0][0] if rows else None
		if current != self.stock_uom:
			rows = frappe.db.sql('select name from `tabYRP Customer Stock` limit 1 for update')
			require(not rows, 'Retail UOM cannot change while Customer stock balances exist.')
