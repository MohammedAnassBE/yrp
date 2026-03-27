# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _, utils
from frappe.model.document import Document
from frappe.utils import cint, get_link_to_form


class ItemPrice(Document):
	def before_validate(self):
		validate_price_values(self.item_price_values)

	def before_submit(self):
		filters = [
			["item_name", "=", self.item_name],
			["docstatus", "=", 1],
		]
		if self.supplier is None:
			filters.append(["supplier", "is", "not set"])
		else:
			filters.append(["supplier", "=", self.supplier])

		# Check for workflow state if workflow exists
		workflow_exists = frappe.db.exists("Workflow", {"document_type": "Item Price", "is_active": 1})
		if workflow_exists:
			filters.append(["workflow_state", "=", "Approved"])

		price_list = frappe.db.get_list(
			"Item Price",
			filters=filters,
			pluck="name",
			order_by="from_date asc",
		)

		for price in price_list:
			doc = frappe.get_doc("Item Price", price)
			from_date = utils.get_datetime(self.from_date).date()
			if doc.from_date == from_date:
				frappe.throw(
					f"An Item Price was found with the same From Date. "
					f"Please expire it before submitting this one.\n"
					f"{get_link_to_form('Item Price', price)}"
				)
			elif doc.from_date > from_date:
				if not self.to_date or self.to_date >= doc.from_date:
					frappe.throw(
						f"An updated price list for the same Item and Supplier exists from "
						f"{frappe.utils.format_date(doc.from_date)}. Please set To Date less than "
						f"that date or cancel the next price.\n"
						f"{get_link_to_form('Item Price', price)}"
					)
			else:
				to_date = utils.add_days(from_date, -1)
				doc.to_date = to_date
				doc.save()

		self.set("approved_by", frappe.session.user)

	def validate_attribute_values(self, qty=0, attribute=None, attribute_value=None, get_lowest_moq_price=False, get_lead_time=False):
		if self.depends_on_attribute and (attribute is None or self.attribute != attribute or attribute_value is None):
			return None
		price_values = [
			[price.moq, price.price, price.lead_time, price.attribute_value]
			for price in self.item_price_values
		]
		return self.get_price_value(price_values, qty, attribute_value, get_lowest_moq_price, get_lead_time=get_lead_time)

	def get_price_value(self, item_price_values, qty=0, attribute_value=None, get_lowest_moq_price=False, get_lead_time=False):
		"""Get Item Price Value for the qty and attribute value (MOQ-based pricing)."""
		item_price_values = [p for p in item_price_values if p[3] == attribute_value]
		if not item_price_values:
			return None

		moq = -1
		rate = -1
		lead_time = -1
		for price in item_price_values:
			if moq < price[0] and (price[0] <= qty or get_lowest_moq_price):
				moq = price[0]
				rate = price[1]
				lead_time = price[2]

		if moq != -1:
			return lead_time if get_lead_time else rate
		return None


def validate_price_values(item_price_values):
	"""Validate no duplicate (moq, attribute_value) combinations."""
	values = []
	for price in item_price_values:
		unique_value = f"{price.moq}, {price.attribute_value}"
		if unique_value in values:
			frappe.throw(_("Duplicate entries found in price values."))
		values.append(unique_value)


@frappe.whitelist()
def get_active_price(item, supplier=None, raise_error=True):
	"""Get the current active Item Price for an item and optional supplier."""
	if not item:
		return None

	filters = {
		"item_name": item,
		"from_date": ["<=", utils.nowdate()],
		"docstatus": 1,
	}
	if supplier:
		filters["supplier"] = supplier

	lst = frappe.db.get_list("Item Price", filters={**filters, "to_date": ["is", "not set"]}) + \
	      frappe.db.get_list("Item Price", filters={**filters, "to_date": [">=", utils.nowdate()]})

	# Fallback: try without supplier filter
	if not lst and supplier:
		del filters["supplier"]
		lst = frappe.db.get_list("Item Price", filters={**filters, "to_date": ["is", "not set"]}) + \
		      frappe.db.get_list("Item Price", filters={**filters, "to_date": [">=", utils.nowdate()]})

	if not lst:
		if raise_error:
			frappe.throw(_("No Active Price List"))
		return None

	if supplier and len(lst) > 1:
		if raise_error:
			frappe.throw(_("Multiple Price Lists Found"))
		return None

	return frappe.get_doc("Item Price", lst[0].name)


