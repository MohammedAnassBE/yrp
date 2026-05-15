import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate


class WorkOrder(Document):
	def save(self, *args, **kwargs):
		self.prepare_process_cost_links()
		return super().save(*args, **kwargs)

	def insert(self, *args, **kwargs):
		self.prepare_process_cost_links()
		return super().insert(*args, **kwargs)

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
		self.prepare_process_cost_links()

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

	def before_cancel(self):
		self.validate_no_submitted_downstream_documents()
		self.ignore_linked_doctypes = ("Delivery Challan", "Goods Received Note")

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	def validate_no_submitted_downstream_documents(self):
		delivery_challan = frappe.db.get_value(
			"Delivery Challan",
			{"work_order": self.name, "docstatus": 1},
			"name",
		)
		if delivery_challan:
			frappe.throw(
				_("Cannot cancel Work Order {0} because submitted Delivery Challan {1} exists.").format(
					self.name, delivery_challan
				)
			)

		goods_received_note = frappe.db.get_value(
			"Goods Received Note",
			{"against": "Work Order", "against_id": self.name, "docstatus": 1},
			"name",
		)
		if goods_received_note:
			frappe.throw(
				_("Cannot cancel Work Order {0} because submitted Goods Received Note {1} exists.").format(
					self.name, goods_received_note
				)
			)

	def prepare_process_cost_links(self):
		if self.flags.get("process_cost_links_prepared"):
			return
		if self.docstatus != 0 or getattr(self, "_action", None) == "cancel":
			return

		self.sync_vue_item_details()
		self.set_missing_dates()
		self.set_pending_quantities()
		self.set_receivable_process_costs()
		self.flags.process_cost_links_prepared = True

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

	def set_receivable_process_costs(self):
		if not self.get("receivables"):
			return
		if self.get("rework_type") == "No Cost":
			self.process_cost = None
			for row in self.receivables:
				row.process_cost = None
				row.cost = 0
				row.total_cost = 0
			return

		process_cost_name = self.get_receivable_process_cost()
		if not process_cost_name:
			if self.get("is_rework"):
				return
			frappe.throw(_("No process cost for {0}.").format(self.process_name))

		self.process_cost = process_cost_name
		process_cost = frappe.get_doc("Process Cost", process_cost_name)
		for row in self.receivables:
			rate = get_process_cost_rate(row.item_variant, row.qty, process_cost)
			row.process_cost = process_cost_name
			row.cost = round(rate, 3)
			row.total_cost = round(rate * flt(row.qty), 2)

	def get_receivable_process_cost(self):
		if not self.process_name or not self.item or not self.wo_date:
			return None

		meta = frappe.get_meta("Process Cost")
		filters = [
			["process_name", "=", self.process_name],
			["item", "=", self.item],
			["is_expired", "=", 0],
			["from_date", "<=", self.wo_date],
			["docstatus", "=", 1],
		]
		if self.supplier:
			filters.append(["supplier", "=", self.supplier])
		if meta.get_field("is_rework"):
			filters.append(["is_rework", "=", 1 if self.get("is_rework") else 0])

		from yrp.stock.dimensions import append_production_group_filters

		append_production_group_filters(filters, self, "Process Cost")

		if meta.get_field("workflow_state") and frappe.db.exists(
			"Workflow", {"document_type": "Process Cost", "is_active": 1}
		):
			filters.append(["workflow_state", "=", "Approved"])

		process_costs = frappe.get_all(
			"Process Cost",
			filters=filters,
			fields=["name", "to_date"],
			order_by="from_date desc, creation desc",
		)
		wo_date = getdate(self.wo_date)
		for process_cost in process_costs:
			if process_cost.to_date and getdate(process_cost.to_date) < wo_date:
				continue
			return process_cost.name
		return None

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


def get_process_cost_rate(item_variant, quantity, process_cost):
	attributes = get_variant_attributes(item_variant)
	cost_rows = sorted(
		process_cost.get("process_cost_values") or [],
		key=lambda row: flt(row.min_order_qty),
	)
	attribute_value = None
	if process_cost.depends_on_attribute:
		attribute_value = attributes.get(process_cost.attribute)

	rate = 0
	order_quantity = 0
	low_price = 0
	found = False
	for cost_row in cost_rows:
		if process_cost.depends_on_attribute and cost_row.attribute_value != attribute_value:
			continue
		min_order_qty = flt(cost_row.min_order_qty)
		if min_order_qty > flt(quantity):
			rate = flt(cost_row.price)
			found = True
			break
		if order_quantity <= min_order_qty:
			order_quantity = min_order_qty
			low_price = flt(cost_row.price)

	return round(rate if found else low_price, 3)


def get_variant_attributes(item_variant):
	rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": item_variant, "parenttype": "Item Variant"},
		fields=["attribute", "attribute_value"],
	)
	return {row.attribute: row.attribute_value for row in rows}
