import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class WorkOrderCorrection(Document):
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

	def validate(self):
		self.sync_vue_item_details()
		docstatus, open_status = frappe.db.get_value(
			"Work Order", self.work_order, ["docstatus", "open_status"]
		) or (None, None)
		if docstatus != 1:
			frappe.throw(_("Work Order {0} must be submitted.").format(self.work_order))
		if open_status == "Close":
			frappe.throw(_("Work Order {0} is closed.").format(self.work_order))
		if not (self.get("deliverables") or self.get("receivables")):
			frappe.throw(_("Add at least one deliverable or receivable row."))
		self.set_pending_quantities()
		self.set_status()

	def set_pending_quantities(self):
		if self.docstatus != 0:
			return
		for row in self.get("deliverables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty
		for row in self.get("receivables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty

	def before_submit(self):
		self.set_pending_quantities()
		for row in (self.get("deliverables") or []) + (self.get("receivables") or []):
			if not flt(row.pending_quantity):
				row.pending_quantity = row.qty

	def on_submit(self):
		self.set_status()
		self.db_set("status", self.status, update_modified=False)

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
			return
		if self.docstatus == 2:
			self.status = "Cancelled"
			return

		status = "Submitted"

		total_deliverable_qty = sum(flt(row.qty) for row in self.get("deliverables") or [])
		# Per-row floor at 0 (2026-07-10, same as Work Order.set_status): excess
		# delivery drives a row's pending negative — a raw sum lets it mask
		# another row's genuinely-owed pending.
		total_delivery_pending = sum(max(flt(row.pending_quantity), 0) for row in self.get("deliverables") or [])
		if total_deliverable_qty:
			if total_delivery_pending <= 0:
				status = "Fully Delivered"
			elif total_delivery_pending < total_deliverable_qty:
				status = "Partially Delivered"

		total_receivable_qty = sum(flt(row.qty) for row in self.get("receivables") or [])
		total_received_pending = sum(max(flt(row.pending_quantity), 0) for row in self.get("receivables") or [])
		if total_receivable_qty:
			received_qty = total_receivable_qty - total_received_pending
			if received_qty > 0:
				status = "Fully Received" if total_received_pending <= 0 else "Partially Received"

		self.status = status

	def update_status(self):
		self.set_status()
		self.db_set("status", self.status, update_modified=False)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
