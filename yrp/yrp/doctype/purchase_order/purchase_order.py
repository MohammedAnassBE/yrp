import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, money_in_words, nowdate, today


class PurchaseOrder(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		self.set_onload(
			"item_details",
			group_items_for_ui(self.get("items") or [], "Purchase Order"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.set_default_terms()
		self.remove_blank_item_rows()
		self.set_missing_values()
		self.set_item_defaults()
		self.apply_item_prices(strict=False)
		self.calculate_totals()
		self.set_status()

	def validate(self):
		self.validate_items()
		self.calculate_totals()
		self.set_status()

	def before_save(self):
		if self.docstatus == 0:
			self.initialize_pending_quantities(force=True)
		self.calculate_totals()
		self.set_status()

	def before_submit(self):
		strict_price_validation = bool(
			frappe.db.get_single_value("YRP Settings", "enable_price_validation")
		)
		self.set_item_defaults()
		self.apply_item_prices(strict=strict_price_validation)
		self.validate_items()
		self.initialize_pending_quantities(force=True)
		self.calculate_totals()
		self.set_status()
		self.approved_by = frappe.session.user

	def before_cancel(self):
		for row in self.get("items") or []:
			if flt(row.received_quantity) > 0 or flt(row.pending_quantity) < flt(row.qty):
				frappe.throw(
					_(
						"Cannot cancel Purchase Order {0} because receipt has started. "
						"Cancel linked GRNs first."
					).format(self.name)
				)
			row.cancelled_quantity = flt(row.qty)
			row.pending_quantity = 0
		self.set_status(cancel=True)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		self.db_set("open_status", "Close", update_modified=False)

	def on_update_after_submit(self):
		self.set_status()
		self.db_set("status", self.status, update_modified=False)
		self.db_set("open_status", self.open_status, update_modified=False)

	def sync_vue_item_details(self):
		if self.docstatus != 0 or not self.get("item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		rows = ungroup_items_from_ui(self.item_details, "Purchase Order")
		self.set("items", [])
		for row in rows:
			self.append("items", row)

	def remove_blank_item_rows(self):
		if self.docstatus != 0:
			return
		self.set(
			"items",
			[
				row
				for row in self.get("items") or []
				if row.item_variant or flt(row.qty) or row.uom
			],
		)

	def set_missing_values(self):
		if not self.po_date:
			self.po_date = nowdate()
		if not self.open_status:
			self.open_status = "Open"

	def set_default_terms(self):
		# Prefill Terms and Condition once, on creation, only when empty — so the
		# user can change or remove it and the removal sticks on later saves.
		if self.is_new() and not self.terms_and_condition:
			from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

			self.terms_and_condition = get_default_terms("PO", self.supplier)

	def set_item_defaults(self):
		from yrp.stock.utils import get_conversion_factor

		for row in self.get("items") or []:
			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			default_uom = (
				frappe.get_cached_value("Item", parent_item, "default_unit_of_measure")
				if parent_item
				else None
			)
			row.uom = row.uom or default_uom
			conversion = get_conversion_factor(row.item_variant, row.uom) if row.uom else {}
			row.conversion_factor = flt(row.conversion_factor) or flt(
				conversion.get("conversion_factor")
			) or 1
			row.stock_uom = row.stock_uom or conversion.get("stock_uom") or row.uom or default_uom
			row.stock_qty = flt(row.qty) * flt(row.conversion_factor)
			row.delivery_date = row.delivery_date or self.expected_delivery_date
			self.calculate_row_amount(row)

	def apply_item_prices(self, strict=False):
		if not self.supplier or not self.get("items"):
			return
		validate_price_details(self.get("items") or [], self.supplier, strict=strict)
		for row in self.get("items") or []:
			self.calculate_row_amount(row)

	def validate_items(self):
		if not self.supplier:
			frappe.throw(_("Supplier is required."))
		if not self.delivery_warehouse:
			frappe.throw(_("Delivery Warehouse is required."))
		if not self.get("items"):
			frappe.throw(_("At least one item is required."))

		for row in self.items:
			if not row.item_variant:
				frappe.throw(_("Row {0}: Item Variant is required.").format(row.idx))
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero.").format(row.idx))
			if not row.uom:
				frappe.throw(_("Row {0}: UOM is required.").format(row.idx))
			if flt(row.rate) < 0:
				frappe.throw(_("Row {0}: Rate cannot be negative.").format(row.idx))
			if flt(row.discount_percentage) < 0 or flt(row.discount_percentage) > 100:
				frappe.throw(
					_("Row {0}: Discount Percentage must be between 0 and 100.").format(row.idx)
				)

	def initialize_pending_quantities(self, force=False):
		for row in self.get("items") or []:
			if force or row.is_new():
				row.pending_quantity = flt(row.qty)
				row.received_quantity = 0
				row.cancelled_quantity = 0
			else:
				row.pending_quantity = flt(row.pending_quantity) or flt(row.qty)
				row.received_quantity = flt(row.received_quantity)
				row.cancelled_quantity = flt(row.cancelled_quantity)

	def calculate_row_amount(self, row):
		base_amount = flt(row.qty) * flt(row.rate)
		discount_amount = base_amount * flt(row.discount_percentage) / 100
		tax_rate = flt(row.tax)
		tax_amount = (base_amount - discount_amount) * tax_rate / 100
		row.amount = base_amount
		row.discount_amount = discount_amount
		row.tax_amount = tax_amount
		row.total_amount = base_amount - discount_amount + tax_amount

	def calculate_totals(self):
		self.total_qty = sum(flt(row.qty) for row in self.get("items") or [])
		self.total_stock_qty = sum(flt(row.stock_qty) for row in self.get("items") or [])
		self.total = sum(flt(row.amount) for row in self.get("items") or [])
		self.total_discount = sum(flt(row.discount_amount) for row in self.get("items") or [])
		self.total_tax = sum(flt(row.tax_amount) for row in self.get("items") or [])
		self.grand_total = sum(flt(row.total_amount) for row in self.get("items") or [])
		self.in_words = money_in_words(self.grand_total) if flt(self.grand_total) else ""

	def set_status(self, cancel=False):
		if cancel or self.docstatus == 2:
			self.status = "Cancelled"
			self.open_status = "Close"
			return
		if self.open_status == "Closed":
			self.open_status = "Close"
		if self.open_status == "Close":
			self.status = "Closed"
			return
		if self.docstatus == 0:
			self.status = "Draft"
			return

		self.status = self.get_fulfillment_status()

	def get_fulfillment_status(self):
		total_qty = sum(flt(row.qty) for row in self.get("items") or [])
		received_qty = sum(flt(row.received_quantity) for row in self.get("items") or [])
		cancelled_qty = sum(flt(row.cancelled_quantity) for row in self.get("items") or [])
		pending_qty = sum(flt(row.pending_quantity) for row in self.get("items") or [])

		if self.get("items") and all(
			flt(row.cancelled_quantity) >= flt(row.qty) - 0.0001
			for row in self.get("items") or []
		):
			return "Cancelled"
		if cancelled_qty > 0:
			return "Partially Cancelled"
		if total_qty and (pending_qty <= 0.0001 or received_qty >= total_qty - 0.0001):
			return "Received"
		if received_qty > 0 or (total_qty and pending_qty < total_qty - 0.0001):
			return "Partially Received"
		return "Ordered"

	def set_open_status(self, close=True):
		if self.docstatus == 2:
			frappe.throw(_("Cannot close a cancelled Purchase Order."))
		self.open_status = "Close" if close else "Open"
		self.set_status()


def validate_price_details(rows, supplier=None, strict=True):
	if not rows:
		return

	from yrp.yrp.doctype.item_price.item_price import get_active_price

	rows_by_item = defaultdict(list)
	for row in rows:
		parent_item = _get_parent_item(row.item_variant)
		if parent_item:
			rows_by_item[parent_item].append(row)

	for parent_item, item_rows in rows_by_item.items():
		item_price = get_active_price(parent_item, supplier, raise_error=strict)
		if not item_price:
			continue
		if item_price.depends_on_attribute:
			_apply_attribute_price(item_price, item_rows, strict=strict)
		else:
			qty = sum(flt(row.qty) for row in item_rows)
			price = item_price.validate_attribute_values(qty=qty)
			if price is None:
				if strict:
					frappe.throw(
						_("No matching price slab for {0} at quantity {1}.").format(
							parent_item, flt(qty)
						)
					)
				continue
			for row in item_rows:
				row.rate = flt(price)
				row.tax = item_price.tax


def _apply_attribute_price(item_price, rows, strict=True):
	rows_by_value = defaultdict(list)
	for row in rows:
		attribute_value = _get_variant_attribute_value(row.item_variant, item_price.attribute)
		if attribute_value:
			rows_by_value[attribute_value].append(row)
		elif strict:
			frappe.throw(
				_("Item Variant {0} does not have attribute {1}.").format(
					row.item_variant, item_price.attribute
				)
			)

	for attribute_value, value_rows in rows_by_value.items():
		qty = sum(flt(row.qty) for row in value_rows)
		price = item_price.validate_attribute_values(
			qty=qty,
			attribute=item_price.attribute,
			attribute_value=attribute_value,
		)
		if price is None:
			if strict:
				frappe.throw(
					_("No matching price slab for {0} {1} at quantity {2}.").format(
						item_price.attribute, attribute_value, flt(qty)
					)
				)
			continue
		for row in value_rows:
			row.rate = flt(price)
			row.tax = item_price.tax


def _get_parent_item(item_variant):
	return frappe.get_cached_value("Item Variant", item_variant, "item")


def _get_variant_attribute_value(item_variant, attribute):
	variant = frappe.get_doc("Item Variant", item_variant)
	for row in variant.get("attributes") or []:
		if row.attribute == attribute:
			return row.attribute_value
	return None


@frappe.whitelist()
def set_open_status(purchase_order, open_status):
	frappe.only_for(("Purchase Manager", "System Manager"))
	requested_open_status = open_status
	open_status = _normalize_open_status(open_status)
	if not open_status:
		frappe.throw(_("Invalid open status {0}.").format(requested_open_status))

	doc = frappe.get_doc("Purchase Order", purchase_order)
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted Purchase Orders can be closed or reopened."))

	doc.set_open_status(close=open_status == "Close")
	_update_status_fields(doc)
	return {"open_status": doc.open_status, "status": doc.status}


@frappe.whitelist()
def refresh_status(purchase_order):
	doc = frappe.get_doc("Purchase Order", purchase_order)
	doc.set_status()
	_update_status_fields(doc)
	return doc.status


@frappe.whitelist()
def close_purchase_order(purchase_order):
	return set_open_status(purchase_order, "Close")["open_status"]


@frappe.whitelist()
def reopen_purchase_order(purchase_order):
	return set_open_status(purchase_order, "Open")["open_status"]


@frappe.whitelist()
def close_or_open_purchase_orders(names, close):
	if not frappe.has_permission("Purchase Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if isinstance(names, str):
		names = json.loads(names)
	if isinstance(close, str):
		close = close.lower() == "true"

	for name in names:
		if close:
			close_purchase_order(name)
		else:
			reopen_purchase_order(name)


def close_received_po():
	purchase_orders = frappe.get_all(
		"Purchase Order",
		filters=[
			["status", "=", "Received"],
			["open_status", "=", "Open"],
			["docstatus", "=", 1],
			["modified", "<", add_days(today(), -8)],
		],
		pluck="name",
	)
	for name in purchase_orders:
		doc = frappe.get_doc("Purchase Order", name)
		doc.set_open_status(close=True)
		_update_status_fields(doc)


def _normalize_open_status(open_status):
	if open_status == "Closed":
		return "Close"
	if open_status in ("Open", "Close"):
		return open_status
	return None


def _update_status_fields(doc):
	update_modified = False
	if doc.open_status != doc.get_db_value("open_status"):
		doc.db_set("open_status", doc.open_status, update_modified=True)
		update_modified = True
	if doc.status != doc.get_db_value("status"):
		doc.db_set("status", doc.status, update_modified=not update_modified)


@frappe.whitelist()
def get_item_price_for_ui(item_detail, supplier=None):
	if not supplier:
		return None
	if isinstance(item_detail, str):
		item_detail = json.loads(item_detail or "{}")
	if not item_detail or not item_detail.get("name"):
		return None

	from yrp.yrp.doctype.item_price.item_price import get_active_price

	item_price = get_active_price(item_detail.get("name"), supplier, raise_error=False)
	if not item_price:
		return None

	result = {
		"tax": item_price.tax,
		"tax_rate": flt(item_price.tax),
	}
	if item_price.depends_on_attribute:
		result.update(_get_attribute_price_for_ui(item_price, item_detail))
	else:
		qty = sum(flt(value.get("qty")) for value in (item_detail.get("values") or {}).values())
		result["rate"] = item_price.validate_attribute_values(qty=qty)
	return result


def _get_attribute_price_for_ui(item_price, item_detail):
	values = item_detail.get("values") or {}
	attributes = item_detail.get("attributes") or {}
	if item_price.attribute in attributes:
		qty = sum(flt(value.get("qty")) for value in values.values())
		return {
			"rate": item_price.validate_attribute_values(
				qty=qty,
				attribute=item_price.attribute,
				attribute_value=attributes[item_price.attribute],
			)
		}

	if item_price.attribute == item_detail.get("primary_attribute"):
		return {
			"rates": {
				key: item_price.validate_attribute_values(
					qty=flt(value.get("qty")),
					attribute=item_price.attribute,
					attribute_value=key,
				)
				for key, value in values.items()
				if flt(value.get("qty")) > 0
			}
		}

	return {"rate": None}

