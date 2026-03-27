# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe import _, utils
from frappe.model.document import Document


class ProcessCost(Document):
	def before_validate(self):
		self.validate_price_values()
		if not self.supplier and self.is_rework:
			frappe.throw(_("Please mention supplier if it is a Rework Process."))

	def before_submit(self):
		self.approved_by = frappe.session.user

		filters = [
			["item", "=", self.item],
			["process_name", "=", self.process_name],
			["docstatus", "=", 1],
			["name", "!=", self.name],
			["is_expired", "=", 0],
			["is_rework", "=", self.is_rework],
		]

		if self.supplier:
			filters.append(["supplier", "=", self.supplier])

		# Check for workflow state if workflow exists
		workflow_exists = frappe.db.exists("Workflow", {"document_type": "Process Cost", "is_active": 1})
		if workflow_exists:
			filters.append(["workflow_state", "=", "Approved"])

		process_cost_list = frappe.db.get_list(
			"Process Cost", filters=filters, pluck="name", order_by="from_date asc"
		)

		for pc_name in process_cost_list:
			doc = frappe.get_doc("Process Cost", pc_name)
			from_date = utils.get_datetime(self.from_date).date()

			if doc.from_date == from_date:
				frappe.throw(
					_("A Process Cost was found with the same From Date. "
					  "Please expire it before submitting this one.")
				)
			elif doc.from_date > from_date:
				to_date = utils.get_datetime(self.to_date).date() if self.to_date else None
				if not to_date or to_date >= doc.from_date:
					frappe.throw(
						_("An updated Process Cost for the same Item and Supplier exists from {0}. "
						  "Please set To Date less than that date or cancel the next one.").format(
							frappe.utils.format_date(doc.from_date)
						)
					)
			else:
				doc.to_date = utils.add_days(from_date, -1)
				doc.save()

	def validate_price_values(self):
		"""Ensure at least one price value row with price > 0."""
		has_price = False
		for row in self.process_cost_values:
			if row.price > 0:
				has_price = True
				break
		if not has_price:
			frappe.throw(_("At least one Process Cost Value must have a price greater than 0."))


def update_all_expired_process_cost():
	"""Cancel all expired Process Costs. Called by daily scheduler."""
	from frappe.utils import cint

	filters = [
		["to_date", "<", utils.nowdate()],
		["to_date", "is", "set"],
		["docstatus", "=", 1],
		["is_expired", "=", 0],
	]
	cost_list = frappe.db.get_all("Process Cost", filters=filters, pluck="name")
	workflow_exists = frappe.db.exists("Workflow", {"document_type": "Process Cost", "is_active": 1})

	for cost_name in cost_list:
		doc = frappe.get_doc("Process Cost", cost_name)
		if workflow_exists:
			_cancel_process_cost_via_workflow(doc)
		else:
			doc.is_expired = 1
			doc.cancel()
		doc.add_comment("Info", "Cancelled automatically due to expiry")

	if cost_list:
		frappe.db.commit()


def _cancel_process_cost_via_workflow(doc):
	"""Cancel a Process Cost through the active workflow."""
	workflow_name = frappe.db.get_value("Workflow", {"document_type": "Process Cost", "is_active": 1}, "name")
	if not workflow_name:
		doc.is_expired = 1
		doc.cancel()
		return

	workflow = frappe.get_doc("Workflow", workflow_name)
	from frappe.utils import cint
	cancel_states = [s.state for s in workflow.states if cint(s.doc_status) == 2]
	if "Expired" in cancel_states:
		cancel_states = ["Expired"]

	current_state = doc.get(workflow.workflow_state_field)
	if not current_state:
		doc.is_expired = 1
		doc.cancel()
		return

	for transition in workflow.transitions:
		if transition.state == current_state and transition.next_state in cancel_states:
			doc.set(workflow.workflow_state_field, transition.next_state)
			next_state = [d for d in workflow.states if d.state == transition.next_state][0]
			if next_state.update_field:
				doc.set(next_state.update_field, next_state.update_value)
			doc.is_expired = 1
			doc.cancel()
			doc.add_comment("Workflow", _(next_state.state))
			return

	doc.is_expired = 1
	doc.cancel()


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_item_attributes(doctype, txt, searchfield, start, page_len, filters):
	"""Generic: return all attributes for the item. Company apps override for process-specific filtering."""
	item_name = filters.get("item")
	if not item_name:
		return []

	item = frappe.get_doc("Item", item_name)
	attributes = [attribute.attribute for attribute in item.attributes]
	return [[a] for a in attributes if not txt or txt.lower() in a.lower()]


@frappe.whitelist()
def get_pc_attribute_values(item, attribute):
	"""Generic: return attribute values from Item Item Attribute Mapping."""
	if not item or not attribute:
		return []

	item_doc = frappe.get_doc("Item", item)
	for attr in item_doc.attributes:
		if attr.attribute == attribute and attr.mapping:
			mapping_doc = frappe.get_doc("Item Item Attribute Mapping", attr.mapping)
			return [
				{"price": 0, "min_order_qty": 0, "attribute_value": val.attribute_value}
				for val in mapping_doc.values
			]
	return []