def get_all_active_price(item=None, supplier=None):
	"""Get all active Item Price records for the given filters."""
	if item is None and supplier is None:
		return []

	filters = {
		"from_date": ["<=", utils.nowdate()],
		"docstatus": 1,
	}
	if item:
		filters["item_name"] = item
	if supplier:
		filters["supplier"] = supplier

	return frappe.db.get_list("Item Price", filters=filters)


@frappe.whitelist()
def get_item_supplier_price(item_detail, supplier=None):
	"""Get price for an item detail dict (with attributes and values)."""
	if item_detail is None or supplier is None:
		return None
	if isinstance(item_detail, str):
		item_detail = json.loads(item_detail)

	try:
		item_price = get_active_price(item_detail["name"], supplier)
	except Exception:
		return None

	if item_price is None:
		return None

	if item_price.depends_on_attribute:
		if item_price.attribute in item_detail.get("attributes", {}).keys():
			qty_sum = sum(v.get("qty", 0) for v in item_detail.get("values", {}).values())
			return item_price.validate_attribute_values(
				qty=qty_sum,
				attribute=item_price.attribute,
				attribute_value=item_detail["attributes"][item_price.attribute],
			)
		elif item_price.attribute == item_detail.get("primary_attribute"):
			prices = {}
			for qty_key, val in item_detail.get("values", {}).items():
				qty = val.get("qty", 0)
				price = item_price.validate_attribute_values(
					qty=qty, attribute=item_price.attribute, attribute_value=qty_key
				)
				prices[qty_key] = price
			return prices
		return None
	else:
		qty_sum = sum(v.get("qty", 0) for v in item_detail.get("values", {}).values())
		return item_price.validate_attribute_values(qty=qty_sum)


def get_item_variant_price(variant, variant_uom=None):
	"""Get price for an Item Variant with optional UOM conversion."""
	variant_doc = frappe.get_doc("Item Variant", variant)
	price_list = get_all_active_price(item=variant_doc.item)
	rate = None
	uom = None

	for price in price_list:
		item_price = frappe.get_doc("Item Price", price.name)
		uom = item_price.uom
		if item_price.depends_on_attribute:
			rate = item_price.validate_attribute_values(
				qty=0,
				attribute=item_price.attribute,
				attribute_value=variant_doc.get_attribute_value(item_price.attribute),
				get_lowest_moq_price=True,
			)
		else:
			rate = item_price.validate_attribute_values(qty=0, get_lowest_moq_price=True)

		if rate:
			break

	if not rate or not variant_uom:
		return rate
	if variant_uom == uom:
		return rate

	# UOM conversion using Item's conversion details
	item = frappe.get_doc("Item", variant_doc.item)
	for row in item.uom_conversion_details:
		if row.uom == variant_uom:
			return rate * row.conversion_factor
	return rate


def update_all_expired_item_price():
	"""Cancel all expired Item Prices. Called by daily scheduler."""
	filters = [
		["to_date", "<", utils.nowdate()],
		["to_date", "is", "set"],
		["docstatus", "=", 1],
	]
	price_list = frappe.db.get_all("Item Price", filters=filters, pluck="name")
	workflow_exists = frappe.db.exists("Workflow", {"document_type": "Item Price", "is_active": 1})

	for price in price_list:
		doc = frappe.get_doc("Item Price", price)
		if workflow_exists:
			_cancel_item_price_via_workflow(doc)
		else:
			doc.cancel()
		doc.add_comment("Info", "Cancelled automatically due to expiry")


def _cancel_item_price_via_workflow(doc):
	"""Cancel an Item Price through the active workflow."""
	workflow_name = frappe.db.get_value("Workflow", {"document_type": "Item Price", "is_active": 1}, "name")
	if not workflow_name:
		doc.cancel()
		return

	workflow = frappe.get_doc("Workflow", workflow_name)
	cancel_states = [s.state for s in workflow.states if cint(s.doc_status) == 2]
	if "Expired" in cancel_states:
		cancel_states = ["Expired"]

	current_state = doc.get(workflow.workflow_state_field)
	if not current_state:
		doc.cancel()
		return

	for transition in workflow.transitions:
		if transition.state == current_state and transition.next_state in cancel_states:
			doc.set(workflow.workflow_state_field, transition.next_state)
			next_state = [d for d in workflow.states if d.state == transition.next_state][0]
			if next_state.update_field:
				doc.set(next_state.update_field, next_state.update_value)
			doc.cancel()
			doc.add_comment("Workflow", _(next_state.state))
			return

	doc.cancel()
