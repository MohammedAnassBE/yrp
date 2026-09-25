"""Transactional demand aggregation with exclusive source claims and customer allocation."""
import math

import frappe
from frappe.model.document import Document
from frappe.model.meta import get_field_precision
from frappe.utils import flt, getdate

from yrp.yrp_retail.logic import aggregate_orders, finite_number, require, same_date, validate_uom_quantity
from yrp.yrp_retail.pricing import lock_pricing_policy
from yrp.yrp_retail.source_quantities import order_conversion_factors, validate_current_conversion


class YRPRetailOrderSummary(Document):
	def before_validate(self):
		# insert() does not pass through PricingLockMixin._save(). Keep the
		# pricing -> source order -> Item lock order identical on both paths.
		lock_pricing_policy()

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
			require(order.docstatus != 2, "Cancelled Retail Orders cannot be included in a summary.")
			require(not order.summary or order.summary == self.name, "A source order already belongs to another summary.")
			for field in ("customer", "sales_person", "order_type"):
				require(order.get(field) == self.get(field), "All source orders must match the summary header.")
			require(order.order_date and getdate(self.from_date) <= getdate(order.order_date) <= getdate(self.to_date), "Source order date is outside the summary date range.")
			orders.append(order)
		requested = aggregate_orders(orders)
		factors = order_conversion_factors(orders)
		sales_item_meta = frappe.get_meta("Sales Order Item")
		stock_precision = get_field_precision(sales_item_meta.get_field("stock_qty"))
		quantity_precision = get_field_precision(sales_item_meta.get_field("qty"))
		stock = {}
		for row in self.items:
			key = (row.item_code, row.uom)
			require(key not in stock, "Duplicate summary Item and UOM rows are not allowed.")
			require(key in requested, "Summary items must come from the source orders.")
			stock[key] = finite_number(row.customer_stock_qty if row.customer_stock_qty is not None else 0, "Customer stock quantity")
		self.set("items", [])
		for (item_code, uom), qty in requested.items():
			row = self.append("items", {"item_code": item_code, "uom": uom})
			qty = validate_uom_quantity(qty, uom, "Requested quantity", row.precision("requested_qty"))
			available = validate_uom_quantity(stock.get((item_code, uom), 0), uom,
				"Customer stock quantity", row.precision("customer_stock_qty"))
			require(0 <= available <= qty, "Customer stock quantity must be between zero and requested quantity.")
			company_qty = validate_uom_quantity(qty - available, uom, "Company quantity", row.precision("company_qty"))
			item = frappe.get_doc("Item", item_code, for_update=True)
			factor = validate_current_conversion(item, uom, factors[(item_code, uom)])
			# Validate the eventual native Sales Order before claiming/consuming
			# Customer supply. Its bin continues to store the entered retail UOM.
			for label, quantity in (("Customer stock allocation", available), ("Company quantity", company_qty)):
				validate_uom_quantity(quantity * factor, item.stock_uom, label, stock_precision)
			# Native Sales Order rounds its row quantity before converting it.
			# A remainder such as 2/3 pack may represent two pieces in memory,
			# but 0.667 packs of three cannot be fulfilled as whole pieces.
			native_quantity = flt(company_qty, quantity_precision)
			require(company_qty == 0 or native_quantity > 0,
				"Company quantity is too small for the Sales Order quantity precision.")
			validate_uom_quantity(native_quantity * factor,
				item.stock_uom, "Company quantity at Sales Order precision", stock_precision)
			# Whole-number checks alone would still allow 6 pieces to become
			# 10 when a small pack remainder rounds up. Compare both quantities
			# at the precision actually recorded by native Sales Orders.
			require(flt(company_qty * factor, stock_precision) == flt(native_quantity * factor, stock_precision),
				"Company stock quantity changes at Sales Order precision. Adjust the Customer allocation.")
			# Source capacity is in the selected UOM, even when stock rounding
			# hides a change in the converted quantity.
			require(math.isclose(company_qty, native_quantity, rel_tol=1e-12, abs_tol=1e-9),
				"Company quantity changes at Sales Order precision. Adjust the Customer allocation.")
			row.update({"requested_qty": qty, "customer_stock_qty": available, "company_qty": company_qty})
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

	def on_discard(self):
		self._release_orders()

	def on_trash(self):
		self._release_orders()

	def _release_orders(self):
		# Conditional updates cannot clear another summary's claim.
		for name in sorted(row.order for row in self.source_orders):
			frappe.db.sql("update `tabYRP Retail Order` set summary=NULL where name=%s and summary=%s", (name, self.name))
