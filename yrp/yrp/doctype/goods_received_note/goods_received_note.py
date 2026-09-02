from collections import defaultdict
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

from yrp.yrp.doctype.delivery_challan.delivery_challan import (
	_apply_dimension_values_to_rows,
	_copy_header_dimensions_to_items,
	_copy_production_group_dimensions_from_source,
	_get_production_group_dimensions,
	_get_warehouse_for_supplier,
	_normal_json,
	_sle_base,
	_update_work_order_correction_status,
	_update_work_order_sre_delivered_qty,
	_update_work_order_status,
)


QTY_TOLERANCE = 0.0001


def _strip_zero_entries(item_details):
	"""Drop entries whose every cell qty is 0 from the grouped item_details JSON.

	The editor pads received-type splits (one entry per received type) at qty 0 so
	the user can split into them; `ungroup_items_from_ui` already skips those zero
	rows for the flat `items` table, but the grouped twin (`item_details`, a stored
	Long Text) would otherwise persist them. Strip here so the DB never carries
	zero-qty received-type rows — `onload` rebuilds the padded editable view from
	`items` on the next load, so nothing downstream needs the padding.
	"""
	if isinstance(item_details, str):
		try:
			data = json.loads(item_details or "[]")
		except (ValueError, json.JSONDecodeError):
			return item_details
	else:
		data = item_details or []
	out = []
	for group in data:
		kept = [
			entry
			for entry in (group.get("items") or [])
			if any(flt((v or {}).get("qty")) for v in (entry.get("values") or {}).values())
		]
		if kept:
			out.append({**group, "items": kept})
	return frappe.as_json(out)


