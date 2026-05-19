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
		self.compute_internal_unit()

	def validate(self):
		self.validate_against()
		self.validate_items()
		self.calculate_totals()

	def before_submit(self):
		self.validate_against()
		self.validate_source_pending()
		self.apply_freight_allocation()

	def on_submit(self):
		self.update_source_pending()
		self.make_stock_ledger_entries()

	def before_cancel(self):
		self.validate_no_purchase_invoice()
		self.validate_closed_purchase_order()
		self.validate_age_limit()
		self.validate_no_inspection_entry()
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation", "Stock Entry", "Inspection Entry")
		if self.is_internal_unit:
			ste_names = frappe.get_all(
				"Stock Entry",
				filters={
					"against": "Goods Received Note",
					"against_id": self.name,
					"purpose": "GRN Completion",
					"docstatus": 1,
				},
				pluck="name",
			)
			for name in ste_names:
				frappe.get_doc("Stock Entry", name).cancel()

	def on_cancel(self):
		self.make_stock_ledger_entries(cancel=True)
		self.update_source_pending(cancel=True)
		if self.is_internal_unit:
			self.db_set("ste_transferred", 0)
			self.db_set("ste_transferred_percent", 0)
			self.db_set("transfer_complete", 0)

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

	def apply_freight_allocation(self):
		"""D-012: fold self.freight_charges into row.rate at submit so the SLE
		valuation_rate is freight-inclusive from the start. No retroactive Landed
		Cost Voucher. Allocation method comes from YRP Stock Settings:
		  - By Quantity: share = row.stock_qty / total_stock_qty
		  - By Value:    share = row.amount / total_amount; falls back to
		                 By Quantity when total_amount <= 0 (Gap #11).
		  - Manual:      share = row.freight_amount (operator-entered). Sum of
		                 row.freight_amount must equal freight_charges; submit is
		                 blocked otherwise. No residual reconciliation.

		Guards: negative freight rejected; zero total stock_qty with non-zero
		freight rejected for the proportional methods (Gap #12). Allocation uses
		stock_qty only (Gap #21). Post-allocation row.rate is per-stock-unit.
		(set_item_defaults stores row.rate as per-form-unit for PO mode; freight
		allocation normalises it to per-stock-unit. With conversion_factor==1 —
		the common case — they coincide.) Amended GRNs are re-based to source
		PO/WO rates before freight is applied so copied freight-inclusive rates
		do not receive freight a second time.

		Idempotent within a single doc lifetime: flags.freight_allocated blocks
		double application if before_submit fires twice.
		"""
		if self.flags.get("freight_allocated"):
			return
		freight = flt(self.freight_charges)
		if freight < 0:
			frappe.throw(_("Freight Charges cannot be negative."))
		self._prepare_freight_base_amounts()
		if freight == 0:
			self.total = sum(flt(r.amount) for r in self.items)
			self.flags.freight_allocated = True
			return

		method = frappe.db.get_single_value(
			"YRP Stock Settings", "freight_allocation_method"
		) or "By Quantity"
		if method not in ("By Quantity", "By Value", "Manual"):
			method = "By Quantity"

		if method == "Manual":
			self._apply_manual_freight(freight)
		else:
			self._apply_proportional_freight(freight, method)

		self.total = sum(flt(r.amount) for r in self.items)
		self.flags.freight_allocated = True

	def _prepare_freight_base_amounts(self):
		"""Normalise item amounts before applying freight.

		PO rows enter the form with a per-form-UOM rate, while SLE valuation uses
		stock_qty. For amended GRNs, copied child rows may already include old
		freight in row.rate, so source PO/WO rates are resolved again first.
		"""
		for row in self.items:
			stock_qty = flt(row.stock_qty) or (flt(row.quantity) * flt(row.conversion_factor or 1))
			if stock_qty <= 0:
				continue

			base_rate = self._get_source_base_rate(row) if self.amended_from else None
			if base_rate is None:
				base_rate = flt(row.rate)

			if self.against == "Purchase Order":
				base_amount = flt(row.quantity) * flt(base_rate)
				row.rate = base_amount / stock_qty if stock_qty else flt(base_rate)
			else:
				base_amount = stock_qty * flt(base_rate)
				row.rate = flt(base_rate)
			row.amount = base_amount

	def _get_source_base_rate(self, row):
		if self.against == "Purchase Order":
			if row.ref_docname:
				rate = frappe.db.get_value("Purchase Order Item", row.ref_docname, "rate")
				if rate is not None:
					return flt(rate)
			po = frappe.get_doc("Purchase Order", self.against_id)
			target = _find_matching_purchase_order_item(po.items, row)
			return flt(target.rate) if target else None

		if self.against == "Work Order":
			wo = frappe.get_doc("Work Order", self.against_id)
			delivery_challan = (
				frappe.get_doc("Delivery Challan", self.delivery_challan)
				if self.delivery_challan else None
			)
			return get_work_order_grn_rate(wo, delivery_challan, row)

		return None

	def _apply_proportional_freight(self, freight, method):
		"""Shared path for By Quantity / By Value. Computes each row's share
		against a total and reuses the residual-on-last-row trick so the SLE
		total exactly equals freight_charges."""
		total_stock_qty = sum(flt(r.stock_qty) for r in self.items)
		total_amount = sum(flt(r.amount) for r in self.items)

		if method == "By Value" and total_amount <= 0:
			method = "By Quantity"
		if method == "By Quantity" and total_stock_qty <= 0:
			frappe.throw(_("Cannot allocate freight when total qty is zero."))

		eligible = [row for row in self.items if flt(row.stock_qty) > 0]
		assigned = 0.0
		for idx, row in enumerate(eligible):
			stock_qty = flt(row.stock_qty)
			is_last = idx == len(eligible) - 1
			if is_last:
				share = freight - assigned
			elif method == "By Quantity":
				share = freight * (stock_qty / total_stock_qty)
			else:
				share = freight * (flt(row.amount) / total_amount) if total_amount else 0
			assigned += share
			new_amount = flt(row.amount) + share
			row.rate = new_amount / stock_qty
			row.amount = new_amount

	def _apply_manual_freight(self, freight):
		"""Manual mode: operator-specified per-row freight_amount. We trust the
		values but enforce sum(freight_amount) == freight_charges so a typo can't
		silently distort valuation.

		Note: rows with stock_qty<=0 are skipped during assignment but still count
		toward the sum. validate_items already rejects quantity<=0 upstream
		(see validate_items below) so this asymmetry is unreachable; if that
		guard ever loosens, freight on a zero-qty row would silently evaporate
		and the sum check should be updated to mirror the assignment skip.
		"""
		total_manual = sum(flt(row.freight_amount) for row in self.items)
		if abs(total_manual - freight) > 1e-2:
			frappe.throw(
				_("Manual freight allocation: sum of row Freight Amounts ({0}) must equal Freight Charges ({1}).").format(
					total_manual, freight
				)
			)
		for row in self.items:
			stock_qty = flt(row.stock_qty)
			if stock_qty <= 0:
				continue
			share = flt(row.freight_amount)
			new_amount = flt(row.amount) + share
			row.rate = new_amount / stock_qty
			row.amount = new_amount

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

	def validate_closed_purchase_order(self):
		# Only PO closure blocks GRN cancel. Work Order closure is handled by WO's
		# own lifecycle (open_status flip auto-closes reservations and zeroes
		# pending), so a WO-GRN cancel is the WO controller's concern.
		if self.against != "Purchase Order" or not self.against_id:
			return
		open_status = frappe.db.get_value("Purchase Order", self.against_id, "open_status")
		if open_status == "Close":
			frappe.throw(
				_("Cannot cancel Goods Received Note {0} — Purchase Order {1} is closed. Reopen the Purchase Order first.").format(
					self.name, self.against_id
				)
			)

	def validate_no_inspection_entry(self):
		"""Block GRN cancel while a submitted Inspection Entry exists for it.
		The IE owns SLEs that depend on this GRN's stock; operator must cancel
		the IE first."""
		if not frappe.db.exists("DocType", "Inspection Entry"):
			return
		ie = frappe.db.exists(
			"Inspection Entry",
			{
				"against": "Goods Received Note",
				"against_id": self.name,
				"docstatus": 1,
			},
		)
		if ie:
			frappe.throw(
				_("Cannot cancel Goods Received Note {0} — Inspection Entry {1} is submitted. Cancel the Inspection Entry first.").format(
					self.name, ie
				)
			)

	def validate_age_limit(self):
		from frappe.utils import getdate, today

		window = frappe.db.get_single_value("YRP Stock Settings", "grn_cancel_window_days")
		if not window or int(window) <= 0:
			return
		age_days = (getdate(today()) - getdate(self.posting_date)).days
		if age_days > int(window):
			frappe.throw(
				_("Cannot cancel Goods Received Note {0} — posted {1} ({2} days ago, limit is {3}).").format(
					self.name, self.posting_date, age_days, int(window)
				)
			)


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

		# Per-Process excess allowance on the source Work Order: receipt is allowed
		# up to receivable.qty × (1 + pct/100) per line. Stored on Process, not the
		# WO header — multiple WOs sharing a process inherit the same rule.
		excess_pct = _wo_excess_percentage(self.against_id)
		for receivable_name, total_qty in totals_by_receivable.items():
			target = receivable_by_name[receivable_name]
			ordered = flt(target.qty)
			allowance = _remaining_receivable_allowance(ordered, target.pending_quantity, excess_pct)
			if total_qty > allowance + 0.0001:
				frappe.throw(
					_("Received qty {0} exceeds allowance {1} for {2} (ordered {3}, excess allowance {4}%).").format(
						flt(total_qty), flt(allowance), target.item_variant, ordered, flt(excess_pct)
					)
				)
			for row in self.items:
				if _find_matching_receivable([target], row):
					row.max_receivable_quantity = max(flt(allowance), 0)

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

		# Per-Item excess allowance on the source PO line. Receipt is allowed up
		# to ordered_qty × (1 + pct/100).
		for item_name, total_qty in totals_by_item.items():
			target = item_by_name[item_name]
			ordered = flt(target.qty)
			excess_pct = _po_excess_percentage(target.item_variant)
			allowance = _remaining_receivable_allowance(ordered, target.pending_quantity, excess_pct)
			if total_qty > allowance + 0.0001:
				frappe.throw(
					_("Received qty {0} exceeds allowance {1} for {2} (ordered {3}, excess allowance {4}%).").format(
						flt(total_qty), flt(allowance), target.item_variant, ordered, flt(excess_pct)
					)
				)
			for row in self.items:
				if _find_matching_purchase_order_item([target], row):
					row.max_receivable_quantity = max(flt(allowance), 0)

	def update_source_pending(self, cancel=False):
		if self.against == "Purchase Order":
			self.update_purchase_order_items(cancel=cancel)
			return
		self.update_work_order_receivables(cancel=cancel)

	def update_work_order_receivables(self, cancel=False):
		# Note: pending_quantity may go negative when an excess receipt is allowed
		# (Process.wo_excess_allowed_percentage > 0). This is intentional — don't
		# add a clamp here. The validator already gates total receipts at
		# ordered × (1 + pct/100).
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
		# Note: pending_quantity may go negative when an excess receipt is allowed
		# (Item.po_excess_allowed_percentage > 0). This is intentional — the
		# validator already gates total receipts at ordered × (1 + pct/100).
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

		destination = self.to_warehouse
		if self.is_internal_unit:
			destination = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
			if not destination:
				frappe.throw(
					_("Transit Warehouse must be set in YRP Stock Settings for internal-unit Goods Received Note.")
				)

		entries = []
		for row in self.items:
			qty = flt(row.stock_qty) or flt(row.quantity)
			if qty <= 0:
				continue
			base = _sle_base(self, row)
			entries.append({
				**base,
				"warehouse": destination,
				"qty": qty,
				"rate": flt(row.rate),
			})

		make_sl_entries(entries, cancel=cancel)

	def compute_internal_unit(self):
		"""Internal-unit GRN: supplier (sender) and delivery_location (receiver) are both
		company locations. Mirrors DC's compute_internal_unit but uses (supplier,
		delivery_location) instead of DC's (from_location, supplier). PO-only GRNs lack
		delivery_location and stay non-internal."""
		if not self.supplier or not self.delivery_location or self.supplier == self.delivery_location:
			self.is_internal_unit = 0
			return
		flags = {
			row.name: row.is_company_location
			for row in frappe.db.get_all(
				"Supplier",
				filters={"name": ["in", [self.supplier, self.delivery_location]]},
				fields=["name", "is_company_location"],
			)
		}
		self.is_internal_unit = 1 if (flags.get(self.supplier) and flags.get(self.delivery_location)) else 0


