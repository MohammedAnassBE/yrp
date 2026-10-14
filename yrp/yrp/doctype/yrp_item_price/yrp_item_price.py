# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt
import json

import frappe
from frappe import _, utils
from frappe.model.document import Document
from frappe.utils import cint, flt, get_link_to_form

from yrp.attribute_links import value as _attribute_value
from yrp.attribute_values import MASTER
from yrp.yrp.doctype.yrp_item.yrp_item import get_global_attribute_values


class YRPItemPrice(Document):
	def _bypass_attribute_link_validation(self):
		return self._is_reject_only_transition()

	def _validate_mandatory(self):
		if self._is_reject_only_transition():
			return
		return super()._validate_mandatory()

	def validate(self):
		if self._is_grandfathered() or self._is_reject_only_transition():
			return
		self._validate_price_data()

	def before_submit(self):
		try:
			item = frappe.get_doc("Item", self.item_name, for_update=True)
		except frappe.DoesNotExistError:
			item = None
		self._validate_price_data(item=item)
		price = frappe.qb.DocType("YRP Item Price")
		query = (
			frappe.qb.from_(price)
			.select(price.name, price.from_date, price.to_date)
			.where(price.item_name == self.item_name)
			.where(price.supplier == self.supplier)
			.where(price.docstatus == 1)
		)
		workflow_state_field = get_active_workflow_state_field("YRP Item Price")
		if workflow_state_field:
			query = query.where(price[workflow_state_field] == "Approved")
		price_list = query.orderby(price.from_date).for_update().run(as_dict=True)

		from_date = utils.getdate(self.from_date)
		overlapping_predecessors = []
		for other in price_list:
			other_from_date = utils.getdate(other.from_date)
			other_to_date = utils.getdate(other.to_date) if other.to_date else None
			if other_from_date == from_date:
				frappe.throw(
					f"An Item Price was found with the same From Date. "
					f"Please expire it before submitting this one.\n"
					f"{get_link_to_form('YRP Item Price', other.name)}"
				)
			if other_from_date > from_date:
				if not self.to_date or utils.getdate(self.to_date) >= other_from_date:
					frappe.throw(
						f"An updated price list for the same Item and Supplier exists from "
						f"{frappe.utils.format_date(other_from_date)}. Please set To Date less than "
						f"that date or cancel the next price.\n"
						f"{get_link_to_form('YRP Item Price', other.name)}"
					)
			elif not other_to_date or other_to_date >= from_date:
				overlapping_predecessors.append(other)

		if len(overlapping_predecessors) > 1:
			frappe.throw(
				_("Multiple approved Item Prices overlap this From Date. Expire the duplicates first.")
			)
		if overlapping_predecessors:
			predecessor = frappe.get_doc(
				"YRP Item Price", overlapping_predecessors[0].name, for_update=True
			)
			predecessor.to_date = utils.add_days(from_date, -1)
			predecessor.flags.closed_by_successor = True
			# The generic validator does a non-locking snapshot read after this locking reload.
			predecessor.flags.ignore_validate_update_after_submit = True
			predecessor.save()

		self.set("approved_by", frappe.session.user)

	def before_update_after_submit(self):
		before = self.get_doc_before_save()
		if before and before.to_date != self.to_date and not self.flags.get("closed_by_successor"):
			frappe.throw(_("To Date cannot be changed on an approved Item Price."))

	def _validate_price_data(self, item=None):
		if not self.supplier:
			frappe.throw(_("Supplier is required."))
		if self.to_date and utils.getdate(self.to_date) < utils.getdate(self.from_date):
			frappe.throw(_("To Date cannot be before From Date."))

		rows = self.get("item_price_values") or []
		if not rows:
			frappe.throw(_("At least one price tier is required."))
		for row in rows:
			if flt(row.moq) < 0:
				frappe.throw(_("Row {0}: MOQ cannot be negative.").format(row.idx))
			if flt(row.price) <= 0:
				frappe.throw(_("Row {0}: Price must be greater than zero.").format(row.idx))
			if flt(row.lead_time) < 0:
				frappe.throw(_("Row {0}: Lead Time cannot be negative.").format(row.idx))

		if item is None:
			item = frappe.db.get_value(
				"Item",
				self.item_name,
				["name", "stock_uom", "variant_of", "disabled"],
				as_dict=True,
			)
		if not item:
			frappe.throw(_("Item {0} does not exist.").format(self.item_name))
		if item.variant_of:
			frappe.throw(_("Item Prices must be configured on the template or plain Item, not a variant."))
		if item.disabled:
			frappe.throw(_("Item {0} is disabled.").format(self.item_name))
		if self.uom != item.stock_uom:
			frappe.throw(
				_(
					"This Item Price uses UOM {0}, but the Item's stock UOM is {1}. Re-enter the prices in a new Item Price."
				).format(frappe.bold(self.uom), frappe.bold(item.stock_uom))
			)

		if not self.depends_on_attribute:
			for row in rows:
				row.attribute_value = None
		else:
			item_doc = item if getattr(item, "doctype", None) == "Item" else frappe.get_doc(
				"Item", self.item_name
			)
			attribute_row = next(
				(row for row in item_doc.get("attributes") or [] if row.attribute == self.attribute),
				None,
			)
			if not attribute_row:
				frappe.throw(_("Price Attribute must be configured on the Item."))
			allowed_links = set()
			if attribute_row.get("mapping"):
				allowed_links = set(
					frappe.get_all(
						"YRP Item Item Attribute Mapping Value",
						filters={"parent": attribute_row.mapping},
						pluck="attribute_value",
					)
				)
			global_values = set(get_global_attribute_values(self.attribute))
			for row in rows:
				identity = frappe.db.get_value(
					MASTER,
					row.attribute_value,
					["attribute_name", "attribute_value"],
					as_dict=True,
				)
				allowed = bool(
					identity
					and identity.attribute_name == self.attribute
					and (
						row.attribute_value in allowed_links
						if allowed_links
						else identity.attribute_value in global_values
					)
				)
				if not allowed:
					frappe.throw(
						_("Row {0}: Attribute Value is not allowed for this Item.").format(row.idx)
					)

		validate_price_values(rows)

	def _is_grandfathered(self):
		before = self.get_doc_before_save()
		return bool(before and cint(before.docstatus) > 0 and cint(self.docstatus) > 0)

	def _is_reject_only_transition(self):
		before = self.get_doc_before_save()
		workflow_field = get_active_workflow_state_field("YRP Item Price")
		return bool(
			before
			and workflow_field
			and before.get(workflow_field) != "Rejected"
			and self.get(workflow_field) == "Rejected"
			and not self._price_data_changed(before)
		)

	def _price_data_changed(self, before):
		workflow_field = get_active_workflow_state_field("YRP Item Price")
		excluded = {workflow_field, "modified", "modified_by"}
		if _persisted_values(before, excluded) != _persisted_values(self, excluded):
			return True
		for table_field in self.meta.get_table_fields():
			before_rows = before.get(table_field.fieldname) or []
			current_rows = self.get(table_field.fieldname) or []
			if len(before_rows) != len(current_rows):
				return True
			for before_row, current_row in zip(before_rows, current_rows, strict=True):
				if _persisted_values(before_row, {"modified", "modified_by"}) != _persisted_values(
					current_row, {"modified", "modified_by"}
				):
					return True
		return False

	def validate_attribute_values(
		self, qty=0, attribute=None, attribute_value=None, get_lowest_moq_price=False, get_lead_time=False
	):
		if self.depends_on_attribute and (
			attribute is None or self.attribute != attribute or attribute_value is None
		):
			return None
		price_values = [
			[price.moq, price.price, price.lead_time, _attribute_value(price.attribute_value)]
			for price in self.item_price_values
		]
		return self.get_price_value(
			price_values, qty, attribute_value, get_lowest_moq_price, get_lead_time=get_lead_time
		)

	def get_price_value(
		self, item_price_values, qty=0, attribute_value=None, get_lowest_moq_price=False, get_lead_time=False
	):
		"""Get Item Price Value for the qty and attribute value (MOQ-based pricing)."""
		attribute_value = _normalize_attribute_value(attribute_value)
		item_price_values = [
			p for p in item_price_values if _normalize_attribute_value(p[3]) == attribute_value
		]
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


