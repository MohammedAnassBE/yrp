"""Transactional demand aggregation with exclusive source claims and customer allocation."""
import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from yrp.yrp_retail.logic import aggregate_orders, finite_number, require, same_date


class YRPRetailOrderSummary(Document):
	def validate(self):
		require(self.customer and self.sales_person, "Customer and Sales Person are required.")
		require(self.order_type == "Secondary", "Only Secondary Retail Orders use a summary. Create a Sales Order directly for Primary orders.")
		require(self.from_date and self.to_date, "Both summary dates are required.")
		require(getdate(self.from_date) <= getdate(self.to_date), "From Date cannot follow To Date.")
		names = [row.order for row in self.source_orders]
		require(names and all(names), "At least one source order is required.")
		require(len(names) == len(set(names)), "Duplicate source orders are not allowed.")
		old = self.get_doc_before_save()
		if old:
			for field in ("customer", "sales_person", "order_type", "from_date", "to_date"):
				unchanged = same_date(self.get(field), old.get(field)) if field.endswith("date") else self.get(field) == old.get(field)
				require(unchanged, "Summary headers cannot change after creation.")
			require(set(names) == {row.order for row in old.source_orders}, "Summary source orders cannot change after creation.")
		orders = []
		for name in sorted(names):
			# All summary writers take these locks in the same order.
			found = frappe.db.sql("select name from `tabYRP Retail Order` where name=%s for update", name)
			require(found, "Source order does not exist.")
			order = frappe.get_doc("YRP Retail Order", name, for_update=True)
			require(not order.summary or order.summary == self.name, "A source order already belongs to another summary.")
			for field in ("customer", "sales_person", "order_type"):
				require(order.get(field) == self.get(field), "All source orders must match the summary header.")
			require(order.order_date and getdate(self.from_date) <= getdate(order.order_date) <= getdate(self.to_date), "Source order date is outside the summary date range.")
			orders.append(order)
		requested = aggregate_orders(orders)
		stock = {}
		for row in self.items:
			key = (row.item_code, row.uom)
			require(key not in stock, "Duplicate summary Item and UOM rows are not allowed.")
			require(key in requested, "Summary items must come from the source orders.")
			stock[key] = finite_number(row.customer_stock_qty if row.customer_stock_qty is not None else 0, "Customer stock quantity")
		self.set("items", [])
		for (item_code, uom), qty in requested.items():
			qty = finite_number(qty, "Requested quantity")
			available = stock.get((item_code, uom), 0)
			if frappe.db.get_value("UOM", uom, "must_be_whole_number"):
				require(float(available).is_integer(), "Customer stock must be a whole number for this UOM.")
			require(0 <= available <= qty, "Customer stock quantity must be between zero and requested quantity.")
			self.append("items", {"item_code": item_code, "uom": uom, "requested_qty": qty, "customer_stock_qty": available, "company_qty": qty - available})
		self.total_requested_qty = finite_number(sum(row.requested_qty for row in self.items), "Total requested quantity")
		self.total_customer_stock_qty = finite_number(sum(row.customer_stock_qty for row in self.items), "Total customer stock quantity")
		self.total_company_qty = finite_number(sum(row.company_qty for row in self.items), "Total company quantity")

	def on_update(self):
		if self.docstatus == 2:
			return
		for row in self.source_orders:
			frappe.db.set_value("YRP Retail Order", row.order, "summary", self.name, update_modified=False)

	def on_submit(self):
		from yrp.yrp_retail.customer_stock import consume_summary
		consume_summary(self)

	def on_cancel(self):
		self._release_orders()

	def on_trash(self):
		self._release_orders()

	def _release_orders(self):
		# Conditional updates cannot clear another summary's claim.
		for name in sorted(row.order for row in self.source_orders):
			frappe.db.sql("update `tabYRP Retail Order` set summary=NULL where name=%s and summary=%s", (name, self.name))
