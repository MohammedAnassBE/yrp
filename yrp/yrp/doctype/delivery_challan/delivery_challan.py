import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime


class DeliveryChallan(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_correction_items_for_ui, group_items_for_ui

		self.set_onload(
			"item_details",
			group_items_for_ui(self.get("items") or [], "Delivery Challan"),
		)
		self.set_onload(
			"correction_item_details",
			group_correction_items_for_ui(self.get("correction_items") or [], "Delivery Challan"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.sync_vue_correction_item_details()
		self.set_missing_values()
		self.apply_dimensions()
		self.set_item_defaults()
		self.compute_internal_unit()
		# Submit pass: drop the zero-qty rows that sync_vue_item_details kept
		# for draft re-editing (a DC inherits every WO deliverable; users
		# typically zero out items they're not delivering in this DC, then
		# submit only the rows that actually move). The save pass leaves them
		# untouched so the user can come back and re-edit.
		if self.docstatus == 1:
			self.items = [it for it in self.get("items") or [] if (it.qty or 0) > 0]
			self.correction_items = [
				it for it in self.get("correction_items") or [] if (it.qty or 0) > 0
			]

	def validate(self):
		self.validate_work_order()
		self.validate_items()
		self.calculate_totals()

	def before_submit(self):
		self.validate_work_order()
		self.validate_against_work_order_pending()
		self.validate_stock_available()

	def on_submit(self):
		self.update_work_order_deliverables()
		self.make_stock_ledger_entries()
		self.update_work_order_reservations()
		self.make_repost_action()

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation", "Stock Entry")
		if self.is_internal_unit:
			ste_names = frappe.get_all(
				"Stock Entry",
				filters={
					"against": "Delivery Challan",
					"against_id": self.name,
					"purpose": "DC Completion",
					"docstatus": 1,
				},
				pluck="name",
			)
			for name in ste_names:
				frappe.get_doc("Stock Entry", name).cancel()

	def on_cancel(self):
		self.make_stock_ledger_entries(cancel=True)
		self.update_work_order_deliverables(cancel=True)
		self.update_work_order_reservations(cancel=True)
		if self.is_internal_unit:
			self.db_set("ste_transferred", 0)
			self.db_set("ste_transferred_percent", 0)
			self.db_set("transfer_complete", 0)
		self.make_repost_action()

	def set_missing_values(self):
		from yrp.stock.utils import apply_posting_datetime

		# Posting date/time follow the "Edit Posting Date and Time" checkbox
		# (stamped to now unless ticked) — ERPNext set_posting_time semantics.
		apply_posting_datetime(self)
		if not self.work_order:
			return

		wo = frappe.get_cached_doc("Work Order", self.work_order)
		if self.meta.get_field("is_rework"):
			self.is_rework = wo.is_rework
		self.process_name = self.process_name or wo.process_name
		self.item = self.item or wo.item
		self.production_detail = self.production_detail or wo.production_detail
		self.supplier = self.supplier or wo.supplier
		self.from_location = self.from_location or wo.delivery_location
		self.to_warehouse = self.to_warehouse or _get_warehouse_for_supplier(wo.supplier)
		self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(wo.delivery_location)
		_copy_production_group_dimensions_from_source(self, wo)

	def sync_vue_item_details(self):
		if self.docstatus != 0 or not self.get("item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		# keep_zero=True: a DC inherits every WO deliverable and the user
		# typically zeroes rows they aren't dispatching in this DC; we keep
		# them across draft saves so the row can be re-edited, then strip
		# qty=0 in before_validate when docstatus==1 (the submit pass).
		rows = ungroup_items_from_ui(self.item_details, "Delivery Challan", keep_zero=True)
		self.set("items", [])
		for row in rows:
			self.append("items", row)

	def sync_vue_correction_item_details(self):
		if self.docstatus != 0 or not self.get("correction_item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_correction_items_from_ui

		rows = ungroup_correction_items_from_ui(
			self.correction_item_details, "Delivery Challan", keep_zero=True
		)
		self.set("correction_items", [])
		for row in rows:
			self.append("correction_items", row)

	def apply_dimensions(self):
		_copy_header_dimensions_to_items(self)
		from yrp.stock.dimensions import apply_dimension_defaults

		apply_dimension_defaults((self.get("items") or []) + (self.get("correction_items") or []))

	def set_item_defaults(self):
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			row.delivered_quantity = flt(row.qty)
			row.conversion_factor = flt(row.conversion_factor) or 1
			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			default_uom = frappe.get_cached_value("Item", parent_item, "default_unit_of_measure") if parent_item else None
			row.uom = row.uom or default_uom
			row.stock_uom = row.stock_uom or row.uom or default_uom
			row.stock_qty = flt(row.delivered_quantity) * flt(row.conversion_factor)
			rate = get_delivery_row_valuation_rate(
				row,
				self.from_warehouse,
				self.posting_date,
				self.posting_time,
				child_doctype="Delivery Challan Item",
			)
			row.valuation_rate = flt(rate)
			row.rate = flt(rate)
			row.amount = flt(row.stock_qty) * flt(row.valuation_rate)

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
		# Correction-only DCs are valid: a fully-delivered WO can still owe its
		# Work Order Correction quantities (user, 2026-07-09).
		if not (self.get("items") or self.get("correction_items")):
			frappe.throw(_("At least one deliverable or correction item is required."))
		if self.from_warehouse == self.to_warehouse:
			frappe.throw(_("From Warehouse and To Warehouse must be different."))
		# qty=0 rows are kept across draft saves so the user can re-edit later;
		# before_validate(docstatus==1) strips them on the submit pass, so by
		# the time validate_items runs on submit the items list is already
		# clean. Skip the qty>0 check on draft saves only.
		check_qty = self.docstatus == 1
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			if not row.item_variant:
				frappe.throw(_("Row {0}: Item Variant is required.").format(row.idx))
			if check_qty and flt(row.delivered_quantity or row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero.").format(row.idx))
			if not row.uom:
				frappe.throw(_("Row {0}: UOM is required.").format(row.idx))

	def calculate_totals(self):
		all_rows = (self.get("items") or []) + (self.get("correction_items") or [])
		self.total_delivered_qty = sum(flt(row.delivered_quantity or row.qty) for row in all_rows)
		self.stock_value = sum(flt(row.stock_qty) * flt(row.valuation_rate or row.rate) for row in all_rows)
		self.total_value = sum(flt(row.amount) for row in all_rows)

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
			row.ref_doctype = "Work Order Deliverables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

		corr_cache = {}
		for row in self.get("correction_items") or []:
			name = row.work_order_correction
			if not name:
				frappe.throw(
					_("Row {0}: correction item missing Work Order Correction.").format(row.idx)
				)
			corr = corr_cache.get(name) or frappe.get_doc("Work Order Correction", name)
			corr_cache[name] = corr
			if corr.work_order != self.work_order:
				frappe.throw(
					_("Row {0}: Work Order Correction {1} belongs to Work Order {2}, not {3}.").format(
						row.idx, name, corr.work_order, self.work_order
					)
				)
			target = _find_matching_row(corr.deliverables, row, "Work Order Deliverables")
			if not target:
				frappe.throw(
					_("Row {0}: no matching correction deliverable for {1}.").format(
						row.idx, row.item_variant
					)
				)
			row.ref_doctype = "Work Order Deliverables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

	def validate_stock_available(self):
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_available_stock, get_stock_balance

		if not self.from_warehouse:
			frappe.throw(_("From Warehouse is required."))

		is_rework = _is_rework_work_order(self.work_order)
		dim_fields = get_dimension_fieldnames()
		required_by_bucket = {}
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			qty = flt(row.stock_qty) or flt(row.delivered_quantity or row.qty)
			if qty <= 0:
				continue
			base = _sle_base(self, row)
			dim_values = tuple(base.get(fn) for fn in dim_fields)
			key = (row.item_variant, self.from_warehouse, dim_values)
			required_by_bucket.setdefault(
				key,
				{"item": row.item_variant, "qty": 0.0, "dims": dict(zip(dim_fields, dim_values))},
			)
			required_by_bucket[key]["qty"] += qty

		for bucket in required_by_bucket.values():
			if is_rework:
				available = get_available_stock(
					bucket["item"],
					self.from_warehouse,
					exclude_voucher_type="Work Order",
					exclude_voucher_name=self.work_order,
					**bucket["dims"],
				)
			else:
				available = get_stock_balance(
					bucket["item"],
					self.from_warehouse,
					posting_date=self.posting_date,
					posting_time=self.posting_time,
					**bucket["dims"],
				)
			required = flt(bucket["qty"])
			if flt(available) + 0.0001 < required:
				frappe.throw(
					_(
						"Insufficient stock for {0} at {1} as of {2} {3}: "
						"available {4}, required {5}."
					).format(
						bucket["item"],
						self.from_warehouse,
						self.posting_date,
						self.posting_time,
						flt(available),
						required,
					)
				)

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

		by_corr = {}
		for row in self.get("correction_items") or []:
			by_corr.setdefault(row.work_order_correction, []).append(row)
		for name, rows in by_corr.items():
			corr = frappe.get_doc("Work Order Correction", name)
			touched = False
			for row in rows:
				target = _find_matching_row(corr.deliverables, row, "Work Order Deliverables")
				if not target:
					continue
				qty = flt(row.delivered_quantity or row.qty)
				pending = flt(target.pending_quantity) + qty if cancel else flt(target.pending_quantity) - qty
				target.db_set("pending_quantity", flt(pending), update_modified=False)
				touched = True
			if touched:
				_update_work_order_correction_status(name)

	def update_work_order_reservations(self, cancel=False):
		# Applies to ANY Work Order, not just rework. If a Stock Reservation
		# Entry exists against the WO+deliverable (auto-created for rework,
		# manually created for normal flow), DC submit increases delivered_qty
		# and DC cancel decreases it. No-op if no matching SRE exists.
		if not self.work_order:
			return
		for row in self.items:
			if row.ref_doctype != "Work Order Deliverables" or not row.ref_docname:
				continue
			qty = flt(row.stock_qty) or flt(row.delivered_quantity or row.qty)
			_update_work_order_sre_delivered_qty(
				self.work_order,
				row.ref_docname,
				-qty if cancel else qty,
			)

	def make_stock_ledger_entries(self, cancel=False):
		from yrp.stock.stock_ledger import make_sl_entries

		destination = self.to_warehouse
		if self.is_internal_unit:
			destination = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
			if not destination:
				frappe.throw(
					_("Transit Warehouse must be set in YRP Stock Settings for internal-unit Delivery Challan.")
				)

		entries = []
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			qty = flt(row.stock_qty) or flt(row.delivered_quantity or row.qty)
			if qty <= 0:
				continue
			base = _sle_base(self, row)
			transfer_key = f"Delivery Challan:{self.name}:{row.name}"
			entries.append({
				**base,
				"warehouse": self.from_warehouse,
				"qty": -qty,
				"rate": 0,
				"outgoing_rate": flt(row.valuation_rate or row.rate),
				"_transfer_key": transfer_key,
				"_transfer_role": "outgoing",
			})
			entries.append({
				**base,
				"warehouse": destination,
				"qty": qty,
				"rate": flt(row.valuation_rate or row.rate),
				"_transfer_key": transfer_key,
				"_transfer_role": "incoming",
			})

		make_sl_entries(entries, cancel=cancel)

	def make_repost_action(self):
		from yrp.stock.stock_ledger import enqueue_voucher_repost

		enqueue_voucher_repost(self)

	def compute_internal_unit(self):
		if not self.from_location or not self.supplier or self.from_location == self.supplier:
			self.is_internal_unit = 0
			return
		flags = {
			row.name: row.is_company_location
			for row in frappe.db.get_all(
				"Supplier",
				filters={"name": ["in", [self.from_location, self.supplier]]},
				fields=["name", "is_company_location"],
			)
		}
		self.is_internal_unit = 1 if (flags.get(self.from_location) and flags.get(self.supplier)) else 0


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
		for row in (doc.get("items") or []) + (doc.get("correction_items") or []):
			if row.meta.get_field(fn) and not row.get(fn):
				row.set(fn, doc.get(fn))


def _copy_production_group_dimensions_from_source(target, source):
	for fn, value in _get_production_group_dimensions(source).items():
		if target.meta.get_field(fn) and not target.get(fn):
			target.set(fn, value)


def _get_production_group_dimensions(doc):
	from yrp.stock.dimensions import get_stock_dimensions

	values = {}
	for dim in get_stock_dimensions():
		if not dim.get("is_production_group"):
			continue
		fn = dim["fieldname"]
		if doc.meta.get_field(fn) and doc.get(fn):
			values[fn] = doc.get(fn)
	return values


def _apply_dimension_values_to_rows(rows, values):
	if not values:
		return
	for row in rows:
		for fn, value in values.items():
			if not row.get(fn):
				row[fn] = value


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
		row_value = row.get(fn) if row.meta.get_field(fn) else None
		doc_value = doc.get(fn) if doc.meta.get_field(fn) else None
		base[fn] = row_value or doc_value
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
			if row.name == source_row.ref_docname and _same_item_row(row, source_row):
				return row
	for row in rows:
		if _same_item_row(row, source_row):
			return row
	return None


def _same_item_row(target_row, source_row):
	if target_row.item_variant != source_row.item_variant:
		return False
	return _normal_json(target_row.get("set_combination")) == _normal_json(
		source_row.get("set_combination")
	)


def _update_work_order_status(work_order):
	wo = frappe.get_doc("Work Order", work_order)
	wo.set_status()
	wo.db_set("status", wo.status, update_modified=False)
	wo.db_set("is_delivered", wo.is_delivered, update_modified=False)


def _update_work_order_correction_status(name):
	corr = frappe.get_doc("Work Order Correction", name)
	corr.set_status()
	corr.db_set("status", corr.status, update_modified=False)


@frappe.whitelist()
def get_work_order_defaults(work_order, posting_date=None, posting_time=None):
	from yrp.stock.save_stock_items import group_correction_items_for_ui, group_items_for_ui
	from yrp.stock.dimensions import apply_dimension_defaults

	wo = frappe.get_doc("Work Order", work_order)
	items = _pending_deliverable_rows(wo)
	dimensions = _get_production_group_dimensions(wo)
	_apply_dimension_values_to_rows(items, dimensions)
	apply_dimension_defaults(items)
	from_warehouse = _get_warehouse_for_supplier(wo.delivery_location)
	for row in items:
		rate = get_delivery_row_valuation_rate(
			row,
			from_warehouse,
			posting_date or nowdate(),
			posting_time or nowtime(),
			child_doctype="Delivery Challan Item",
		)
		row["rate"] = flt(rate)
		row["valuation_rate"] = flt(rate)
		row["amount"] = flt(row.get("stock_qty") or row.get("delivered_quantity") or row.get("qty")) * flt(rate)

	correction_items = _pending_correction_deliverable_rows(wo)
	_apply_dimension_values_to_rows(correction_items, dimensions)
	apply_dimension_defaults(correction_items)
	for row in correction_items:
		rate = get_delivery_row_valuation_rate(
			row,
			from_warehouse,
			posting_date or nowdate(),
			posting_time or nowtime(),
			child_doctype="Delivery Challan Item",
		)
		row["rate"] = flt(rate)
		row["valuation_rate"] = flt(rate)
		row["amount"] = flt(row.get("stock_qty") or row.get("delivered_quantity") or row.get("qty")) * flt(rate)
	defaults = {
		"process_name": wo.process_name,
		"item": wo.item,
		"production_detail": wo.production_detail,
		"is_rework": wo.is_rework,
		"supplier": wo.supplier,
		"from_location": wo.delivery_location,
		"from_warehouse": from_warehouse,
		"to_warehouse": _get_warehouse_for_supplier(wo.supplier),
		"items": items,
		"item_details": group_items_for_ui(items, "Delivery Challan"),
		"correction_items": correction_items,
		"correction_item_details": group_correction_items_for_ui(correction_items, "Delivery Challan"),
	}
	defaults.update(dimensions)
	return defaults


def _pending_deliverable_rows(wo):
	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	rows = []
	for row in wo.get("deliverables") or []:
		pending = flt(row.pending_quantity)
		# Zero-pending rows are STILL offered (qty prefilled 0) so the user can
		# deliver EXCESS against a fully-delivered Work Order (2026-07-10):
		# submit strips qty=0 rows, and update_work_order_deliverables lets
		# pending go negative — the excess-delivered signal.
		prefill = pending if pending > 0 else 0
		out = {
			"item_variant": row.item_variant,
			"qty": prefill,
			"delivered_quantity": prefill,
			"uom": row.uom,
			"pending_quantity": pending,
			"ref_doctype": "Work Order Deliverables",
			"ref_docname": row.name,
			"table_index": row.table_index,
			"row_index": row.row_index,
			"set_combination": row.set_combination,
		}
		for fn in dim_fields:
			if row.meta.get_field(fn) and row.get(fn):
				out[fn] = row.get(fn)
		rows.append(out)
	return rows


def _pending_correction_deliverable_rows(wo):
	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	rows = []
	names = frappe.get_all(
		"Work Order Correction",
		filters={"work_order": wo.name, "docstatus": 1},
		pluck="name",
	)
	for name in names:
		corr = frappe.get_doc("Work Order Correction", name)
		for row in corr.get("deliverables") or []:
			pending = flt(row.pending_quantity)
			if pending <= 0:
				continue
			out = {
				"item_variant": row.item_variant,
				"qty": pending,
				"delivered_quantity": pending,
				"uom": row.uom,
				"pending_quantity": pending,
				"ref_doctype": "Work Order Deliverables",
				"ref_docname": row.name,
				"work_order_correction": name,
				"table_index": row.table_index,
				"row_index": row.row_index,
				"set_combination": row.set_combination,
			}
			for fn in dim_fields:
				if row.meta.get_field(fn) and row.get(fn):
					out[fn] = row.get(fn)
			rows.append(out)
	return rows


def get_delivery_row_valuation_rate(row, warehouse, posting_date=None, posting_time=None, child_doctype=None):
	if not row or not row.get("item_variant") or not warehouse:
		return 0
	from yrp.stock.utils import get_stock_balance

	dim_filters = _row_dimension_filters(row, child_doctype)
	_, rate = get_stock_balance(
		row.get("item_variant"),
		warehouse,
		posting_date=posting_date,
		posting_time=posting_time,
		with_valuation_rate=True,
		**dim_filters,
	)
	return flt(rate)


def _row_dimension_filters(row, child_doctype=None):
	from yrp.stock.dimensions import get_dimension_fieldnames

	meta = frappe.get_meta(child_doctype) if child_doctype else getattr(row, "meta", None)
	filters = {}
	for fn in get_dimension_fieldnames():
		if meta and not meta.get_field(fn):
			continue
		value = row.get(fn)
		if value:
			filters[fn] = value
	return filters


def _is_rework_work_order(work_order):
	if not work_order:
		return False
	return bool(frappe.db.get_value("Work Order", work_order, "is_rework"))


def _update_work_order_sre_delivered_qty(work_order, voucher_detail_no, qty_delta):
	if not work_order or not voucher_detail_no or not qty_delta:
		return
	sre_name = frappe.db.get_value(
		"Stock Reservation Entry",
		{
			"voucher_type": "Work Order",
			"voucher_no": work_order,
			"voucher_detail_no": voucher_detail_no,
			"docstatus": 1,
		},
		"name",
	)
	if not sre_name:
		return
	sre = frappe.get_doc("Stock Reservation Entry", sre_name)
	# Once an SRE has been manually closed-short, its delivered_qty is
	# frozen. A later DC submit/cancel must not silently mutate it.
	if sre.status == "Closed":
		frappe.throw(_(
			"Stock Reservation Entry {0} has been closed; cannot update delivered qty."
		).format(sre.name))
	# Clamp into [0, reserved_qty]: excess delivery (2026-07-10) can exceed the
	# reservation — delivered_qty beyond reserved_qty would drive the SRE's
	# remaining negative and INFLATE apparent availability downstream
	# (get_sre_reserved_qty). The excess itself still ships; only the
	# reservation consumption is capped at what was actually reserved.
	delivered_qty = max(flt(sre.delivered_qty) + flt(qty_delta), 0)
	delivered_qty = min(delivered_qty, flt(sre.reserved_qty))
	sre.delivered_qty = delivered_qty
	sre.set_status()
	frappe.db.set_value(
		"Stock Reservation Entry",
		sre.name,
		{"delivered_qty": delivered_qty, "status": sre.status},
		update_modified=False,
	)


@frappe.whitelist()
def make_dc_completion(doc_name):
	frappe.has_permission("Stock Entry", "create", throw=True)
	dc = frappe.get_doc("Delivery Challan", doc_name)
	if dc.docstatus != 1:
		frappe.throw(_("Delivery Challan must be submitted."))
	if not dc.is_internal_unit:
		frappe.throw(_("Delivery Challan is not an internal unit transfer."))
	if dc.transfer_complete:
		frappe.throw(_("Transfer is already complete for this Delivery Challan."))
	pending_draft = frappe.db.exists(
		"Stock Entry",
		{"against": "Delivery Challan", "against_id": doc_name, "purpose": "DC Completion", "docstatus": 0},
	)
	if pending_draft:
		frappe.throw(
			_("A draft DC Completion Stock Entry already exists ({0}). Submit or delete it before creating a new one.").format(pending_draft)
		)

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	items = []
	for item in dc.items:
		pending = flt(item.delivered_quantity) - flt(item.ste_delivered_quantity)
		if pending <= 0:
			continue
		conv = flt(item.conversion_factor) or 1
		row_data = {
			"item": item.item_variant,
			"qty": pending,
			"stock_qty": pending * conv,
			"uom": item.uom,
			"stock_uom": item.stock_uom or item.uom,
			"conversion_factor": conv,
			"rate": flt(item.valuation_rate or item.rate),
			"table_index": item.table_index,
			"row_index": item.row_index,
			"against": "Delivery Challan Item",
			"against_id_detail": item.name,
			"remarks": item.comments,
		}
		for fn in dim_fields:
			if item.meta.get_field(fn):
				row_data[fn] = item.get(fn)
		items.append(row_data)

	if not items:
		frappe.throw(_("Nothing left to transfer."))

	ste = frappe.new_doc("Stock Entry")
	ste.purpose = "DC Completion"
	ste.against = "Delivery Challan"
	ste.against_id = doc_name
	ste.from_warehouse = dc.from_warehouse
	ste.to_warehouse = dc.to_warehouse
	for row_data in items:
		ste.append("items", row_data)
	ste.flags.allow_from_dc = True
	ste.insert(ignore_permissions=True)
	return ste.name