def _normalize_attribute_value(value):
	if value is None:
		return None
	if isinstance(value, str) and not value.strip():
		return None
	return value


def _persisted_values(doc, excluded):
	return tuple(
		(fieldname, doc.get(fieldname))
		for fieldname in doc.meta.get_valid_columns()
		if fieldname not in excluded
	)


def validate_price_values(item_price_values):
	"""Validate no duplicate (moq, attribute_value) combinations."""
	values = []
	for price in item_price_values:
		unique_value = f"{price.moq}, {_attribute_value(price.attribute_value)}"
		if unique_value in values:
			frappe.throw(_("Duplicate entries found in price values."))
		values.append(unique_value)


@frappe.whitelist()
def get_active_price(item, supplier=None, raise_error=True):
	"""Get the current active Item Price for an exact item and supplier."""
	if not item:
		return None
	if not supplier:
		if raise_error:
			frappe.throw(_("No Active Price List"))
		return None

	filters = {
		"item_name": item,
		"from_date": ["<=", utils.nowdate()],
		"docstatus": 1,
	}
	workflow_state_field = get_active_workflow_state_field("YRP Item Price")
	if workflow_state_field:
		filters[workflow_state_field] = "Approved"
	filters["supplier"] = supplier

	lst = frappe.db.get_list(
		"YRP Item Price", filters={**filters, "to_date": ["is", "not set"]}
	) + frappe.db.get_list("YRP Item Price", filters={**filters, "to_date": [">=", utils.nowdate()]})

	if not lst:
		if raise_error:
			frappe.throw(_("No Active Price List"))
		return None

	if len(lst) > 1:
		if raise_error:
			frappe.throw(_("Multiple Price Lists Found"))
		return None

	return frappe.get_doc("YRP Item Price", lst[0].name)


