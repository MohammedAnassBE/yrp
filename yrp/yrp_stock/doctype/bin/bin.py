"""Bin — balance snapshot per Item × Warehouse × dimensions.

Dimension fields are added by the YRP Stock dimension patch.
"""

import frappe
from frappe.model.document import Document
from frappe.query_builder.functions import CombineDatetime, Sum

from yrp.stock.dimensions import get_stock_dimensions


class Bin(Document):
	def before_save(self):
		if not self.stock_uom and self.item_code:
			parent = frappe.db.get_value("Item Variant", self.item_code, "item")
			if parent:
				self.stock_uom = frappe.db.get_value("Item", parent, "default_unit_of_measure")

	def update_reserved_stock(self):
		from yrp.stock.utils import get_sre_reserved_qty

		filters = {"item_code": self.item_code, "warehouse": self.warehouse}
		for dim in get_stock_dimensions():
			filters[dim["fieldname"]] = self.get(dim["fieldname"])
		self.reserved_qty = get_sre_reserved_qty(filters)
		if self.reserved_qty > self.actual_qty:
			frappe.throw(f"Reserved qty {self.reserved_qty} exceeds actual qty {self.actual_qty}")
		self.db_set("reserved_qty", self.reserved_qty, update_modified=False)


def update_qty(bin_name, args):
	"""Refresh Bin.actual_qty from latest SLE for matching dimensions."""
	from yrp.stock.utils import get_combine_datetime

	bin_doc = frappe.get_doc("Bin", bin_name)

	sle = frappe.qb.DocType("Stock Ledger Entry")
	q = (
		frappe.qb.from_(sle)
		.select(sle.qty_after_transaction, sle.valuation_rate, sle.stock_value)
		.where(sle.item == bin_doc.item_code)
		.where(sle.warehouse == bin_doc.warehouse)
		.where(sle.is_cancelled == 0)
	)
	for dim in get_stock_dimensions():
		fn = dim["fieldname"]
		val = bin_doc.get(fn)
		if val is not None:
			q = q.where(sle[fn] == val)

	q = q.orderby(CombineDatetime(sle.posting_date, sle.posting_time), order=frappe.qb.desc).limit(1)
	rows = q.run(as_dict=True)
	if rows:
		row = rows[0]
		bin_doc.db_set("actual_qty", row.qty_after_transaction or 0, update_modified=False)
		bin_doc.db_set("valuation_rate", row.valuation_rate or 0, update_modified=False)
		bin_doc.db_set("stock_value", row.stock_value or 0, update_modified=False)
	else:
		bin_doc.db_set("actual_qty", 0, update_modified=False)