class GoodsReceivedNote(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_correction_items_for_ui, group_items_for_ui

		rows = self.get("items") or []
		if (
			self.docstatus == 0
			and self.against == "Work Order"
			and self.against_id
			and rows
			and not self.get("is_return")
		):
			from yrp.stock.dimensions import apply_dimension_defaults

			wo = frappe.get_doc("Work Order", self.against_id)
			delivery_challan = (
				frappe.get_doc("Delivery Challan", self.delivery_challan)
				if self.delivery_challan else None
			)
			rows = _pending_receivable_rows(wo, existing_rows=rows, delivery_challan=delivery_challan)
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
		self.set_onload(
			"correction_item_details",
			group_correction_items_for_ui(self.get("correction_items") or [], "Goods Received Note"),
		)

	def before_validate(self):
		self.sync_vue_item_details()
		self.sync_vue_correction_item_details()
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
		prepare_grn_deliverable_valuation(self)
		self.apply_freight_allocation()

	def on_submit(self):
		self.update_source_pending()
		self.make_stock_ledger_entries()
		self.set_current_valuation_fields()
		self.update_rework_delivery_challan_items()

	def set_current_valuation_fields(self):
		"""Initialize the mutable/current view while preserving submitted rate."""
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			if not row.meta.get_field("current_valuation_rate"):
				continue
			qty = flt(row.stock_qty) or flt(row.quantity)
			values = {
				"current_valuation_rate": flt(row.rate),
				"current_valuation_value": qty * flt(row.rate),
			}
			row.update(values)
			frappe.db.set_value(
				row.doctype,
				row.name,
				values,
				update_modified=False,
			)

	def before_cancel(self):
		self.validate_no_purchase_invoice()
		self.validate_closed_purchase_order()
		self.validate_age_limit()
		self.validate_no_inspection_entry()
		if self.get("is_return") and self.get("delivery_challan"):
			_validate_return_cancellation(self)
		self.ignore_linked_doctypes = (
			"Stock Ledger Entry",
			"Repost Item Valuation",
			"Stock Valuation Adjustment",
			"Stock Entry",
			"Inspection Entry",
		)
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
		self.update_rework_delivery_challan_items(cancel=True)
		if self.is_internal_unit:
			self.db_set("ste_transferred", 0)
			self.db_set("ste_transferred_percent", 0)
			self.db_set("transfer_complete", 0)

	def set_missing_values(self):
		from yrp.stock.utils import apply_posting_datetime

		# Posting date/time follow the "Edit Posting Date and Time" checkbox
		# (stamped to now unless ticked) — ERPNext set_posting_time semantics.
		apply_posting_datetime(self)
		if not self.against_id:
			return

		if self.against == "Work Order":
			wo = frappe.get_cached_doc("Work Order", self.against_id)
			if self.get("is_return"):
				self._set_return_missing_values(wo)
				return
			if self.meta.get_field("is_rework"):
				self.is_rework = wo.is_rework
			self.process_name = self.process_name or wo.process_name
			self.item = self.item or wo.item
			self.production_detail = self.production_detail or wo.production_detail
			self.supplier = self.supplier or wo.supplier
			self.delivery_location = self.delivery_location or wo.delivery_location
			self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(wo.supplier)
			self.to_warehouse = self.to_warehouse or _get_warehouse_for_supplier(
				self.delivery_location
			)
			_copy_production_group_dimensions_from_source(self, wo)
		elif self.against == "Purchase Order":
			po = frappe.get_cached_doc("Purchase Order", self.against_id)
			self.supplier = self.supplier or po.supplier
			self.from_warehouse = self.from_warehouse or _get_warehouse_for_supplier(po.supplier)
			self.to_warehouse = self.to_warehouse or po.delivery_warehouse
			_copy_production_group_dimensions_from_source(self, po)

	def _set_return_missing_values(self, work_order):
		if not self.delivery_challan:
			return
		delivery_challan = frappe.get_cached_doc("Delivery Challan", self.delivery_challan)
		self.process_name = work_order.process_name
		self.item = work_order.item
		self.production_detail = work_order.production_detail
		self.is_rework = work_order.is_rework
		self.supplier = delivery_challan.supplier
		self.delivery_location = delivery_challan.from_location
		self.from_warehouse = delivery_challan.to_warehouse
		self.to_warehouse = delivery_challan.from_warehouse
		self.freight_charges = 0
		_copy_production_group_dimensions_from_source(self, delivery_challan)

	def sync_vue_item_details(self):
		if self.docstatus != 0 or not self.get("item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		rows = ungroup_items_from_ui(self.item_details, "Goods Received Note")
		self.set("items", [])
		for row in rows:
			self.append("items", row)
		# Don't persist padded all-zero received-type entries in the grouped twin —
		# they aren't real receipts (ungroup already dropped them from `items`).
		self.item_details = _strip_zero_entries(self.item_details)

	def sync_vue_correction_item_details(self):
		if self.docstatus != 0 or not self.get("correction_item_details"):
			return
		from yrp.stock.save_stock_items import ungroup_correction_items_from_ui

		rows = ungroup_correction_items_from_ui(self.correction_item_details, "Goods Received Note")
		self.set("correction_items", [])
		for row in rows:
			self.append("correction_items", row)

	def apply_dimensions(self):
		_copy_header_dimensions_to_items(self)
		from yrp.stock.dimensions import apply_dimension_defaults, get_dimension_fieldnames

		correction_items = self.get("correction_items") or []
		if correction_items:
			header_dims = {
				fn: self.get(fn)
				for fn in get_dimension_fieldnames()
				if self.meta.get_field(fn) and self.get(fn)
			}
			_apply_dimension_values_to_rows(correction_items, header_dims)
		apply_dimension_defaults(self.get("items") or [])
		apply_dimension_defaults(correction_items)

	def set_item_defaults(self):
		from yrp.stock.uom import apply_item_uom

		wo = frappe.get_doc("Work Order", self.against_id) if self.against == "Work Order" and self.against_id else None
		delivery_challan = (
			frappe.get_doc("Delivery Challan", self.delivery_challan)
			if self.delivery_challan else None
		)
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			apply_item_uom(row)
			row.stock_qty = flt(row.quantity) * flt(row.conversion_factor)
			if self.get("is_return"):
				rate = _get_return_source_rate(self, row)
				row.rate = flt(rate)
				row.amount = flt(row.stock_qty or row.quantity) * flt(rate)
			elif row.get("work_order_correction"):
				# Correction receivables keep their own receivable cost as rate;
				# no DC material-rate blend in v1.
				row.amount = flt(row.stock_qty or row.quantity) * flt(row.rate)
			elif wo:
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
		if self.against == "Work Order":
			is_rework = frappe.db.get_value("Work Order", self.against_id, "is_rework")
			if is_rework and not self.delivery_challan:
				frappe.throw(_("Delivery Challan is required for rework Goods Received Note."))
		if self.delivery_challan:
			dc_work_order, dc_docstatus, dc_internal, dc_transfer_complete = frappe.db.get_value(
				"Delivery Challan",
				self.delivery_challan,
				["work_order", "docstatus", "is_internal_unit", "transfer_complete"],
			)
			if dc_docstatus != 1:
				frappe.throw(_("Delivery Challan {0} must be submitted.").format(self.delivery_challan))
			if dc_work_order != self.against_id:
				frappe.throw(_("Delivery Challan must belong to the same Work Order."))
			if self.get("is_return") and dc_internal and not dc_transfer_complete:
				frappe.throw(
					_("Complete the internal-unit transfer for Delivery Challan {0} before returning items.").format(
						self.delivery_challan
					)
				)
		if self.get("is_return"):
			if self.against != "Work Order" or not self.delivery_challan:
				frappe.throw(_("A return GRN must be against a Work Order and Delivery Challan."))
			if self.get("correction_items"):
				frappe.throw(_("Correction Items are not supported on a Delivery Challan return."))

	def validate_items(self):
		# Correction-only GRNs are valid: a fully-received WO can still owe its
		# Work Order Correction quantities (user, 2026-07-09).
		if not (self.get("items") or self.get("correction_items")):
			frappe.throw(_("At least one receivable or correction item is required."))
		if self.against == "Work Order" and not self.from_warehouse:
			frappe.throw(_("From Warehouse is required."))
		if not self.to_warehouse:
			frappe.throw(_("To Warehouse is required."))
		if self.from_warehouse and self.from_warehouse == self.to_warehouse:
			frappe.throw(_("From Warehouse and To Warehouse must be different."))
		for row in (self.get("items") or []) + (self.get("correction_items") or []):
			if not row.item_variant:
				frappe.throw(_("Row {0}: Item Variant is required.").format(row.idx))
			if flt(row.quantity) <= 0:
				frappe.throw(_("Row {0}: Quantity must be greater than zero.").format(row.idx))

	def calculate_totals(self):
		all_rows = (self.get("items") or []) + (self.get("correction_items") or [])
		self.total_received_quantity = sum(flt(row.quantity) for row in all_rows)
		self.total = sum(flt(row.amount) for row in all_rows)

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
		if self.get("is_return"):
			self.freight_charges = 0
			self.total = sum(flt(row.amount) for row in self.items)
			self.flags.freight_allocated = True
			return
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
		stock_qty. PO receipts always resolve the source PO item's net rate so its
		discount percentage reduces stock value. For amended WO GRNs, copied child
		rows may already include old freight in row.rate, so the source rate is
		resolved again first.
		"""
		for row in self.items:
			stock_qty = flt(row.stock_qty) or (flt(row.quantity) * flt(row.conversion_factor or 1))
			if stock_qty <= 0:
				continue

			base_rate = (
				self._get_source_base_rate(row)
				if self.against == "Purchase Order" or self.amended_from
				else None
			)
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
				po_item = frappe.db.get_value(
					"Purchase Order Item",
					row.ref_docname,
					["rate", "discount_percentage"],
					as_dict=True,
				)
				if po_item:
					return _purchase_order_item_net_rate(po_item)
			po = frappe.get_doc("Purchase Order", self.against_id)
			target = _find_matching_purchase_order_item(po.items, row)
			return _purchase_order_item_net_rate(target) if target else None

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
		if self.get("is_return"):
			_validate_return_quantities(self)
			return
		if self.against == "Purchase Order":
			self.validate_against_purchase_order_pending()
			return
		self.validate_against_work_order_pending()
		self.validate_rework_delivery_challan_pending()

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

		self.validate_against_correction_pending(excess_pct)

	def validate_against_correction_pending(self, excess_pct):
		# Correction receivables: same matching + excess-allowance gate as the WO's
		# own receivables, but each row is scoped to its own Work Order Correction
		# (row.work_order_correction) and matched against that correction's
		# receivables rather than the WO's.
		corr_cache = {}
		totals_by_receivable = defaultdict(float)
		receivable_by_name = {}
		for row in self.get("correction_items") or []:
			name = row.work_order_correction
			if not name:
				frappe.throw(_("Row {0}: correction item missing Work Order Correction.").format(row.idx))
			corr = corr_cache.get(name)
			if corr is None:
				corr = frappe.get_doc("Work Order Correction", name)
				corr_cache[name] = corr
			if corr.work_order != self.against_id:
				frappe.throw(
					_("Row {0}: Work Order Correction {1} belongs to Work Order {2}, not {3}.").format(
						row.idx, name, corr.work_order, self.against_id
					)
				)
			target = _find_matching_receivable(corr.receivables, row)
			if not target:
				frappe.throw(
					_("Row {0}: no matching correction receivable found for {1}.").format(
						row.idx, row.item_variant
					)
				)
			totals_by_receivable[(name, target.name)] += flt(row.quantity)
			receivable_by_name[(name, target.name)] = target
			row.ref_doctype = "Work Order Receivables"
			row.ref_docname = target.name
			row.pending_quantity = target.pending_quantity

		for (name, receivable_name), total_qty in totals_by_receivable.items():
			target = receivable_by_name[(name, receivable_name)]
			ordered = flt(target.qty)
			allowance = _remaining_receivable_allowance(ordered, target.pending_quantity, excess_pct)
			if total_qty > allowance + 0.0001:
				frappe.throw(
					_("Received qty {0} exceeds allowance {1} for {2} (ordered {3}, excess allowance {4}%).").format(
						flt(total_qty), flt(allowance), target.item_variant, ordered, flt(excess_pct)
					)
				)
			for row in self.get("correction_items") or []:
				if row.work_order_correction == name and _find_matching_receivable([target], row):
					row.max_receivable_quantity = max(flt(allowance), 0)

	def validate_rework_delivery_challan_pending(self):
		if not _is_rework_work_order(self.against_id):
			return
		if not self.delivery_challan:
			frappe.throw(_("Delivery Challan is required for rework Goods Received Note."))

		from yrp.stock.utils import get_stock_balance

		dc = frappe.get_doc("Delivery Challan", self.delivery_challan)
		dc_items = {row.name: row for row in dc.items}
		totals_by_dc_item = defaultdict(float)
		for row in self.items:
			if not row.delivery_challan_item:
				frappe.throw(_("Row {0}: Delivery Challan Item is required for rework GRN.").format(row.idx))
			dc_item = dc_items.get(row.delivery_challan_item)
			if not dc_item:
				frappe.throw(_("Row {0}: Delivery Challan Item must belong to {1}.").format(row.idx, dc.name))
			if dc_item.item_variant != row.item_variant:
				frappe.throw(_("Row {0}: Item must match Delivery Challan Item {1}.").format(row.idx, dc_item.name))
			totals_by_dc_item[dc_item.name] += flt(row.quantity)

		for dc_item_name, qty in totals_by_dc_item.items():
			dc_item = dc_items[dc_item_name]
			pending = flt(dc_item.delivered_quantity or dc_item.qty) - flt(dc_item.received_quantity)
			if qty > pending + 0.0001:
				frappe.throw(
					_("Received qty {0} exceeds pending rework return qty {1} for Delivery Challan Item {2}.").format(
						flt(qty), flt(pending), dc_item.name
					)
				)
			dims = _delivery_challan_item_dimension_values(dc_item)
			balance = get_stock_balance(
				dc_item.item_variant,
				self.from_warehouse,
				posting_date=self.posting_date,
				posting_time=self.posting_time,
				**dims,
			)
			if flt(balance) + 0.0001 < qty:
				frappe.throw(
					_("Insufficient supplier-side rework stock for {0}: available {1}, required {2}.").format(
						dc_item.item_variant, flt(balance), flt(qty)
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
		if self.get("is_return"):
			_update_returned_deliverables(self, cancel=cancel)
			return
		if self.against == "Purchase Order":
			self.update_purchase_order_items(cancel=cancel)
			return
		self.update_work_order_receivables(cancel=cancel)

	def update_rework_delivery_challan_items(self, cancel=False):
		if self.get("is_return"):
			return
		if not _is_rework_work_order(self.against_id):
			return
		totals = defaultdict(float)
		for row in self.items:
			if row.delivery_challan_item:
				totals[row.delivery_challan_item] += flt(row.quantity)
		for dc_item_name, qty in totals.items():
			current = flt(frappe.db.get_value("Delivery Challan Item", dc_item_name, "received_quantity"))
			received = current - qty if cancel else current + qty
			frappe.db.set_value(
				"Delivery Challan Item",
				dc_item_name,
				"received_quantity",
				flt(received),
				update_modified=False,
			)

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

		self.update_correction_receivables(cancel=cancel)

	def update_correction_receivables(self, cancel=False):
		# Draw down (or restore, on cancel) each correction's own receivable
		# pending, routed by row.work_order_correction. Negative pending is allowed
		# when an excess receipt was permitted — no clamp, mirroring the WO branch.
		by_corr = {}
		for row in self.get("correction_items") or []:
			if row.work_order_correction:
				by_corr.setdefault(row.work_order_correction, []).append(row)
		for name, rows in by_corr.items():
			corr = frappe.get_doc("Work Order Correction", name)
			touched = False
			for row in rows:
				target = _find_matching_receivable(corr.receivables, row)
				if not target:
					continue
				qty = flt(row.quantity)
				pending = flt(target.pending_quantity) + qty if cancel else flt(target.pending_quantity) - qty
				target.db_set("pending_quantity", flt(pending), update_modified=False)
				touched = True
			if touched:
				_update_work_order_correction_status(name)

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

		if self.get("is_return"):
			make_sl_entries(_return_stock_ledger_entries(self), cancel=cancel)
			return

		destination = self.to_warehouse
		if self.is_internal_unit:
			destination = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
			if not destination:
				frappe.throw(
					_("Transit Warehouse must be set in YRP Stock Settings for internal-unit Goods Received Note.")
				)

		if has_mapped_grn_deliverables(self):
			make_production_grn_stock_ledger_entries(self, destination, cancel=cancel)
			return

		make_sl_entries(_grn_receipt_stock_entries(self, destination), cancel=cancel)

	def compute_internal_unit(self):
		"""Internal-unit GRN: supplier (sender) and delivery_location (receiver) are both
		company locations. Mirrors DC's compute_internal_unit but uses (supplier,
		delivery_location) instead of DC's (from_location, supplier). PO-only GRNs lack
		delivery_location and stay non-internal."""
		if self.get("is_return"):
			# F15 parity: the reverse movement is posted directly from the DC's
			# destination warehouse back to its source warehouse. Internal-unit DCs
			# must complete transit first (validated above).
			self.is_internal_unit = 0
			return
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


def _get_return_delivery_challan(grn):
	if not grn.get("delivery_challan"):
		return None
	# A return can be created and submitted in the same request as its source
	# DC. Use the authoritative child rows instead of a request-cache snapshot;
	# stale DC items would silently skip the Work Order pending update.
	return frappe.get_doc("Delivery Challan", grn.delivery_challan)


def _get_return_dc_item(grn, row):
	delivery_challan = _get_return_delivery_challan(grn)
	if not delivery_challan or not row.get("delivery_challan_item"):
		return None
	for dc_item in delivery_challan.get("items") or []:
		if dc_item.name == row.delivery_challan_item:
			return dc_item
	return None


def _get_return_deliverable(work_order, dc_item):
	if not dc_item or dc_item.get("ref_doctype") != "Work Order Deliverables":
		return None
	for deliverable in work_order.get("deliverables") or []:
		if deliverable.name == dc_item.get("ref_docname"):
			return deliverable
	return None


def _return_source_dimension_values(grn, dc_item):
	from yrp.stock.dimensions import get_dimension_fieldnames

	delivery_challan = _get_return_delivery_challan(grn)
	if not delivery_challan or not dc_item:
		return {}
	base = _sle_base(delivery_challan, dc_item)
	return {fieldname: base.get(fieldname) for fieldname in get_dimension_fieldnames()}


def _get_return_source_rate(grn, row):
	from yrp.stock.utils import get_stock_balance

	dc_item = _get_return_dc_item(grn, row)
	if not dc_item or not grn.get("from_warehouse"):
		return 0
	_dimensions = _return_source_dimension_values(grn, dc_item)
	_stock_qty, valuation_rate = get_stock_balance(
		dc_item.item_variant,
		grn.from_warehouse,
		posting_date=grn.posting_date,
		posting_time=grn.posting_time,
		with_valuation_rate=True,
		**_dimensions,
	)
	return flt(valuation_rate)


def _submitted_return_quantities(delivery_challan, *, exclude_grn=None):
	filters = {
		"against": "Work Order",
		"delivery_challan": delivery_challan,
		"is_return": 1,
		"docstatus": 1,
	}
	return_grns = frappe.get_all("Goods Received Note", filters=filters, pluck="name")
	if exclude_grn:
		return_grns = [name for name in return_grns if name != exclude_grn]
	if not return_grns:
		return {}
	quantities = defaultdict(float)
	for row in frappe.get_all(
		"Goods Received Note Item",
		filters={
			"parent": ["in", return_grns],
			"parentfield": "items",
			"parenttype": "Goods Received Note",
		},
		fields=["delivery_challan_item", "quantity"],
	):
		if row.delivery_challan_item:
			quantities[row.delivery_challan_item] += flt(row.quantity)
	return dict(quantities)


def _validate_return_quantities(grn):
	from yrp.stock.dimensions import get_dimension_fieldnames
	from yrp.stock.utils import get_stock_balance

	delivery_challan = _get_return_delivery_challan(grn)
	if not delivery_challan:
		frappe.throw(_("Delivery Challan is required for a return GRN."))
	work_order = frappe.get_doc("Work Order", grn.against_id)
	dc_items = {row.name: row for row in delivery_challan.get("items") or []}
	previous_returns = _submitted_return_quantities(
		delivery_challan.name,
		exclude_grn=grn.name,
	)
	requested_by_dc_item = defaultdict(float)
	requested_by_deliverable = defaultdict(float)
	deliverables = {}
	stock_buckets = {}
	dimension_fields = get_dimension_fieldnames()

	for row in grn.get("items") or []:
		dc_item = dc_items.get(row.get("delivery_challan_item"))
		if not dc_item:
			frappe.throw(
				_("Row {0}: Delivery Challan Item must belong to {1}.").format(
					row.idx, delivery_challan.name
				)
			)
		deliverable = _get_return_deliverable(work_order, dc_item)
		if not deliverable:
			frappe.throw(
				_("Row {0}: Delivery Challan Item {1} is not linked to a Work Order Deliverable.").format(
					row.idx, dc_item.name
				)
			)
		if row.item_variant != dc_item.item_variant or _normal_json(
			row.get("set_combination")
		) != _normal_json(dc_item.get("set_combination")):
			frappe.throw(
				_("Row {0}: item and set combination must match Delivery Challan Item {1}.").format(
					row.idx, dc_item.name
				)
			)

		source_dimensions = _return_source_dimension_values(grn, dc_item)
		incoming_base = _sle_base(grn, row)
		for fieldname in dimension_fields:
			# F15 permits the returned stock to be classified into a selected
			# Received Type. Every other configured dimension stays identical.
			if fieldname == "received_type":
				continue
			if incoming_base.get(fieldname) != source_dimensions.get(fieldname):
				frappe.throw(
					_("Row {0}: Stock Dimension {1} must match Delivery Challan Item {2}.").format(
						row.idx, fieldname, dc_item.name
					)
				)

		row.ref_doctype = "Work Order Deliverables"
		row.ref_docname = deliverable.name
		requested_by_dc_item[dc_item.name] += flt(row.quantity)
		requested_by_deliverable[deliverable.name] += flt(row.quantity)
		deliverables[deliverable.name] = deliverable

		stock_qty = flt(row.stock_qty) or flt(row.quantity)
		dimension_key = tuple(source_dimensions.get(fieldname) for fieldname in dimension_fields)
		bucket_key = (dc_item.item_variant, grn.from_warehouse, dimension_key)
		bucket = stock_buckets.setdefault(
			bucket_key,
			{
				"item": dc_item.item_variant,
				"stock_qty": 0.0,
				"dimensions": source_dimensions,
			},
		)
		bucket["stock_qty"] += stock_qty

	for dc_item_name, return_qty in requested_by_dc_item.items():
		dc_item = dc_items[dc_item_name]
		dispatched = flt(dc_item.delivered_quantity or dc_item.qty)
		already_returned = flt(previous_returns.get(dc_item_name))
		remaining_for_dc = max(dispatched - already_returned, 0)
		if return_qty > remaining_for_dc + QTY_TOLERANCE:
			frappe.throw(
				_("Return qty {0} exceeds the remaining DC qty {1} for row {2}.").format(
					flt(return_qty), flt(remaining_for_dc), dc_item_name
				)
			)

	for deliverable_name, return_qty in requested_by_deliverable.items():
		deliverable = deliverables[deliverable_name]
		net_delivered = flt(deliverable.qty) - flt(deliverable.pending_quantity)
		unconsumed = max(net_delivered - flt(deliverable.stock_update), 0)
		if return_qty > unconsumed + QTY_TOLERANCE:
			frappe.throw(
				_("Return qty {0} exceeds unconsumed qty {1} for {2}.").format(
					flt(return_qty), flt(unconsumed), deliverable.item_variant
				)
			)

	for bucket in stock_buckets.values():
		available = get_stock_balance(
			bucket["item"],
			grn.from_warehouse,
			posting_date=grn.posting_date,
			posting_time=grn.posting_time,
			**bucket["dimensions"],
		)
		if flt(available) + QTY_TOLERANCE < flt(bucket["stock_qty"]):
			frappe.throw(
				_("Insufficient return stock for {0}: available {1}, required {2}.").format(
					bucket["item"], flt(available), flt(bucket["stock_qty"])
				)
			)


def _update_returned_deliverables(grn, *, cancel):
	work_order = frappe.get_doc("Work Order", grn.against_id)
	quantities = defaultdict(float)
	stock_quantities = defaultdict(float)
	for row in grn.get("items") or []:
		dc_item = _get_return_dc_item(grn, row)
		deliverable = _get_return_deliverable(work_order, dc_item)
		if not deliverable:
			continue
		quantities[deliverable.name] += flt(row.quantity)
		stock_quantities[deliverable.name] += flt(row.stock_qty) or flt(row.quantity)

	changed = False
	for deliverable in work_order.get("deliverables") or []:
		quantity = quantities.get(deliverable.name)
		if not quantity:
			continue
		pending = (
			flt(deliverable.pending_quantity) - quantity
			if cancel
			else flt(deliverable.pending_quantity) + quantity
		)
		deliverable.db_set("pending_quantity", flt(pending), update_modified=False)
		_update_work_order_sre_delivered_qty(
			work_order.name,
			deliverable.name,
			stock_quantities[deliverable.name] if cancel else -stock_quantities[deliverable.name],
		)
		changed = True

	if changed:
		_update_work_order_status(work_order.name)


def _validate_return_cancellation(grn):
	"""Do not cancel returned qty that a later DC has already re-delivered."""
	deliverable_names = set()
	return_quantities = defaultdict(float)
	for row in grn.get("items") or []:
		dc_item = _get_return_dc_item(grn, row)
		if dc_item and dc_item.get("ref_doctype") == "Work Order Deliverables":
			deliverable_name = dc_item.get("ref_docname")
			deliverable_names.add(deliverable_name)
			return_quantities[deliverable_name] += flt(row.quantity)
	if not deliverable_names:
		return

	# pending_quantity contains the stock made available again by submitted
	# returns. If a later delivery has already consumed any part of this GRN's
	# quantity, cancelling it would drive the deliverable pending balance below
	# zero even when the later DC cannot be identified reliably by timestamps.
	for deliverable_name, return_quantity in return_quantities.items():
		pending_quantity, item_variant = frappe.db.get_value(
			"Work Order Deliverables",
			deliverable_name,
			["pending_quantity", "item_variant"],
		)
		if flt(pending_quantity) + QTY_TOLERANCE < flt(return_quantity):
			frappe.throw(
				_(
					"Cannot cancel this return because {0} has already been re-delivered. "
					"Cancel the later Delivery Challan first."
				).format(item_variant)
			)

	return_posting = get_datetime(f"{grn.posting_date} {grn.posting_time}")
	return_created = get_datetime(grn.creation)
	later_delivery_challans = []
	for candidate in frappe.get_all(
		"Delivery Challan",
		filters={"work_order": grn.against_id, "docstatus": 1},
		fields=["name", "posting_date", "posting_time", "creation"],
	):
		candidate_posting = get_datetime(
			f"{candidate.posting_date} {candidate.posting_time}"
		)
		candidate_created = get_datetime(candidate.creation)
		if candidate_posting > return_posting or (
			candidate_posting == return_posting and candidate_created > return_created
		):
			later_delivery_challans.append(candidate.name)
	if not later_delivery_challans:
		return

	redelivered = defaultdict(float)
	for row in frappe.get_all(
		"Delivery Challan Item",
		filters={
			"parent": ["in", later_delivery_challans],
			"parenttype": "Delivery Challan",
			"parentfield": "items",
			"ref_doctype": "Work Order Deliverables",
			"ref_docname": ["in", list(deliverable_names)],
		},
		fields=["ref_docname", "delivered_quantity", "qty"],
	):
		redelivered[row.ref_docname] += flt(row.delivered_quantity or row.qty)

	redelivered_name = next(
		(name for name, quantity in redelivered.items() if quantity > QTY_TOLERANCE),
		None,
	)
	if redelivered_name:
		item_variant = frappe.db.get_value(
			"Work Order Deliverables", redelivered_name, "item_variant"
		)
		frappe.throw(
			_(
				"Cannot cancel this return because {0} has already been re-delivered. "
				"Cancel the later Delivery Challan first."
			).format(item_variant)
		)


def _return_stock_ledger_entries(grn):
	from yrp.stock.dimensions import get_dimension_fieldnames

	delivery_challan = _get_return_delivery_challan(grn)
	if not delivery_challan or not grn.from_warehouse or not grn.to_warehouse:
		frappe.throw(_("Return source and destination Warehouses are required."))
	dimension_fields = get_dimension_fieldnames()
	entries = []
	for row in grn.get("items") or []:
		stock_qty = flt(row.stock_qty) or flt(row.quantity)
		if stock_qty <= 0:
			continue
		dc_item = _get_return_dc_item(grn, row)
		if not dc_item:
			continue
		incoming = _sle_base(grn, row)
		outgoing = dict(incoming)
		source_dimensions = _return_source_dimension_values(grn, dc_item)
		for fieldname in dimension_fields:
			outgoing[fieldname] = source_dimensions.get(fieldname)
		rate = _get_return_source_rate(grn, row)
		transfer_key = f"Goods Received Note Return:{grn.name}:{row.name}"
		entries.extend(
			[
				{
					**outgoing,
					"warehouse": grn.from_warehouse,
					"qty": -stock_qty,
					"rate": 0,
					"outgoing_rate": flt(rate),
					"_transfer_key": transfer_key,
					"_transfer_role": "outgoing",
				},
				{
					**incoming,
					"warehouse": grn.to_warehouse,
					"qty": stock_qty,
					"rate": flt(rate),
					"_transfer_key": transfer_key,
					"_transfer_role": "incoming",
				},
			]
		)
	return entries


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
	from yrp.stock.dimensions import get_dimension_fieldnames

	if source_row.get("ref_doctype") == "Purchase Order Item" and source_row.get("ref_docname"):
		for row in rows:
			if row.name == source_row.get("ref_docname"):
				return row
	for row in rows:
		if row.item_variant != source_row.get("item_variant"):
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(source_row.get("set_combination")):
			if all(
				(row.get(fieldname) or None) == (source_row.get(fieldname) or None)
				for fieldname in get_dimension_fieldnames()
			):
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


def has_mapped_grn_deliverables(grn):
	"""Return whether an installed custom app supplied the base valuation contract.

	The custom app owns the ``grn_deliverables`` field and its child DocType.
	Base YRP deliberately activates only when that child schema provides the
	explicit ``goods_received_note_item`` mapping. This keeps older custom GRN
	deliverable tables working on their legacy path until they adopt the contract.
	"""
	field = grn.meta.get_field("grn_deliverables")
	rows = grn.get("grn_deliverables") or []
	if not field or not field.options or not rows:
		return False
	child_meta = frappe.get_meta(field.options)
	return bool(
		child_meta.get_field("goods_received_note_item")
		and all(row.get("goods_received_note_item") for row in rows)
	)


def prepare_grn_deliverable_valuation(grn):
	"""Set provisional output rates from mapped consumed-material rows.

	This runs before freight allocation. The authoritative consumed value is
	replaced with the actual FIFO/Moving Average value during ledger posting;
	the provisional value exists so validation, totals, and value-based freight
	allocation are deterministic before submission.
	"""
	if not has_mapped_grn_deliverables(grn):
		return
	if (
		grn.against != "Work Order"
		or grn.get("is_return")
		or grn.get("is_rework")
	):
		frappe.throw(_("Mapped GRN Deliverables are supported only for a regular Work Order receipt."))

	items = {row.name: row for row in grn.get("items") or []}
	material_value_by_output = defaultdict(float)
	for deliverable in grn.get("grn_deliverables") or []:
		output_name = deliverable.get("goods_received_note_item")
		if output_name not in items:
			frappe.throw(
				_("GRN Deliverable row {0} is not mapped to a received item on this GRN.").format(
					deliverable.idx
				)
			)
		stock_qty = flt(deliverable.get("stock_qty"))
		if stock_qty <= 0 or not deliverable.get("item_variant"):
			frappe.throw(
				_("GRN Deliverable row {0} has no calculated stock quantity or item.").format(
					deliverable.idx
				)
			)
		# Parse now so malformed or unknown dimension payloads cannot reach the
		# stock ledger after the document has started submitting.
		_grn_deliverable_dimensions(deliverable)
		material_value = stock_qty * flt(deliverable.get("valuation_rate"))
		material_value_by_output[output_name] += material_value
		_set_deliverable_value(deliverable, "material_value", material_value)

	wo = frappe.get_doc("Work Order", grn.against_id)
	for output_name, output in items.items():
		if output_name not in material_value_by_output:
			frappe.throw(
				_("Received item row {0} has no mapped GRN Deliverables.").format(output.idx)
			)
		stock_qty = flt(output.stock_qty) or flt(output.quantity)
		if stock_qty <= 0:
			continue
		material_rate = material_value_by_output[output_name] / stock_qty
		process_rate = get_work_order_process_rate(wo, output)
		output.rate = material_rate + process_rate
		output.amount = stock_qty * flt(output.rate)

	grn.calculate_totals()


def _set_deliverable_value(row, fieldname, value):
	if row.meta.get_field(fieldname):
		row.set(fieldname, value)


def _grn_deliverable_dimensions(row):
	from yrp.stock.dimensions import get_dimension_fieldnames

	raw = row.get("stock_dimensions") or {}
	if isinstance(raw, str):
		try:
			raw = frappe.parse_json(raw)
		except (TypeError, ValueError):
			frappe.throw(_("GRN Deliverable row {0} has invalid Stock Dimensions.").format(row.idx))
	if not isinstance(raw, dict):
		frappe.throw(_("GRN Deliverable row {0} has invalid Stock Dimensions.").format(row.idx))

	dimension_fieldnames = get_dimension_fieldnames()
	unknown = set(raw) - set(dimension_fieldnames)
	if unknown:
		frappe.throw(
			_("GRN Deliverable row {0} contains unknown Stock Dimensions: {1}.").format(
				row.idx, ", ".join(sorted(unknown))
			)
		)
	values = {}
	for fieldname in dimension_fieldnames:
		direct_value = row.get(fieldname) if row.meta.get_field(fieldname) else None
		values[fieldname] = direct_value if direct_value is not None else raw.get(fieldname)
	return values


def _grn_receipt_stock_entries(grn, destination, with_result_keys=False):
	entries = []
	for row in (grn.get("items") or []) + (grn.get("correction_items") or []):
		qty = flt(row.stock_qty) or flt(row.quantity)
		if qty <= 0:
			continue
		base = _sle_base(grn, row)
		# Correction receivables always post a plain destination-warehouse
		# receipt, even on a rework WO — the rework input-return SLE applies
		# only to the WO's own returned deliverables (v1).
		if _is_rework_work_order(grn.against_id) and not row.get("work_order_correction"):
			dc_item = _get_delivery_challan_item(row.delivery_challan_item)
			entries.append({
				**_rework_input_sle_base(grn, row, dc_item),
				"warehouse": grn.from_warehouse,
				"qty": -qty,
				"rate": 0,
				"outgoing_rate": flt(dc_item.valuation_rate or dc_item.rate or row.rate),
			})
		receipt_entry = {
			**base,
			"warehouse": destination,
			"qty": qty,
			"rate": flt(row.rate),
		}
		if with_result_keys:
			receipt_entry["_result_key"] = f"grn-output:{row.name}"
		entries.append(receipt_entry)
	return entries


def _group_grn_deliverable_consumption(grn):
	"""Group physical issues by Item + Warehouse + every Stock Dimension."""
	from yrp.stock.dimensions import get_dimension_fieldnames

	dimension_fieldnames = get_dimension_fieldnames()
	groups = {}
	for row in grn.get("grn_deliverables") or []:
		dimensions = _grn_deliverable_dimensions(row)
		key = (
			row.item_variant,
			grn.from_warehouse,
			row.get("stock_uom") or row.get("uom"),
			*(dimensions.get(fieldname) for fieldname in dimension_fieldnames),
		)
		group = groups.setdefault(
			key,
			{
				"rows": [],
				"stock_qty": 0.0,
				"dimensions": dimensions,
			},
		)
		group["rows"].append(row)
		group["stock_qty"] += flt(row.stock_qty)

	result = []
	for index, group in enumerate(groups.values(), 1):
		first = group["rows"][0]
		result_key = f"grn-consumption-{index}"
		entry = {
			"item": first.item_variant,
			"warehouse": grn.from_warehouse,
			"uom": first.get("stock_uom") or first.get("uom"),
			"voucher_type": grn.doctype,
			"voucher_no": grn.name,
			"voucher_detail_no": first.name,
			"posting_date": grn.posting_date,
			"posting_time": grn.posting_time,
			"qty": -flt(group["stock_qty"]),
			"rate": 0,
			"outgoing_rate": 0,
			"is_cancelled": 0,
			"_result_key": result_key,
		}
		entry.update(group["dimensions"])
		group["result_key"] = result_key
		group["entry"] = entry
		result.append(group)
	return result


def make_production_grn_stock_ledger_entries(grn, destination, cancel=False):
	"""Consume mapped inputs first, then receive outputs at their exact value."""
	from yrp.stock.stock_ledger import make_sl_entries
	from yrp.yrp_stock.doctype.stock_valuation_adjustment.stock_valuation_adjustment import (
		deactivate_production_links,
		register_production_links,
	)

	groups = _group_grn_deliverable_consumption(grn)
	consumption_entries = [group["entry"] for group in groups]
	if cancel:
		make_sl_entries(
			consumption_entries + _grn_receipt_stock_entries(grn, destination),
			cancel=True,
			force_inline=True,
		)
		deactivate_production_links(grn.doctype, grn.name)
		return

	result = make_sl_entries(
		consumption_entries,
		return_details=True,
		force_inline=True,
	)
	actual_value_by_output = defaultdict(float)
	for group in groups:
		detail = result["entries"].get(group["result_key"])
		if not detail:
			frappe.throw(
				_("Could not calculate consumed stock value for {0}.").format(
					group["rows"][0].item_variant
				)
			)
		group_value = flt(detail["value"])
		group_qty = flt(group["stock_qty"])
		actual_rate = group_value / group_qty if group_qty else 0
		assigned = 0.0
		for index, row in enumerate(group["rows"]):
			is_last = index == len(group["rows"]) - 1
			material_value = (
				group_value - assigned
				if is_last
				else actual_rate * flt(row.stock_qty)
			)
			assigned += material_value
			actual_value_by_output[row.goods_received_note_item] += material_value
			_persist_grn_deliverable_value(
				row,
				actual_rate,
				material_value,
				consumption_sle=detail["sle"],
			)

	wo = frappe.get_doc("Work Order", grn.against_id)
	for output in grn.get("items") or []:
		stock_qty = flt(output.stock_qty) or flt(output.quantity)
		if stock_qty <= 0:
			continue
		process_rate = get_work_order_process_rate(wo, output)
		output.rate = process_rate + (actual_value_by_output[output.name] / stock_qty)
		output.amount = stock_qty * flt(output.rate)

	# Re-run freight using the authoritative material values. This matters for
	# "By Value" allocation when a FIFO issue spans layers whose actual cost is
	# different from the provisional current balance rate.
	grn.flags.freight_allocated = False
	grn.apply_freight_allocation()
	for output in grn.get("items") or []:
		frappe.db.set_value(
			output.doctype,
			output.name,
			{"rate": output.rate, "amount": output.amount},
			update_modified=False,
		)

	grn.calculate_totals()
	frappe.db.set_value(
		grn.doctype,
		grn.name,
		{"total_received_quantity": grn.total_received_quantity, "total": grn.total},
		update_modified=False,
	)
	receipt_result = make_sl_entries(
		_grn_receipt_stock_entries(grn, destination, with_result_keys=True),
		return_details=True,
		force_inline=True,
	)
	output_sles = {
		row.name: (receipt_result["entries"].get(f"grn-output:{row.name}") or {}).get("sle")
		for row in grn.get("items") or []
	}
	production_links = []
	for row in grn.get("grn_deliverables") or []:
		output_sle = output_sles.get(row.goods_received_note_item)
		consumption_sle = row.get("consumption_sle")
		if not output_sle or not consumption_sle:
			frappe.throw(
				_("Could not persist valuation lineage for GRN Deliverable row {0}.").format(
					row.idx
				)
			)
		_persist_grn_deliverable_value(
			row,
			flt(row.get("valuation_rate")),
			flt(row.get("material_value")),
			consumption_sle=consumption_sle,
			output_receipt_sle=output_sle,
		)
		production_links.append(
			{
				"consumption_sle": consumption_sle,
				"output_receipt_sle": output_sle,
				"source_row": row.name,
				"input_quantity": flt(row.stock_qty),
				"allocation_weight": flt(row.stock_qty),
				"stock_dimensions": row.get("stock_dimensions") or "{}",
			}
		)
	register_production_links(grn.doctype, grn.name, production_links)


def _persist_grn_deliverable_value(
	row,
	valuation_rate,
	material_value,
	consumption_sle=None,
	output_receipt_sle=None,
):
	values = {}
	if row.meta.get_field("valuation_rate"):
		row.valuation_rate = valuation_rate
		values["valuation_rate"] = valuation_rate
	if row.meta.get_field("material_value"):
		row.material_value = material_value
		values["material_value"] = material_value
	if consumption_sle and row.meta.get_field("consumption_sle"):
		row.consumption_sle = consumption_sle
		values["consumption_sle"] = consumption_sle
	if output_receipt_sle and row.meta.get_field("output_receipt_sle"):
		row.output_receipt_sle = output_receipt_sle
		values["output_receipt_sle"] = output_receipt_sle
	if values:
		frappe.db.set_value(row.doctype, row.name, values, update_modified=False)


def get_work_order_grn_rate(wo, delivery_challan, row):
	process_rate = get_work_order_process_rate(wo, row)
	material_rate = get_delivery_challan_material_rate(delivery_challan, row)
	return flt(material_rate) + flt(process_rate)


def get_work_order_process_rate(wo, row):
	"""Return the Work Order process cost per received stock unit."""
	process_rate = 0
	target = _find_matching_receivable(wo.receivables, row)
	if target:
		process_rate = flt(target.cost)
		row.ref_doctype = "Work Order Receivables"
		row.ref_docname = target.name
		row.pending_quantity = target.pending_quantity
	return process_rate


def get_delivery_challan_material_rate(delivery_challan, row):
	if not delivery_challan:
		return 0
	dc_items = delivery_challan.get("items") or []
	if row.get("delivery_challan_item"):
		for dc_row in dc_items:
			if dc_row.name == row.get("delivery_challan_item"):
				return flt(dc_row.get("valuation_rate") or dc_row.get("rate"))
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
def get_work_order_defaults(work_order, delivery_challan=None):
	from yrp.stock.save_stock_items import group_correction_items_for_ui, group_items_for_ui
	from yrp.stock.dimensions import apply_dimension_defaults

	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("read")
	_validate_defaults_source(wo)
	dc = frappe.get_doc("Delivery Challan", delivery_challan) if delivery_challan else None
	if dc:
		dc.check_permission("read")
		_validate_defaults_source(dc)
		if dc.work_order != wo.name:
			frappe.throw(_("Delivery Challan must belong to the selected Work Order."))
	items = _pending_receivable_rows(wo, delivery_challan=dc)
	dimensions = _get_production_group_dimensions(wo)
	_apply_dimension_values_to_rows(items, dimensions)
	apply_dimension_defaults(items)
	correction_items = _pending_correction_receivable_rows(wo)
	_apply_dimension_values_to_rows(correction_items, dimensions)
	apply_dimension_defaults(correction_items)
	defaults = {
		"process_name": wo.process_name,
		"item": wo.item,
		"production_detail": wo.production_detail,
		"is_rework": wo.is_rework,
		"supplier": wo.supplier,
		"delivery_location": wo.delivery_location,
		"from_warehouse": _get_warehouse_for_supplier(wo.supplier),
		"to_warehouse": _get_warehouse_for_supplier(wo.delivery_location),
		"items": items,
		"item_details": group_items_for_ui(items, "Goods Received Note"),
		"correction_items": correction_items,
		"correction_item_details": group_correction_items_for_ui(correction_items, "Goods Received Note"),
	}
	defaults.update(dimensions)
	return defaults


@frappe.whitelist()
def get_purchase_order_defaults(purchase_order):
	from yrp.stock.dimensions import apply_dimension_defaults
	from yrp.stock.save_stock_items import group_items_for_ui

	po = frappe.get_doc("Purchase Order", purchase_order)
	po.check_permission("read")
	_validate_defaults_source(po)
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


def _validate_defaults_source(source):
	if source.docstatus != 1:
		frappe.throw(_("{0} {1} must be submitted.").format(source.doctype, source.name))
	if source.get("open_status") == "Close":
		frappe.throw(_("{0} {1} is closed.").format(source.doctype, source.name))


def _pending_receivable_rows(wo, existing_rows=None, delivery_challan=None):
	if delivery_challan and wo.get("is_rework"):
		return _pending_rework_receivable_rows(wo, delivery_challan, existing_rows)

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


def _pending_correction_receivable_rows(wo):
	# v1: correction receivables use a single default Received Type (applied later
	# by apply_dimension_defaults); the per-RT split UI used for WO receivables is
	# NOT applied to corrections.
	excess_pct = _wo_excess_percentage(wo.name)
	rows = []
	names = frappe.get_all(
		"Work Order Correction",
		filters={"work_order": wo.name, "docstatus": 1},
		pluck="name",
	)
	for name in names:
		corr = frappe.get_doc("Work Order Correction", name)
		for row in corr.get("receivables") or []:
			pending = flt(row.pending_quantity)
			if pending <= 0:
				continue
			max_receivable = max(
				flt(_remaining_receivable_allowance(row.qty, pending, excess_pct)),
				0,
			)
			rows.append({
				"item_variant": row.item_variant,
				"quantity": pending,
				"uom": row.uom,
				"pending_quantity": pending,
				"max_receivable_quantity": max_receivable,
				"ref_doctype": "Work Order Receivables",
				"ref_docname": row.name,
				"work_order_correction": name,
				"table_index": row.table_index,
				"row_index": row.row_index,
				"set_combination": row.set_combination,
				"rate": row.cost,
			})
	return rows


def _pending_rework_receivable_rows(wo, delivery_challan, existing_rows=None):
	received_types, default_received_type = _get_rework_output_received_type_options(existing_rows)
	existing_quantities = (
		_existing_rework_receipt_quantities(existing_rows, default_received_type)
		if existing_rows is not None
		else {}
	)
	# Per-DC-item RT visibility: fresh GRN (existing_rows is None or empty)
	# starts with only the default RT — user adds more via the editor "+" control.
	# When existing_rows are present, emit the RTs the user has saved so removed
	# rows stay removed across reloads.
	rts_seen_per_dc = {}
	if existing_rows:
		for row in existing_rows:
			dc_key = row.get("delivery_challan_item")
			rt = row.get("received_type") or default_received_type
			if not dc_key:
				continue
			rts_seen_per_dc.setdefault(dc_key, set()).add(rt)
	rows = []
	for dc_item in delivery_challan.get("items") or []:
		pending_dc = flt(dc_item.delivered_quantity or dc_item.qty) - flt(dc_item.received_quantity)
		if pending_dc <= 0 and existing_rows is None:
			continue
		target = _find_matching_receivable(wo.receivables, dc_item)
		if not target:
			continue
		# Drop any source-RT suffix on the DC item's row_index (e.g. "0::Adas"
		# -> "0") so all source-RT variants for the same parent-Item bucket
		# collapse into one Received-Type row in the GRN pivot UI.
		raw_row_index = dc_item.row_index if dc_item.row_index not in (None, "") else dc_item.idx - 1
		base_row_index = str(raw_row_index).split("::", 1)[0] if raw_row_index not in (None, "") else dc_item.idx - 1
		emit_rts = sorted(rts_seen_per_dc.get(dc_item.name, set())) if existing_rows is not None else []
		if not emit_rts:
			emit_rts = [default_received_type] if default_received_type else (received_types[:1] if received_types else [None])
		for received_type in emit_rts:
			key = _rework_receipt_split_key(target.name, dc_item.name, dc_item.item_variant, received_type)
			if existing_rows is None:
				quantity = (
					pending_dc
					if pending_dc > 0 and (not received_type or received_type == default_received_type)
					else 0
				)
			else:
				quantity = existing_quantities.get(key, 0)
			if pending_dc <= 0 and flt(quantity) <= 0:
				continue
			out = {
				"item_variant": dc_item.item_variant,
				"quantity": quantity,
				"uom": dc_item.uom,
				"stock_uom": dc_item.stock_uom,
				"conversion_factor": flt(dc_item.conversion_factor) or 1,
				"stock_qty": flt(quantity) * (flt(dc_item.conversion_factor) or 1),
				"pending_quantity": target.pending_quantity,
				"max_receivable_quantity": min(flt(target.pending_quantity), flt(pending_dc)),
				"ref_doctype": "Work Order Receivables",
				"ref_docname": target.name,
				"delivery_challan_item": dc_item.name,
				"table_index": dc_item.table_index,
				"row_index": f"{base_row_index}::{dc_item.name}::{received_type or ''}",
				"set_combination": dc_item.set_combination,
				"rate": flt(dc_item.valuation_rate or dc_item.rate),
			}
			out["row_index"] = f"{base_row_index}::{received_type or ''}"
			for fn, value in _delivery_challan_item_dimension_values(dc_item).items():
				if fn == "received_type":
					continue
				out[fn] = value
			if received_type:
				out["received_type"] = received_type
			rows.append(out)
	return _aggregate_rework_receivable_rows(rows)


def _aggregate_rework_receivable_rows(rows):
	"""When two DC items feed the same `(target receivable, variant, RT)` cell
	(e.g. one dispatched as Adas + one as Oil Mark, but both received back as
	Accepted), sum their max_receivable / pending / stock_qty into one row.

	Without this, `group_items_for_ui` silently overwrites one cell entry with
	the next at the same primary attribute value (size), so the GRN's Allowed
	column under-reports vs the WO's Pending by the lost cell's qty.

	Linkage trade-off: `delivery_challan_item` keeps the FIRST contributing
	DC item's name. On submit, only that DC item's `received_quantity` updates
	directly — bucket-level reconciliation is correct, per-DC-item is approximate.
	"""
	aggregated = {}
	order = []
	for row in rows:
		key = (
			row.get("ref_docname"),
			row.get("item_variant"),
			row.get("received_type") or "",
		)
		if key not in aggregated:
			aggregated[key] = {**row}
			order.append(key)
			continue
		existing = aggregated[key]
		existing["max_receivable_quantity"] = flt(
			existing.get("max_receivable_quantity")
		) + flt(row.get("max_receivable_quantity"))
		existing["stock_qty"] = flt(existing.get("stock_qty")) + flt(row.get("stock_qty"))
		existing["quantity"] = flt(existing.get("quantity")) + flt(row.get("quantity"))
	return [aggregated[k] for k in order]


def _pending_purchase_order_rows(po, existing_rows=None):
	from yrp.stock.dimensions import get_dimension_fieldnames

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
		out = {
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
			"rate": _purchase_order_item_net_rate(row),
		}
		for fieldname in get_dimension_fieldnames():
			if row.get(fieldname):
				out[fieldname] = row.get(fieldname)
		rows.append(out)
	return rows


def _purchase_order_item_net_rate(row):
	"""Return the PO item's discounted rate in its purchase UOM.

	GRN submit later divides the received net amount by received stock quantity,
	so partial receipts and non-stock purchase UOMs retain the same proportional
	discount without rounding the discount into each unit prematurely.
	"""
	discount_percentage = flt(row.get("discount_percentage"))
	return flt(row.get("rate")) * (1 - discount_percentage / 100)


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


@frappe.whitelist()
def get_rework_output_received_types(work_order=None):
	"""Whitelisted: list of Received Types the GRN UI can offer for adding RT
	rows on a rework GRN. Order: default, rejected, then others alphabetically.
	"""
	received_types, default_received_type = _get_rework_output_received_type_options(None)
	return {
		"received_types": received_types,
		"default_received_type": default_received_type,
	}


def _get_rework_output_received_type_options(existing_rows=None):
	from yrp.stock.dimensions import get_dimension_fieldnames

	if "received_type" not in get_dimension_fieldnames():
		return [None], None

	settings = frappe.get_cached_doc("YRP Stock Settings")
	default_received_type = settings.get("default_received_type")
	rejected_received_type = settings.get("default_rejected_received_type")
	received_type_rows = frappe.get_all(
		"Received Type",
		fields=["name", "is_default"],
		order_by="is_default desc, name asc",
	)
	# Rework supplier may classify returned stock at any RT — fully fixed
	# (default), partially fixed (e.g. previous defect resolved but a new one
	# detected), still defective, or unrecoverable (rejected). Include all RTs;
	# order: default first, rejected second, others alphabetically.
	received_types = []
	if default_received_type:
		received_types.append(default_received_type)
	if rejected_received_type and rejected_received_type not in received_types:
		received_types.append(rejected_received_type)
	for row in received_type_rows:
		if row.name not in received_types:
			received_types.append(row.name)
	if not received_types:
		return [None], None

	for row in existing_rows or []:
		received_type = row.get("received_type")
		if received_type and received_type not in received_types:
			received_types.append(received_type)
	if not default_received_type:
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


def _existing_rework_receipt_quantities(existing_rows, default_received_type):
	quantities = defaultdict(float)
	for row in existing_rows or []:
		received_type = row.get("received_type") or default_received_type
		key = _rework_receipt_split_key(
			row.get("ref_docname"),
			row.get("delivery_challan_item"),
			row.get("item_variant"),
			received_type,
		)
		quantities[key] += flt(row.get("quantity"))
	return quantities


def _rework_receipt_split_key(receivable_name, delivery_challan_item, item_variant, received_type):
	return (receivable_name, delivery_challan_item, item_variant, received_type or "")


def _is_rework_work_order(work_order):
	if not work_order:
		return False
	return bool(frappe.db.get_value("Work Order", work_order, "is_rework"))


def _get_delivery_challan_item(name):
	if not name:
		frappe.throw(_("Delivery Challan Item is required for rework GRN rows."))
	return frappe.get_doc("Delivery Challan Item", name)


def _delivery_challan_item_dimension_values(row):
	from yrp.stock.dimensions import get_dimension_fieldnames

	values = {}
	for fn in get_dimension_fieldnames():
		value = row.get(fn) if row.meta.get_field(fn) else None
		if fn == "received_type" and not value:
			value = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
		if value is not None:
			values[fn] = value
	return values


def _rework_input_sle_base(doc, row, dc_item):
	base = _sle_base(doc, row)
	base.update(_delivery_challan_item_dimension_values(dc_item))
	return base


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
