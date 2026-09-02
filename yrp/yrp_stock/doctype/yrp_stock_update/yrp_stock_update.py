"""Stock Update — simple add/reduce stock adjustment.

Add: increases stock qty at the given warehouse (incoming stock)
Reduce: decreases stock qty at the given warehouse (outgoing stock)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


MAX_RATE_LOOKUP_VALUES = 200
MAX_RATE_LOOKUP_ATTRIBUTES = 50


class YRPStockUpdate(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		grouped = group_items_for_ui(self.get("stock_update_details") or [], 'YRP Stock Update')
		self.set_onload("item_details", grouped)

	def before_validate(self):
		from yrp.stock.save_stock_items import ungroup_items_from_ui
		from yrp.stock.dimensions import apply_dimension_defaults

		if self.get("item_details") and self._action != "submit":
			rows = ungroup_items_from_ui(self.item_details, 'YRP Stock Update')
			self.set("stock_update_details", [])
			for r in rows:
				self.append("stock_update_details", r)
			self.set_rate_from_last_sle()
		apply_dimension_defaults(self.get("stock_update_details") or [])

	def set_rate_from_last_sle(self):
		"""Auto-fill rate from last uncancelled SLE for each item, scoped
		to (warehouse, valuation_dims) bucket (Gap #17)."""
		from yrp.stock.utils import get_last_sle_rate
		from yrp.stock.dimensions import get_dimension_fieldnames

		dim_fields = get_dimension_fieldnames()
		for row in self.stock_update_details:
			dim_filters = {fn: row.get(fn) for fn in dim_fields}
			rate, _matched = get_last_sle_rate(
				row.item_variant, warehouse=self.warehouse, **dim_filters
			)
			row.rate = flt(rate)

	def validate(self):
		if not self.stock_update_details:
			frappe.throw(_("At least one item is required"))
		from yrp.stock.uom import apply_item_uom
		from yrp.stock.utils import get_stock_balance
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		for row in self.stock_update_details:
			if not row.update_diff_qty or row.update_diff_qty <= 0:
				frappe.throw(_("Row {0}: qty must be > 0").format(row.idx))

			apply_item_uom(row)
			row.stock_qty = row.update_diff_qty * row.conversion_factor

			# For Reduce: enforce reservation-aware available stock (H.2).
			# Reservation is honored regardless of Item.allow_negative_stock;
			# the negative-stock flag only bypasses the actual_qty>=0 check.
			if self.update_type == "Reduce":
				from yrp.stock.utils import get_available_stock

				dim_filters = {fn: row.get(fn) for fn in dim_fields}
				actual = get_stock_balance(row.item_variant, self.warehouse, **dim_filters)
				available = get_available_stock(
					row.item_variant,
					self.warehouse,
					exclude_voucher_type='YRP Stock Update',
					exclude_voucher_name=self.name,
					**dim_filters,
				)
				row.available_stock = actual

				parent_item = frappe.get_cached_value(
					'YRP Item Variant', row.item_variant, "item"
				)
				item_allows_neg = bool(
					parent_item
					and frappe.get_cached_value(
						'YRP Item', parent_item, "allow_negative_stock"
					)
				)
				reserved = actual - available
				if not item_allows_neg and row.stock_qty > actual:
					frappe.throw(
						_("Row {0}: cannot reduce {1}, only {2} available").format(
							row.idx, row.stock_qty, actual
						)
					)
				# H.2: reservation is honored regardless of allow_negative_stock.
				# Only fire when there's an actual reservation to bypass; with
				# reserved=0, the negative-stock flag alone governs.
				if reserved > 0 and row.stock_qty > available:
					frappe.throw(
						_(
							"Row {0}: requested {1} exceeds available-after-reservation {2}. "
							"Negative-stock cannot bypass an active reservation."
						).format(row.idx, row.stock_qty, available)
					)

	# ------------------------------------------------------------------
	# Submit and Cancel — both use _build_sl_entries to avoid duplication
	# ------------------------------------------------------------------
	def before_submit(self):
		# Rate must reflect the last SLE at submit time on EVERY path. A direct
		# submit (no prior save) and an amend both run before_validate with
		# _action == 'submit', which skips the save-path re-sourcing; re-source
		# here so a stale/copied rate can never reach the Stock Ledger. Idempotent
		# with the save path.
		if self.stock_update_details:
			self.set_rate_from_last_sle()
		self.validate_incoming_rates()

	def validate_incoming_rates(self):
		"""Reject zero-value incoming stock while preserving zero-rate reductions."""
		if self.update_type != "Add":
			return

		for row in self.stock_update_details or []:
			if flt(row.rate) > 0:
				continue
			frappe.throw(
				_(
					"Row {0}: Valuation Rate must be greater than zero when adding stock for Item {1}. "
					"Zero-rate stock can only be reduced."
				).format(row.idx, row.item_variant),
				title=_("Positive Valuation Rate Required"),
			)

	def on_submit(self):
		from yrp.stock.stock_ledger import make_sl_entries
		make_sl_entries(self._build_sl_entries())

	def before_cancel(self):
		self.ignore_linked_doctypes = ('YRP Stock Ledger Entry', 'YRP Repost Item Valuation')

	def on_cancel(self):
		from yrp.stock.stock_ledger import make_sl_entries
		make_sl_entries(self._build_sl_entries(cancel=True), cancel=True)

	def _build_sl_entries(self, cancel=False):
		"""Build SLE dicts for submit or cancel — single source of truth.

		Submit (Add):    qty = +stock_qty, rate = rate,  outgoing_rate = 0
		Submit (Reduce): qty = -stock_qty, rate = 0,     outgoing_rate = rate

		Cancel reverses the direction:
		Cancel (Add):    qty = -stock_qty, rate = 0,     outgoing_rate = rate  (was incoming, now outgoing)
		Cancel (Reduce): qty = +stock_qty, rate = rate,  outgoing_rate = 0     (was outgoing, now incoming)
		"""
		from yrp.stock.dimensions import get_stock_dimensions

		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		is_add = self.update_type == "Add"

		# On submit: Add is positive, Reduce is negative
		# On cancel: flip the sign
		if cancel:
			is_incoming = not is_add  # cancel of Add = outgoing, cancel of Reduce = incoming
			sign = -1 if is_add else 1
		else:
			is_incoming = is_add
			sign = 1 if is_add else -1

		entries = []
		for row in self.stock_update_details:
			rate = row.rate or 0
			entry = {
				"item": row.item_variant,
				"warehouse": self.warehouse,
				"uom": row.uom,
				"voucher_type": 'YRP Stock Update',
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": sign * row.stock_qty,
				# Incoming stock needs "rate" for valuation (add to FIFO queue)
				# Outgoing stock needs "outgoing_rate" for FIFO consumption
				"rate": rate if is_incoming else 0,
				"outgoing_rate": rate if not is_incoming else 0,
			}
			if cancel:
				entry["is_cancelled"] = 1

			# Add dimension values
			for fn in dim_fields:
				entry[fn] = row.get(fn)

			entries.append(entry)

		return entries


@frappe.whitelist()
def get_stock_update_rates(
	item,
	attributes=None,
	primary_attribute=None,
	value_keys=None,
	warehouse=None,
	dimensions=None,
):
	"""Return read-only current rates for a grouped Stock Update editor row."""
	from yrp.stock.dimensions import get_dimension_fieldnames
	from yrp.stock.utils import get_last_sle_rate
	from yrp.yrp.doctype.yrp_item.yrp_item import get_variant

	frappe.has_permission('YRP Stock Update', "create", throw=True)
	if not isinstance(item, str) or not item or len(item) > 140:
		frappe.throw(_("Select an Item before fetching its valuation rate."))
	if not isinstance(warehouse, str) or not warehouse or len(warehouse) > 140:
		frappe.throw(_("Set Warehouse before fetching an item's valuation rate."))
	frappe.has_permission('YRP Item', "read", doc=item, throw=True)
	frappe.has_permission('YRP Warehouse', "read", doc=warehouse, throw=True)

	attributes = _parse_json_value(attributes, {})
	value_keys = _parse_json_value(value_keys, ["default"])
	dimensions = _parse_json_value(dimensions, {})
	if not isinstance(attributes, dict) or not isinstance(dimensions, dict):
		frappe.throw(_("Invalid Stock Update item details."))
	if not isinstance(value_keys, list):
		frappe.throw(_("Invalid Stock Update value list."))
	if (
		len(attributes) > MAX_RATE_LOOKUP_ATTRIBUTES
		or len(dimensions) > MAX_RATE_LOOKUP_ATTRIBUTES
	):
		frappe.throw(_("Too many Item Attributes were supplied."))
	if len(value_keys) > MAX_RATE_LOOKUP_VALUES:
		frappe.throw(
			_("A maximum of {0} valuation rates can be fetched at once.").format(
				MAX_RATE_LOOKUP_VALUES
			)
		)
	if any(
		not isinstance(value_key, str | int | float) or len(str(value_key)) > 140
		for value_key in value_keys
	):
		frappe.throw(_("Invalid Stock Update attribute value."))
	if primary_attribute and (
		not isinstance(primary_attribute, str) or len(primary_attribute) > 140
	):
		frappe.throw(_("Invalid Stock Update primary attribute."))
	if any(
		not isinstance(attribute, str)
		or len(attribute) > 140
		or (
			value not in (None, "")
			and (
				not isinstance(value, str | int | float)
				or len(str(value)) > 140
			)
		)
		for attribute, value in attributes.items()
	):
		frappe.throw(_("Invalid Stock Update Item Attributes."))
	if any(
		not isinstance(fieldname, str)
		or len(fieldname) > 140
		or (
			value not in (None, "")
			and (
				not isinstance(value, str | int | float)
				or len(str(value)) > 140
			)
		)
		for fieldname, value in dimensions.items()
	):
		frappe.throw(_("Invalid Stock Update dimensions."))

	dimension_fields = set(get_dimension_fieldnames())
	dimension_filters = {
		fieldname: dimensions.get(fieldname)
		for fieldname in dimension_fields
		if dimensions.get(fieldname) not in (None, "")
	}
	base_attributes = {
		str(attribute): value
		for attribute, value in attributes.items()
		if value not in (None, "")
	}
	rates = {}
	for value_key in value_keys:
		key = str(value_key)
		variant_attributes = dict(base_attributes)
		if primary_attribute and key != "default":
			variant_attributes[str(primary_attribute)] = value_key
		variant = get_variant(item, variant_attributes)
		if not variant:
			rates[key] = 0.0
			continue
		rate, _matched = get_last_sle_rate(
			variant,
			warehouse=warehouse,
			**dimension_filters,
		)
		rates[key] = flt(rate)
	return rates


def _parse_json_value(value, default):
	if value in (None, ""):
		return default
	if isinstance(value, dict | list):
		return value
	try:
		return frappe.parse_json(value)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid Stock Update item details."))


StockUpdate = YRPStockUpdate
