from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime

from yrp.yrp.doctype.delivery_challan.delivery_challan import (
	_apply_dimension_values_to_rows,
	_copy_header_dimensions_to_items,
	_copy_production_group_dimensions_from_source,
	_get_production_group_dimensions,
	_get_warehouse_for_supplier,
	_normal_json,
	_sle_base,
	_update_work_order_status,
)


class GoodsReceivedNote(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		rows = self.get("items") or []
		if self.docstatus == 0 and self.against == "Work Order" and self.against_id and rows:
			from yrp.stock.dimensions import apply_dimension_defaults

			wo = frappe.get_doc("Work Order", self.against_id)
			rows = _pending_receivable_rows(wo, existing_rows=rows)
			_apply_dimension_values_to_rows(rows, _get_production_group_dimensions(wo))
			apply_dimension_defaults(rows)
		elif self.docstatus == 0 and self.against == "Purchase Order" and self.against_id and rows:
			from yrp.stock.dimensions import apply_dimension_defaults

			po = frappe.get_doc("Purchase Order", self.against_id)
			rows = _pending_purchase_order_rows(po, existing_rows=rows)
			_apply_dimension_values_to_rows(rows, _get_production_group_dimensions(po))
			apply_dimension_defaults(rows)

		self.set_onload(
			"item_details",
			group_items_for_ui(rows, "Goods Received Note"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.set_missing_values()
		self.apply_dimensions()
		self.set_item_defaults()

	def validate(self):
		self.validate_against()
		self.validate_items()
		self.calculate_totals()

	def before_submit(self):
		self.validate_against()
		self.validate_source_pending()

	def on_submit(self):
		self.update_source_pending()
		self.make_stock_ledger_entries()

	def before_cancel(self):
		self.validate_no_purchase_invoice()
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		self.make_stock_ledger_entries(cancel=True)
		self.update_source_pending(cancel=True)

	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.posting_time:
			self.posting_time = nowtime()
		if not self.against_id:
			return

		if self.against == "Work Order":
			wo = frappe.get_cached_doc("Work Order", self.against_id)
			self.process_name = self.process_name or wo.process_name
			self.item = self.item or wo.item
			self.production_detail = self.production_detail or wo.production_detail
			self.supplier = self.supplier or wo.supplier
			self.delivery_location = self.delivery_location or wo.delivery_location
			self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(wo.supplier)
			self.to_warehouse = self.to_warehouse or _get_warehouse_for_supplier(wo.delivery_location)
			_copy_production_group_dimensions_from_source(self, wo)
		elif self.against == "Purchase Order":
			po = frappe.get_cached_doc("Purchase Order", self.against_id)
			self.supplier = self.supplier or po.supplier
			self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(po.supplier)
			self.to_warehouse = self.to_warehouse or po.delivery_warehouse
			_copy_production_group_dimensions_from_source(self, po)

	def sync_vue_item_details(self):
		if self.docstatus != 0 or not self.get("item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		rows = ungroup_items_from_ui(self.item_details, "Goods Received Note")
		self.set("items", [])
		for row in rows:
			self.append("items", row)

	def apply_dimensions(self):
		_copy_header_dimensions_to_items(self)
		from yrp.stock.dimensions import apply_dimension_defaults

		apply_dimension_defaults(self.get("items") or [])

	def set_item_defaults(self):
		wo = frappe.get_doc("Work Order", self.against_id) if self.against == "Work Order" and self.against_id else None
		delivery_challan = (
			frappe.get_doc("Delivery Challan", self.delivery_challan)
			if self.delivery_challan else None
		)
		for row in self.get("items") or []:
			row.conversion_factor = flt(row.conversion_factor) or 1
			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			default_uom = frappe.get_cached_value("Item", parent_item, "default_unit_of_measure") if parent_item else None
			row.uom = row.uom or default_uom
			row.stock_uom = row.stock_uom or row.uom or default_uom
			row.stock_qty = flt(row.quantity) * flt(row.conversion_factor)
			if wo:
				row.rate = get_work_order_grn_rate(wo, delivery_challan, row)
				row.amount = flt(row.stock_qty or row.quantity) * flt(row.rate)
			else:
				row.amount = flt(row.quantity) * flt(row.rate)

	def validate_against(self):
		if self.against not in ("Work Order", "Purchase Order"):
			frappe.throw(_("GRN against {0} is not available.").format(self.against))
		if not self.against_id:
			frappe.throw(_("Against ID is required."))
		docstatus, open_status = frappe.db.get_value(self.against, self.against_id, ["docstatus", "open_status"])
		if docstatus != 1:
			frappe.throw(_("{0} {1} must be submitted.").format(self.against, self.against_id))
		if open_status == "Close":
			frappe.throw(_("{0} {1} is closed.").format(self.against, self.against_id))
		if self.delivery_challan and self.against != "Work Order":
			frappe.throw(_("Delivery Challan can only be used with Work Order GRN."))
		if self.delivery_challan:
			dc_work_order, dc_docstatus = frappe.db.get_value(
				"Delivery Challan", self.delivery_challan, ["work_order", "docstatus"]
			)
			if dc_docstatus != 1:
				frappe.throw(_("Delivery Challan {0} must be submitted.").format(self.delivery_challan))
			if dc_work_order != self.against_id:
				frappe.throw(_("Delivery Challan must belong to the same Work Order."))

	def validate_items(self):
		if not self.get("items"):
			frappe.throw(_("At least one item is required."))
		if self.against == "Work Order" and not self.from_warehouse:
			frappe.throw(_("From Warehouse is required."))
		if not self.to_warehouse:
			frappe.throw(_("To Warehouse is required."))
		if self.from_warehouse and self.from_warehouse == self.to_warehouse:
			frappe.throw(_("From Warehouse and To Warehouse must be different."))
		for row in self.items:
			if not row.item_variant:
				frappe.throw(_("Row {0}: Item Variant is required.").format(row.idx))
			if flt(row.quantity) <= 0:
				frappe.throw(_("Row {0}: Quantity must be greater than zero.").format(row.idx))
			if not row.uom:
				frappe.throw(_("Row {0}: UOM is required.").format(row.idx))

	def calculate_totals(self):
		self.total_received_quantity = sum(flt(row.quantity) for row in self.items)
		self.total = sum(flt(row.amount) for row in self.items)

	def validate_source_pending(self):
		if self.against == "Purchase Order":
			self.validate_against_purchase_order_pending()
			return
		self.validate_against_work_order_pending()

	def validate_no_purchase_invoice(self):
		purchase_invoice = self.get("purchase_invoice_name")
		if _is_active_purchase_invoice(purchase_invoice):
			_throw_purchase_invoice_link_error(self.name, purchase_invoice)

		purchase_invoice = _get_linked_purchase_invoice_from_child_table(self.name)
		if purchase_invoice:
			_throw_purchase_invoice_link_error(self.name, purchase_invoice)

	def validate_against_work_order_pending(self):
		wo = frappe.get_doc("Work Order", self.against_id)
		totals_by_receivable = defaultdict(float)
		receivable_by_name = {}
		for row in self.items:
			target = _find_matching_receivable(wo.receivables, row)
			if not target:
				frappe.throw(
					_("Row {0}: no matching Work Order Receivable found for {1}.").format(
						row.idx, row.item_variant
					)
				)
			totals_by_receivable[target.name] += flt(row.quantity)
			receivable_by_name[target.name] = target
			row.ref_doctype = "Work Order Receivables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

		for receivable_name, total_qty in totals_by_receivable.items():
			target = receivable_by_name[receivable_name]
			if flt(target.pending_quantity) + 0.0001 < total_qty:
				frappe.throw(
					_("Received qty {0} exceeds pending qty {1} for {2}.").format(
						flt(total_qty), flt(target.pending_quantity), target.item_variant
					)
				)

	def validate_against_purchase_order_pending(self):
		po = frappe.get_doc("Purchase Order", self.against_id)
		totals_by_item = defaultdict(float)
		item_by_name = {}
		for row in self.items:
			target = _find_matching_purchase_order_item(po.items, row)
			if not target:
				frappe.throw(
					_("Row {0}: no matching Purchase Order Item found for {1}.").format(
						row.idx, row.item_variant
					)
				)
			totals_by_item[target.name] += flt(row.quantity)
			item_by_name[target.name] = target
			row.ref_doctype = "Purchase Order Item"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

		for item_name, total_qty in totals_by_item.items():
			target = item_by_name[item_name]
			if flt(target.pending_quantity) + 0.0001 < total_qty:
				frappe.throw(
					_("Received qty {0} exceeds pending qty {1} for {2}.").format(
						flt(total_qty), flt(target.pending_quantity), target.item_variant
					)
				)

	def update_source_pending(self, cancel=False):
		if self.against == "Purchase Order":
			self.update_purchase_order_items(cancel=cancel)
			return
		self.update_work_order_receivables(cancel=cancel)

	def update_work_order_receivables(self, cancel=False):
		wo = frappe.get_doc("Work Order", self.against_id)
		changed = False
		for row in self.items:
			target = _find_matching_receivable(wo.receivables, row)
			if not target:
				continue
			qty = flt(row.quantity)
			pending = flt(target.pending_quantity) + qty if cancel else flt(target.pending_quantity) - qty
			target.db_set("pending_quantity", flt(pending), update_modified=False)
			changed = True

		if changed:
			_update_work_order_status(self.against_id)

	def update_purchase_order_items(self, cancel=False):
		po = frappe.get_doc("Purchase Order", self.against_id)
		changed = False
		for row in self.items:
			target = _find_matching_purchase_order_item(po.items, row)
			if not target:
				continue
			qty = flt(row.quantity)
			pending = flt(target.pending_quantity) + qty if cancel else flt(target.pending_quantity) - qty
			received = flt(target.received_quantity) - qty if cancel else flt(target.received_quantity) + qty
			target.db_set("pending_quantity", flt(pending), update_modified=False)
			target.db_set("received_quantity", flt(received), update_modified=False)
			changed = True

		if changed:
			_update_purchase_order_status(self.against_id)

	def make_stock_ledger_entries(self, cancel=False):
		from yrp.stock.stock_ledger import make_sl_entries

		entries = []
		for row in self.items:
			qty = flt(row.stock_qty) or flt(row.quantity)
			if qty <= 0:
				continue
			base = _sle_base(self, row)
			entries.append({
				**base,
				"warehouse": self.to_warehouse,
				"qty": qty,
				"rate": flt(row.rate),
			})

		make_sl_entries(entries, cancel=cancel)


def _find_matching_receivable(rows, source_row):
	if source_row.get("ref_doctype") == "Work Order Receivables" and source_row.get("ref_docname"):
		for row in rows:
			if row.name == source_row.get("ref_docname"):
				return row
	for row in rows:
		if row.item_variant != source_row.get("item_variant"):
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(source_row.get("set_combination")):
			return row
	return None


def _find_matching_purchase_order_item(rows, source_row):
	if source_row.get("ref_doctype") == "Purchase Order Item" and source_row.get("ref_docname"):
		for row in rows:
			if row.name == source_row.get("ref_docname"):
				return row
	for row in rows:
		if row.item_variant != source_row.get("item_variant"):
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(source_row.get("set_combination")):
			return row
	return None


def _is_active_purchase_invoice(purchase_invoice):
	if not purchase_invoice or not frappe.db.exists("DocType", "Purchase Invoice"):
		return False
	docstatus = frappe.db.get_value("Purchase Invoice", purchase_invoice, "docstatus")
	return docstatus is not None and int(docstatus) != 2


def _get_linked_purchase_invoice_from_child_table(grn_name):
	if not frappe.db.exists("DocType", "Purchase Invoice GRN"):
		return None

	grn_field = _get_purchase_invoice_grn_field()
	if not grn_field:
		return None

	for row in frappe.get_all(
		"Purchase Invoice GRN",
		filters={grn_field: grn_name, "parenttype": "Purchase Invoice"},
		fields=["parent"],
		limit=20,
	):
		if _is_active_purchase_invoice(row.parent):
			return row.parent
	return None


def _get_purchase_invoice_grn_field():
	meta = frappe.get_meta("Purchase Invoice GRN")
	for fieldname in ("grn", "goods_received_note"):
		if meta.has_field(fieldname):
			return fieldname
	return None


def _throw_purchase_invoice_link_error(grn_name, purchase_invoice):
	frappe.throw(
		_("Cannot cancel Goods Received Note {0} because Purchase Invoice {1} exists.").format(
			grn_name, purchase_invoice
		)
	)


def get_work_order_grn_rate(wo, delivery_challan, row):
	process_rate = 0
	target = _find_matching_receivable(wo.receivables, row)
	if target:
		process_rate = flt(target.cost)
		row.ref_doctype = "Work Order Receivables"
		row.ref_docname = target.name
		row.pending_quantity = target.pending_quantity
	material_rate = get_delivery_challan_material_rate(delivery_challan, row)
	return flt(material_rate) + flt(process_rate)


def get_delivery_challan_material_rate(delivery_challan, row):
	if not delivery_challan:
		return 0
	dc_items = delivery_challan.get("items") or []
	matching_variant_rows = [
		dc_row for dc_row in dc_items
		if dc_row.item_variant == row.get("item_variant")
		and _normal_json(dc_row.get("set_combination")) == _normal_json(row.get("set_combination"))
	]
	if matching_variant_rows:
		return _weighted_delivery_rate(matching_variant_rows)

	same_item_rows = [
		dc_row for dc_row in dc_items
		if dc_row.item_variant == row.get("item_variant")
	]
	if same_item_rows:
		return _weighted_delivery_rate(same_item_rows)

	return _weighted_delivery_rate(dc_items)


def _weighted_delivery_rate(rows):
	total_qty = 0
	total_value = 0
	for row in rows or []:
		qty = flt(row.get("stock_qty")) or flt(row.get("delivered_quantity") or row.get("qty"))
		if qty <= 0:
			continue
		rate = flt(row.get("valuation_rate") or row.get("rate"))
		total_qty += qty
		total_value += qty * rate
	if not total_qty:
		return 0
	return total_value / total_qty


def _update_purchase_order_status(purchase_order):
	from yrp.yrp.doctype.purchase_order.purchase_order import _update_status_fields

	po = frappe.get_doc("Purchase Order", purchase_order)
	po.set_status()
	_update_status_fields(po)


@frappe.whitelist()
def get_work_order_defaults(work_order):
	from yrp.stock.save_stock_items import group_items_for_ui
	from yrp.stock.dimensions import apply_dimension_defaults

	wo = frappe.get_doc("Work Order", work_order)
	items = _pending_receivable_rows(wo)
	dimensions = _get_production_group_dimensions(wo)
	_apply_dimension_values_to_rows(items, dimensions)
	apply_dimension_defaults(items)
	defaults = {
		"process_name": wo.process_name,
		"item": wo.item,
		"production_detail": wo.production_detail,
		"supplier": wo.supplier,
		"delivery_location": wo.delivery_location,
		"from_warehouse": _get_warehouse_for_supplier(wo.supplier),
		"to_warehouse": _get_warehouse_for_supplier(wo.delivery_location),
		"items": items,
		"item_details": group_items_for_ui(items, "Goods Received Note"),
	}
	defaults.update(dimensions)
	return defaults


@frappe.whitelist()
def get_purchase_order_defaults(purchase_order):
	from yrp.stock.dimensions import apply_dimension_defaults
	from yrp.stock.save_stock_items import group_items_for_ui

	po = frappe.get_doc("Purchase Order", purchase_order)
	items = _pending_purchase_order_rows(po)
	dimensions = _get_production_group_dimensions(po)
	_apply_dimension_values_to_rows(items, dimensions)
	apply_dimension_defaults(items)
	defaults = {
		"supplier": po.supplier,
		"from_warehouse": _get_warehouse_for_supplier(po.supplier),
		"to_warehouse": po.delivery_warehouse,
		"items": items,
		"item_details": group_items_for_ui(items, "Goods Received Note"),
	}
	defaults.update(dimensions)
	return defaults


def _pending_receivable_rows(wo, existing_rows=None):
	received_types, default_received_type = _get_received_type_options(existing_rows)
	existing_quantities = (
		_existing_receipt_quantities(wo, existing_rows, default_received_type)
		if existing_rows is not None
		else {}
	)
	rows = []
	for row in wo.get("receivables") or []:
		pending = flt(row.pending_quantity)
		if pending <= 0:
			continue
		base_row_index = row.row_index if row.row_index not in (None, "") else row.idx - 1
		for received_type in received_types:
			key = _receipt_split_key(row.name, row.item_variant, received_type)
			if existing_rows is None:
				quantity = pending if not received_type or received_type == default_received_type else 0
			else:
				quantity = existing_quantities.get(key, 0)
			out = {
				"item_variant": row.item_variant,
				"quantity": quantity,
				"uom": row.uom,
				"pending_quantity": pending,
				"ref_doctype": "Work Order Receivables",
				"ref_docname": row.name,
				"table_index": row.table_index,
				"row_index": (
					f"{base_row_index}::{received_type}"
					if received_type else base_row_index
				),
				"set_combination": row.set_combination,
				"rate": row.cost,
			}
			if received_type:
				out["received_type"] = received_type
			rows.append(out)
	return rows


def _pending_purchase_order_rows(po, existing_rows=None):
	existing_quantities = (
		_existing_purchase_receipt_quantities(po, existing_rows)
		if existing_rows is not None
		else {}
	)
	rows = []
	for row in po.get("items") or []:
		pending = flt(row.pending_quantity)
		if pending <= 0:
			continue
		quantity = existing_quantities.get(row.name, pending)
		rows.append({
			"item_variant": row.item_variant,
			"quantity": quantity,
			"uom": row.uom,
			"stock_uom": row.stock_uom,
			"conversion_factor": row.conversion_factor,
			"stock_qty": flt(quantity) * flt(row.conversion_factor or 1),
			"pending_quantity": pending,
			"ref_doctype": "Purchase Order Item",
			"ref_docname": row.name,
			"table_index": row.table_index,
			"row_index": row.row_index,
			"set_combination": row.set_combination,
			"rate": row.rate,
		})
	return rows


def _existing_purchase_receipt_quantities(po, existing_rows):
	quantities = defaultdict(float)
	for row in existing_rows or []:
		target = _find_matching_purchase_order_item(po.items, row)
		if not target:
			continue
		quantities[target.name] += flt(row.get("quantity"))
	return quantities


def _get_received_type_options(existing_rows=None):
	from yrp.stock.dimensions import get_dimension_fieldnames

	if "received_type" not in get_dimension_fieldnames():
		return [None], None

	default_received_type = frappe.db.get_single_value(
		"YRP Stock Settings", "default_received_type"
	)
	received_type_rows = frappe.get_all(
		"Received Type",
		fields=["name", "is_default"],
		order_by="is_default desc, name asc",
	)
	received_types = [row.name for row in received_type_rows]
	if not default_received_type:
		default_received_type = next(
			(row.name for row in received_type_rows if row.is_default),
			None,
		)
	if default_received_type and default_received_type not in received_types:
		received_types.insert(0, default_received_type)

	for row in existing_rows or []:
		received_type = row.get("received_type")
		if received_type and received_type not in received_types:
			received_types.append(received_type)

	if not received_types:
		return [None], None
	if not default_received_type and len(received_types) == 1:
		default_received_type = received_types[0]
	return received_types, default_received_type


def _existing_receipt_quantities(wo, existing_rows, default_received_type):
	quantities = defaultdict(float)
	for row in existing_rows or []:
		target = _find_matching_receivable(wo.receivables, row)
		if not target:
			continue
		received_type = row.get("received_type") or default_received_type
		key = _receipt_split_key(target.name, row.get("item_variant"), received_type)
		quantities[key] += flt(row.get("quantity"))
	return quantities


def _receipt_split_key(receivable_name, item_variant, received_type):
	return (receivable_name, item_variant, received_type or "")
