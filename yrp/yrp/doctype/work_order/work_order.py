import frappe
from frappe.model.document import Document
from frappe.utils import flt, nowdate


class WorkOrder(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		self.set_onload(
			"deliverable_details",
			group_items_for_ui(self.get("deliverables") or [], "Work Order Deliverables"),
		)
		self.set_onload(
			"receivable_details",
			group_items_for_ui(self.get("receivables") or [], "Work Order Receivables"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.set_missing_dates()

	def validate(self):
		self.set_missing_dates()
		self.set_pending_quantities()
		self.set_total_quantity()
		self.set_status()

	def before_submit(self):
		if not self.get("deliverables"):
			frappe.throw("There are no deliverables on the Work Order.")
		if not self.get("receivables"):
			frappe.throw("There are no receivables on the Work Order.")
		if not self.start_date:
			self.start_date = nowdate()
		if not self.get("work_order_tracking_logs"):
			self.append("work_order_tracking_logs", {
				"from_date": self.planned_start_date,
				"to_date": self.expected_delivery_date,
				"user": frappe.session.user,
			})
		self.set_pending_quantities()
		self.set_total_quantity()
		self.set_status()

	def on_update_after_submit(self):
		self.set_status()
		self.db_set("status", self.status, update_modified=False)
		self.db_set("is_delivered", self.is_delivered, update_modified=False)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	def set_missing_dates(self):
		if not self.wo_date:
			self.wo_date = nowdate()
		if not self.planned_start_date:
			self.planned_start_date = nowdate()
		if not self.expected_delivery_date:
			self.expected_delivery_date = self.planned_end_date or self.planned_start_date

	def set_pending_quantities(self):
		if self.docstatus != 0:
			return
		for row in self.get("deliverables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty
		for row in self.get("receivables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty

	def set_total_quantity(self):
		calculated_qty = sum(flt(row.quantity) for row in self.get("work_order_calculated_items") or [])
		receivable_qty = sum(flt(row.qty) for row in self.get("receivables") or [])
		deliverable_qty = sum(flt(row.qty) for row in self.get("deliverables") or [])
		self.total_quantity = calculated_qty or receivable_qty or deliverable_qty
		if not self.planned_quantity and self.total_quantity:
			self.planned_quantity = self.total_quantity

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
			return
		if self.docstatus == 2:
			self.status = "Cancelled"
			return

		status = "Submitted"

		total_deliverable_qty = sum(flt(row.qty) for row in self.get("deliverables") or [])
		total_delivery_pending = sum(flt(row.pending_quantity) for row in self.get("deliverables") or [])
		if total_deliverable_qty:
			if total_delivery_pending <= 0:
				status = "Fully Delivered"
			elif total_delivery_pending < total_deliverable_qty:
				status = "Partially Delivered"

		total_receivable_qty = sum(flt(row.qty) for row in self.get("receivables") or [])
		total_received_pending = sum(flt(row.pending_quantity) for row in self.get("receivables") or [])
		if total_receivable_qty:
			received_qty = total_receivable_qty - total_received_pending
			if received_qty > 0:
				status = "Fully Received" if total_received_pending <= 0 else "Partially Received"

		total_billed_qty = sum(flt(row.billed_qty) for row in self.get("work_order_calculated_items") or [])
		if total_receivable_qty and total_billed_qty:
			status = "Fully Billed" if total_billed_qty >= total_receivable_qty else "Partially Billed"

		if self.open_status == "Close":
			status = "Closed"
		elif self.open_status == "Close Request":
			status = "Close Request"

		self.status = status
		self.is_delivered = 1 if total_deliverable_qty and total_delivery_pending <= 0 else 0

	def sync_vue_item_details(self):
		if self.docstatus != 0:
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		if self.get("deliverable_details"):
			rows = ungroup_items_from_ui(self.deliverable_details, "Work Order Deliverables")
			self.set("deliverables", [])
			for row in rows:
				self.append("deliverables", row)
		if self.get("receivable_details"):
			rows = ungroup_items_from_ui(self.receivable_details, "Work Order Receivables")
			self.set("receivables", [])
			for row in rows:
				self.append("receivables", row)