def get_all_active_price(item=None, supplier=None):
	"""Get all active Item Price records for the given filters."""
	if item is None and supplier is None:
		return []

	filters = {
		"from_date": ["<=", utils.nowdate()],
		"docstatus": 1,
	}
	workflow_state_field = get_active_workflow_state_field("YRP Item Price")
	if workflow_state_field:
		filters[workflow_state_field] = "Approved"
	if item:
		filters["item_name"] = item
	if supplier:
		filters["supplier"] = supplier

	return frappe.db.get_list("YRP Item Price", filters=filters)


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
	from yrp.yrp.doctype.yrp_item.yrp_item import get_parent_item

	variant_doc = frappe.get_doc("Item", variant)
	parent_item = get_parent_item(variant)
	price_list = get_all_active_price(item=parent_item)
	rate = None
	uom = None

	for price in price_list:
		item_price = frappe.get_doc("YRP Item Price", price.name)
		uom = item_price.uom
		if item_price.depends_on_attribute:
			attribute_value = next(
				(
					_attribute_value(row.attribute_value)
					for row in variant_doc.get("attributes") or []
					if row.attribute == item_price.attribute
				),
				None,
			)
			rate = item_price.validate_attribute_values(
				qty=0,
				attribute=item_price.attribute,
				attribute_value=attribute_value,
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
	item = frappe.get_doc("Item", parent_item)
	for row in item.uoms:
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
	price_list = frappe.db.get_all("YRP Item Price", filters=filters, pluck="name")
	workflow_exists = bool(get_active_workflow_state_field("YRP Item Price"))

	for price in price_list:
		try:
			doc = frappe.get_doc("YRP Item Price", price)
			if workflow_exists:
				_cancel_item_price_via_workflow(doc)
			else:
				doc.cancel()
			doc.add_comment("Info", "Cancelled automatically due to expiry")
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Failed to expire YRP Item Price {price}",
			)
			frappe.db.commit()


def _cancel_item_price_via_workflow(doc):
	"""Cancel an Item Price through the active workflow."""
	workflow_name = frappe.db.get_value(
		"Workflow", {"document_type": "YRP Item Price", "is_active": 1}, "name"
	)
	if not workflow_name:
		doc.cancel()
		return

	workflow = frappe.get_doc("Workflow", workflow_name)
	if not frappe.get_meta("YRP Item Price").get_field(workflow.workflow_state_field):
		doc.cancel()
		return

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
			next_state = next(d for d in workflow.states if d.state == transition.next_state)
			if next_state.update_field:
				doc.set(next_state.update_field, next_state.update_value)
			doc.cancel()
			doc.add_comment("Workflow", _(next_state.state))
			return

	doc.cancel()


def get_active_workflow_state_field(doctype):
	workflow_name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
	if not workflow_name:
		return None

	workflow_state_field = frappe.db.get_value("Workflow", workflow_name, "workflow_state_field")
	if workflow_state_field and frappe.get_meta(doctype).get_field(workflow_state_field):
		return workflow_state_field
	return None


ItemPrice = YRPItemPrice
