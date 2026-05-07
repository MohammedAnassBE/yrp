import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime

from yrp.yrp.doctype.delivery_challan.delivery_challan import (
	_copy_header_dimensions_to_items,
	_get_warehouse_for_supplier,
	_normal_json,
	_sle_base,
	_update_work_order_status,
)


class GoodsReceivedNote(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		self.set_onload(
			"item_details",
			group_items_for_ui(self.get("items") or [], "Goods Received Note"),
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
		self.validate_against_work_order_pending()

	def on_submit(self):
		self.update_work_order_receivables()
		self.make_stock_ledger_entries()

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		self.make_stock_ledger_entries(cancel=True)
		self.update_work_order_receivables(cancel=True)

	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.posting_time:
			self.posting_time = nowtime()
		if self.against != "Work Order" or not self.against_id:
			return

		wo = frappe.get_cached_doc("Work Order", self.against_id)
		self.process_name = self.process_name or wo.process_name
		self.item = self.item or wo.item
		self.production_detail = self.production_detail or wo.production_detail
		self.supplier = self.supplier or wo.supplier
		self.delivery_location = self.delivery_location or wo.delivery_location
		self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(wo.supplier)
		self.to_warehouse = self.to_warehouse or _get_warehouse_for_supplier(wo.delivery_location)

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
		for row in self.get("items") or []:
			row.conversion_factor = flt(row.conversion_factor) or 1
			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			default_uom = frappe.get_cached_value("Item", parent_item, "default_unit_of_measure") if parent_item else None
			row.uom = row.uom or default_uom
			row.stock_uom = row.stock_uom or row.uom or default_uom
			row.stock_qty = flt(row.quantity) * flt(row.conversion_factor)
			row.amount = flt(row.quantity) * flt(row.rate)

	def validate_against(self):
		if self.against != "Work Order":
			frappe.throw(_("Only Work Order GRN is available in YRP base."))
		if not self.against_id:
			frappe.throw(_("Against ID is required."))
		docstatus, open_status = frappe.db.get_value(
			"Work Order", self.against_id, ["docstatus", "open_status"]
		)
		if docstatus != 1:
			frappe.throw(_("Work Order {0} must be submitted.").format(self.against_id))
		if open_status == "Close":
			frappe.throw(_("Work Order {0} is closed.").format(self.against_id))
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
		if self.from_warehouse == self.to_warehouse:
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

	def validate_against_work_order_pending(self):
		wo = frappe.get_doc("Work Order", self.against_id)
		for row in self.items:
			target = _find_matching_receivable(wo.receivables, row)
			if not target:
				frappe.throw(
					_("Row {0}: no matching Work Order Receivable found for {1}.").format(
						row.idx, row.item_variant
					)
				)
			if flt(target.pending_quantity) + 0.0001 < flt(row.quantity):
				frappe.throw(
					_("Row {0}: received qty {1} exceeds pending qty {2} for {3}.").format(
						row.idx, flt(row.quantity), flt(target.pending_quantity), row.item_variant
					)
				)
			row.ref_doctype = "Work Order Receivables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

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

		entries.extend(self.get_consumption_entries())
		make_sl_entries(entries, cancel=cancel)

	def get_consumption_entries(self):
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_last_sle_rate

		wo = frappe.get_doc("Work Order", self.against_id)
		total_receivable = sum(flt(row.qty) for row in wo.get("receivables") or [])
		scale = flt(self.total_received_quantity) / total_receivable if total_receivable else 0
		if scale <= 0:
			return []

		dim_fields = get_dimension_fieldnames()
		dim_values = {
			fn: self.get(fn)
			for fn in dim_fields
			if self.meta.get_field(fn) and self.get(fn)
		}
		_default_dimension_values(dim_values)

		entries = []
		for row in wo.get("deliverables") or []:
			qty = flt(row.qty) * scale
			if qty <= 0:
				continue
			rate, _matched = get_last_sle_rate(row.item_variant, warehouse=self.from_warehouse, **dim_values)
			base = {
				"item": row.item_variant,
				"uom": row.uom,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"is_cancelled": 0,
			}
			base.update(dim_values)
			entries.append({
				**base,
				"warehouse": self.from_warehouse,
				"qty": -qty,
				"rate": 0,
				"outgoing_rate": flt(rate),
			})
		return entries


def _default_dimension_values(dim_values):
	default_rt = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
	if default_rt and "received_type" not in dim_values:
		from yrp.stock.dimensions import get_dimension_fieldnames

		if "received_type" in get_dimension_fieldnames():
			dim_values["received_type"] = default_rt


def _find_matching_receivable(rows, source_row):
	if source_row.ref_doctype == "Work Order Receivables" and source_row.ref_docname:
		for row in rows:
			if row.name == source_row.ref_docname:
				return row
	for row in rows:
		if row.item_variant != source_row.item_variant:
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(source_row.get("set_combination")):
			return row
	return None


@frappe.whitelist()
def get_work_order_defaults(work_order):
	from yrp.stock.save_stock_items import group_items_for_ui
	from yrp.stock.dimensions import apply_dimension_defaults

	wo = frappe.get_doc("Work Order", work_order)
	items = _pending_receivable_rows(wo)
	apply_dimension_defaults(items)
	return {
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


def _pending_receivable_rows(wo):
	rows = []
	for row in wo.get("receivables") or []:
		pending = flt(row.pending_quantity)
		if pending <= 0:
			continue
		rows.append({
			"item_variant": row.item_variant,
			"quantity": pending,
			"uom": row.uom,
			"pending_quantity": pending,
			"ref_doctype": "Work Order Receivables",
			"ref_docname": row.name,
			"table_index": row.table_index,
			"row_index": row.row_index,
			"set_combination": row.set_combination,
			"rate": row.cost,
		})
	return rows
