import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime


class DeliveryChallan(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		self.set_onload(
			"item_details",
			group_items_for_ui(self.get("items") or [], "Delivery Challan"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.set_missing_values()
		self.apply_dimensions()
		self.set_item_defaults()

	def validate(self):
		self.validate_work_order()
		self.validate_items()
		self.calculate_totals()

	def before_submit(self):
		self.validate_work_order()
		self.validate_against_work_order_pending()

	def on_submit(self):
		self.update_work_order_deliverables()
		self.make_stock_ledger_entries()

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		self.make_stock_ledger_entries(cancel=True)
		self.update_work_order_deliverables(cancel=True)

	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.posting_time:
			self.posting_time = nowtime()
		if not self.work_order:
			return

		wo = frappe.get_cached_doc("Work Order", self.work_order)
		self.process_name = self.process_name or wo.process_name
		self.item = self.item or wo.item
		self.production_detail = self.production_detail or wo.production_detail
		self.supplier = self.supplier or wo.supplier
		self.from_location = self.from_location or wo.delivery_location
		self.to_warehouse = self.to_warehouse or _get_warehouse_for_supplier(wo.supplier)
		self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(wo.delivery_location)

	def sync_vue_item_details(self):
		if self.docstatus != 0 or not self.get("item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		rows = ungroup_items_from_ui(self.item_details, "Delivery Challan")
		self.set("items", [])
		for row in rows:
			self.append("items", row)

	def apply_dimensions(self):
		_copy_header_dimensions_to_items(self)
		from yrp.stock.dimensions import apply_dimension_defaults

		apply_dimension_defaults(self.get("items") or [])

	def set_item_defaults(self):
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_last_sle_rate

		dim_fields = get_dimension_fieldnames()
		for row in self.get("items") or []:
			row.delivered_quantity = flt(row.delivered_quantity or row.qty)
			row.conversion_factor = flt(row.conversion_factor) or 1
			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			default_uom = frappe.get_cached_value("Item", parent_item, "default_unit_of_measure") if parent_item else None
			row.uom = row.uom or default_uom
			row.stock_uom = row.stock_uom or row.uom or default_uom
			row.stock_qty = flt(row.delivered_quantity) * flt(row.conversion_factor)

			dim_filters = {fn: row.get(fn) for fn in dim_fields if row.meta.get_field(fn)}
			rate, _matched = get_last_sle_rate(
				row.item_variant, warehouse=self.from_warehouse, **dim_filters
			)
			if not flt(row.rate):
				row.rate = flt(rate)
			row.valuation_rate = flt(rate or row.rate)
			row.amount = flt(row.delivered_quantity) * flt(row.rate)

	def validate_work_order(self):
		if not self.work_order:
			frappe.throw(_("Work Order is required."))
		docstatus, open_status = frappe.db.get_value(
			"Work Order", self.work_order, ["docstatus", "open_status"]
		)
		if docstatus != 1:
			frappe.throw(_("Work Order {0} must be submitted.").format(self.work_order))
		if open_status == "Close":
			frappe.throw(_("Work Order {0} is closed.").format(self.work_order))

	def validate_items(self):
		if not self.get("items"):
			frappe.throw(_("At least one item is required."))
		if self.from_warehouse == self.to_warehouse:
			frappe.throw(_("From Warehouse and To Warehouse must be different."))
		for row in self.items:
			if not row.item_variant:
				frappe.throw(_("Row {0}: Item Variant is required.").format(row.idx))
			if flt(row.delivered_quantity or row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero.").format(row.idx))
			if not row.uom:
				frappe.throw(_("Row {0}: UOM is required.").format(row.idx))

	def calculate_totals(self):
		self.total_delivered_qty = sum(flt(row.delivered_quantity or row.qty) for row in self.items)
		self.stock_value = sum(flt(row.stock_qty) * flt(row.valuation_rate or row.rate) for row in self.items)
		self.total_value = sum(flt(row.amount) for row in self.items)

	def validate_against_work_order_pending(self):
		wo = frappe.get_doc("Work Order", self.work_order)
		for row in self.items:
			target = _find_matching_row(wo.deliverables, row, "Work Order Deliverables")
			if not target:
				frappe.throw(
					_("Row {0}: no matching Work Order Deliverable found for {1}.").format(
						row.idx, row.item_variant
					)
				)
			qty = flt(row.delivered_quantity or row.qty)
			if flt(target.pending_quantity) + 0.0001 < qty:
				frappe.throw(
					_("Row {0}: delivery qty {1} exceeds pending qty {2} for {3}.").format(
						row.idx, qty, flt(target.pending_quantity), row.item_variant
					)
				)
			row.ref_doctype = "Work Order Deliverables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

	def update_work_order_deliverables(self, cancel=False):
		wo = frappe.get_doc("Work Order", self.work_order)
		changed = False
		for row in self.items:
			target = _find_matching_row(wo.deliverables, row, "Work Order Deliverables")
			if not target:
				continue
			qty = flt(row.delivered_quantity or row.qty)
			pending = flt(target.pending_quantity) + qty if cancel else flt(target.pending_quantity) - qty
			target.db_set("pending_quantity", flt(pending), update_modified=False)
			changed = True

		if changed:
			_update_work_order_status(self.work_order)

	def make_stock_ledger_entries(self, cancel=False):
		from yrp.stock.stock_ledger import make_sl_entries

		entries = []
		for row in self.items:
			qty = flt(row.stock_qty) or flt(row.delivered_quantity or row.qty)
			if qty <= 0:
				continue
			base = _sle_base(self, row)
			entries.append({
				**base,
				"warehouse": self.from_warehouse,
				"qty": -qty,
				"rate": 0,
				"outgoing_rate": flt(row.valuation_rate or row.rate),
			})
			entries.append({
				**base,
				"warehouse": self.to_warehouse,
				"qty": qty,
				"rate": flt(row.valuation_rate or row.rate),
			})

		make_sl_entries(entries, cancel=cancel)


def _get_warehouse_for_supplier(supplier):
	if not supplier or not frappe.db.exists("DocType", "Warehouse"):
		return None
	warehouses = frappe.get_all("Warehouse", filters={"supplier": supplier, "disabled": 0}, pluck="name")
	return warehouses[0] if len(warehouses) == 1 else None


def _copy_header_dimensions_to_items(doc):
	from yrp.stock.dimensions import get_dimension_fieldnames

	for fn in get_dimension_fieldnames():
		if not doc.meta.get_field(fn) or not doc.get(fn):
			continue
		for row in doc.get("items") or []:
			if row.meta.get_field(fn) and not row.get(fn):
				row.set(fn, doc.get(fn))


def _sle_base(doc, row):
	from yrp.stock.dimensions import get_dimension_fieldnames

	base = {
		"item": row.item_variant,
		"uom": row.stock_uom or row.uom,
		"voucher_type": doc.doctype,
		"voucher_no": doc.name,
		"voucher_detail_no": row.name,
		"posting_date": doc.posting_date,
		"posting_time": doc.posting_time,
		"is_cancelled": 0,
	}
	for fn in get_dimension_fieldnames():
		if row.meta.get_field(fn):
			base[fn] = row.get(fn)
		elif doc.meta.get_field(fn):
			base[fn] = doc.get(fn)
		else:
			base[fn] = None
	if "received_type" in base and not base.get("received_type"):
		base["received_type"] = frappe.db.get_single_value(
			"YRP Stock Settings", "default_received_type"
		)
	return base


def _normal_json(value):
	if not value:
		return {}
	return frappe.parse_json(value) if isinstance(value, str) else value


def _find_matching_row(rows, source_row, ref_doctype):
	if source_row.ref_doctype == ref_doctype and source_row.ref_docname:
		for row in rows:
			if row.name == source_row.ref_docname:
				return row
	for row in rows:
		if row.item_variant != source_row.item_variant:
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(source_row.get("set_combination")):
			return row
	return None


def _update_work_order_status(work_order):
	wo = frappe.get_doc("Work Order", work_order)
	wo.set_status()
	wo.db_set("status", wo.status, update_modified=False)
	wo.db_set("is_delivered", wo.is_delivered, update_modified=False)


@frappe.whitelist()
def get_work_order_defaults(work_order):
	from yrp.stock.save_stock_items import group_items_for_ui
	from yrp.stock.dimensions import apply_dimension_defaults

	wo = frappe.get_doc("Work Order", work_order)
	items = _pending_deliverable_rows(wo)
	apply_dimension_defaults(items)
	return {
		"process_name": wo.process_name,
		"item": wo.item,
		"production_detail": wo.production_detail,
		"supplier": wo.supplier,
		"from_location": wo.delivery_location,
		"from_warehouse": _get_warehouse_for_supplier(wo.delivery_location),
		"to_warehouse": _get_warehouse_for_supplier(wo.supplier),
		"items": items,
		"item_details": group_items_for_ui(items, "Delivery Challan"),
	}


def _pending_deliverable_rows(wo):
	rows = []
	for row in wo.get("deliverables") or []:
		pending = flt(row.pending_quantity)
		if pending <= 0:
			continue
		rows.append({
			"item_variant": row.item_variant,
			"qty": pending,
			"delivered_quantity": pending,
			"uom": row.uom,
			"pending_quantity": pending,
			"ref_doctype": "Work Order Deliverables",
			"ref_docname": row.name,
			"table_index": row.table_index,
			"row_index": row.row_index,
			"set_combination": row.set_combination,
		})
	return rows