def _po_excess_percentage(item_variant):
	"""Look up Item.po_excess_allowed_percentage via the item variant's parent.
	Returns 0 (strict pending) when the field is missing or unset."""
	if not item_variant:
		return 0
	parent_item = frappe.get_cached_value("Item Variant", item_variant, "item")
	if not parent_item:
		return 0
	return flt(frappe.get_cached_value("Item", parent_item, "po_excess_allowed_percentage"))


def _wo_excess_percentage(work_order_name):
	"""Look up Process.wo_excess_allowed_percentage for the WO's process.
	Returns 0 when no process is set or the field is unset.

	Coarser than the PO side (Item-level): one Process-level percentage applies
	uniformly to every receivable line on the WO. Intentional per the design —
	rework/excess at WO time is driven by process capability, not by the
	individual items the process consumes/produces.
	"""
	if not work_order_name:
		return 0
	process = frappe.db.get_value("Work Order", work_order_name, "process_name")
	if not process:
		return 0
	return flt(frappe.get_cached_value("Process", process, "wo_excess_allowed_percentage"))


def _remaining_receivable_allowance(ordered_qty, pending_quantity, excess_pct):
	ordered_qty = flt(ordered_qty)
	received_so_far = ordered_qty - flt(pending_quantity)
	return ordered_qty * (1 + flt(excess_pct) / 100) - received_so_far


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
	excess_pct = _wo_excess_percentage(wo.name)
	rows = []
	for row in wo.get("receivables") or []:
		pending = flt(row.pending_quantity)
		max_receivable = max(
			flt(_remaining_receivable_allowance(row.qty, pending, excess_pct)),
			0,
		)
		base_row_index = row.row_index if row.row_index not in (None, "") else row.idx - 1
		for received_type in received_types:
			key = _receipt_split_key(row.name, row.item_variant, received_type)
			if existing_rows is None:
				quantity = (
					pending
					if pending > 0 and (not received_type or received_type == default_received_type)
					else 0
				)
			else:
				quantity = existing_quantities.get(key, 0)
			if max_receivable <= 0 and flt(quantity) <= 0:
				continue
			out = {
				"item_variant": row.item_variant,
				"quantity": quantity,
				"uom": row.uom,
				"pending_quantity": pending,
				"max_receivable_quantity": max_receivable,
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
		excess_pct = _po_excess_percentage(row.item_variant)
		max_receivable = max(
			flt(_remaining_receivable_allowance(row.qty, pending, excess_pct)),
			0,
		)
		if existing_rows is None:
			quantity = pending if pending > 0 else 0
		else:
			quantity = existing_quantities.get(row.name, pending if pending > 0 else 0)
		if max_receivable <= 0 and flt(quantity) <= 0:
			continue
		rows.append({
			"item_variant": row.item_variant,
			"quantity": quantity,
			"uom": row.uom,
			"stock_uom": row.stock_uom,
			"conversion_factor": row.conversion_factor,
			"stock_qty": flt(quantity) * flt(row.conversion_factor or 1),
			"pending_quantity": pending,
			"max_receivable_quantity": max_receivable,
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


@frappe.whitelist()
def make_grn_completion(doc_name):
	frappe.has_permission("Stock Entry", "create", throw=True)
	grn = frappe.get_doc("Goods Received Note", doc_name)
	if grn.docstatus != 1:
		frappe.throw(_("Goods Received Note must be submitted."))
	if not grn.is_internal_unit:
		frappe.throw(_("Goods Received Note is not an internal unit transfer."))
	if grn.transfer_complete:
		frappe.throw(_("Transfer is already complete for this Goods Received Note."))
	pending_draft = frappe.db.exists(
		"Stock Entry",
		{"against": "Goods Received Note", "against_id": doc_name, "purpose": "GRN Completion", "docstatus": 0},
	)
	if pending_draft:
		frappe.throw(
			_("A draft GRN Completion Stock Entry already exists ({0}). Submit or delete it before creating a new one.").format(pending_draft)
		)

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	transit_warehouse = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
	if not transit_warehouse:
		frappe.throw(_("Transit Warehouse must be set in YRP Stock Settings."))

	items = []
	for item in grn.items:
		pending = flt(item.quantity) - flt(item.ste_received_quantity)
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
			"rate": flt(item.rate),
			"table_index": item.table_index,
			"row_index": item.row_index,
			"against": "Goods Received Note Item",
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
	ste.purpose = "GRN Completion"
	ste.against = "Goods Received Note"
	ste.against_id = doc_name
	ste.from_warehouse = transit_warehouse
	ste.to_warehouse = grn.to_warehouse
	# from_warehouse is set for display; Stock Entry.get_sl_entries reads transit
	# directly from YRP Stock Settings for "GRN Completion" (same as "DC Completion").
	for row_data in items:
		ste.append("items", row_data)
	ste.flags.allow_from_grn = True
	ste.insert(ignore_permissions=True)
	return ste.name
