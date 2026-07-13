"""Group/ungroup helpers for the dimension-aware Vue stock editor.

Mirrors the production_api pattern where:
- `row_index` groups all primary-attribute variants of the SAME logical item
  (e.g. T-Shirt Red in S/M/L all share row_index=0)
- `table_index` encodes the group (attribute-structure) position

`group_items_for_ui`  : flat child rows  → nested grouped JSON (for onload)
`ungroup_items_from_ui`: nested grouped JSON → flat child rows (for before_validate)
"""

import json
from itertools import groupby

import frappe
from frappe import _

from yrp.stock.dimensions import get_dimension_fieldnames, get_stock_dimensions


# ---------------------------------------------------------------------------
# Per-doctype field map
#   child_table_field: child table field on parent
#   child_doctype: child table DocType
#   item_field: item variant fieldname on child row
#   qty_field: quantity fieldname on child row
#   value_fields: fields stored per primary-attribute value in the Vue editor
#   entry_fields: fields stored once per logical item row in the Vue editor
# ---------------------------------------------------------------------------
PARENT_CHILD_MAP = {
	"Stock Entry": {
		"child_table_field": "items",
		"child_doctype": "Stock Entry Detail",
		"item_field": "item",
		"qty_field": "qty",
		"value_fields": ["rate", "secondary_qty", "secondary_uom"],
		"entry_fields": ["allow_zero_valuation_rate", "make_qty_zero"],
	},
	"Stock Update": {
		"child_table_field": "stock_update_details",
		"child_doctype": "Stock Update Detail",
		"item_field": "item_variant",
		"qty_field": "update_diff_qty",
		"value_fields": ["rate", "secondary_qty", "secondary_uom"],
		"entry_fields": ["allow_zero_valuation_rate", "make_qty_zero"],
	},
	"Stock Reconciliation": {
		"child_table_field": "items",
		"child_doctype": "Stock Reconciliation Item",
		"item_field": "item",
		"qty_field": "qty",
		"value_fields": ["rate", "secondary_qty", "secondary_uom"],
		"entry_fields": ["allow_zero_valuation_rate", "make_qty_zero"],
	},
	"Work Order Deliverables": {
		"child_table_field": "deliverables",
		"child_doctype": "Work Order Deliverables",
		"item_field": "item_variant",
		"qty_field": "qty",
		"value_fields": ["pending_quantity", "stock_update", "valuation_rate"],
		"entry_fields": [
			"comments", "secondary_qty", "secondary_uom", "cancelled_quantity",
			"additional_parameters", "set_combination", "grn_detail_no", "item_type",
			"is_calculated", "source_grn", "source_grn_item",
			"source_inspection_entry_item",
		],
	},
	"Work Order Receivables": {
		"child_table_field": "receivables",
		"child_doctype": "Work Order Receivables",
		"item_field": "item_variant",
		"qty_field": "qty",
		"value_fields": ["cost", "pending_quantity", "total_cost"],
		"entry_fields": [
			"comments", "secondary_qty", "secondary_uom", "process_cost",
			"additional_parameters", "set_combination",
		],
	},
	"Delivery Challan": {
		"child_table_field": "items",
		"child_doctype": "Delivery Challan Item",
		"item_field": "item_variant",
		"qty_field": "qty",
		"value_fields": [
			"rate", "valuation_rate", "pending_quantity", "delivered_quantity",
			"received_quantity", "stock_qty", "amount", "ref_doctype", "ref_docname",
			"secondary_qty", "secondary_uom",
		],
		"entry_fields": [
			"stock_uom", "conversion_factor", "table_index", "row_index",
			"set_combination", "comments",
		],
	},
	"Goods Received Note": {
		"child_table_field": "items",
		"child_doctype": "Goods Received Note Item",
		"item_field": "item_variant",
		"qty_field": "quantity",
		"value_fields": [
			"rate", "pending_quantity", "max_receivable_quantity", "stock_qty", "amount",
			"ref_doctype", "ref_docname", "delivery_challan_item",
			"secondary_qty", "secondary_uom",
		],
		"entry_fields": [
			"stock_uom", "conversion_factor", "ref_doctype", "ref_docname",
			"delivery_challan_item", "table_index", "row_index", "set_combination",
			"comments",
		],
	},
	"Purchase Order": {
		"child_table_field": "items",
		"child_doctype": "Purchase Order Item",
		"item_field": "item_variant",
		"qty_field": "qty",
		"value_fields": [
			"rate", "pending_quantity", "received_quantity", "cancelled_quantity",
			"stock_qty", "amount", "discount_amount", "tax_amount", "total_amount",
			"secondary_qty", "secondary_uom",
		],
		"entry_fields": [
			"stock_uom", "conversion_factor", "delivery_date", "tax",
			"discount_percentage", "table_index", "row_index", "set_combination",
			"comments",
		],
	},
}


def _get_map_config(parent_doctype):
	if parent_doctype not in PARENT_CHILD_MAP:
		frappe.throw(f"Unsupported item editor parent doctype {parent_doctype}")
	return PARENT_CHILD_MAP[parent_doctype]


def _child_has_field(child_doctype, fieldname):
	if not child_doctype or not fieldname:
		return False
	meta = frappe.get_meta(child_doctype)
	return bool(meta.get_field(fieldname))


def _copy_supported_fields(source, fieldnames, child_doctype=None):
	out = {}
	meta = frappe.get_meta(child_doctype) if child_doctype else None
	for fn in fieldnames:
		df = meta.get_field(fn) if meta else None
		if child_doctype and not df:
			continue
		value = source.get(fn)
		if value is None:
			continue
		if df and df.fieldtype == "Link" and value in ("", 0, "0"):
			continue
		if isinstance(value, list):
			if not value:
				continue
			value = json.dumps(value)
		elif isinstance(value, dict) and df and df.fieldtype != "JSON":
			if not value:
				continue
			value = json.dumps(value)
		out[fn] = value
	return out


# ====================================================================
# Group: child rows → grouped JSON for the Vue editor
# ====================================================================

def group_items_for_ui(child_rows, parent_doctype):
	"""Convert flat child rows into the nested grouped structure the Vue editor expects.

	Example transformation:

	Flat (from child table — 3 rows):
	  row_index=0, item="T-Shirt-Red-S",  qty=10, lot="LOT-001"
	  row_index=0, item="T-Shirt-Red-M",  qty=20, lot="LOT-001"   (same row_index = same logical item)
	  row_index=1, item="Jeans-Blue-32",   qty=5,  lot="LOT-002"

	Grouped (for Vue editor — 2 groups):
	  [
	    {
	      primary_attribute: "Size",
	      items: [{
	        name: "T-Shirt",
	        dimensions: {lot: "LOT-001"},
	        attributes: {Colour: "Red"},
	        values: {S: {qty: 10}, M: {qty: 20}},
	      }],
	    },
	    {
	      items: [{
	        name: "Jeans",
	        dimensions: {lot: "LOT-002"},
	        attributes: {Colour: "Blue"},
	        values: {default: {qty: 5}},
	      }],
	    },
	  ]

	Key concept:
	  - row_index groups variants of the SAME logical item (all sizes of T-Shirt Red share row_index=0)
	  - table_index positions the attribute-group in the editor
	"""
	if not child_rows:
		return []

	config = _get_map_config(parent_doctype)
	item_field = config["item_field"]
	qty_field = config["qty_field"]
	child_doctype = config.get("child_doctype")
	value_fields = config.get("value_fields") or []
	entry_fields = config.get("entry_fields") or []

	# Normalise to dicts
	rows = []
	for idx, r in enumerate(child_rows):
		d = dict(r) if isinstance(r, dict) else r.as_dict()
		# A NULL row_index survives as_dict(), so setdefault alone never fires.
		# Indexless rows (created programmatically) must each stay their OWN
		# logical row — collapsing them into one "" bucket rendered only the
		# bucket's first variant and silently dropped the rest on re-save.
		if d.get("row_index") in (None, ""):
			d["row_index"] = f"__auto__{idx}"
		d["_original_order"] = idx
		rows.append(d)

	def _row_group_key(row):
		value = row.get("row_index")
		if value is None or value == "":
			return ""
		return str(value)

	rows.sort(key=lambda r: (_row_group_key(r), r.get("_original_order") or 0))
	dim_fields = get_dimension_fieldnames()

	item_details = []  # final output — list of groups

	# Cache per parent item
	_attr_cache = {}

	def _get_attr_details(parent_item):
		if parent_item not in _attr_cache:
			from yrp.yrp.doctype.item.item import get_attribute_details
			_attr_cache[parent_item] = get_attribute_details(parent_item)
		return _attr_cache[parent_item]

	def _variant_attrs(variant_doc, attr_names):
		out = {}
		for row in (variant_doc.attributes or []):
			if row.attribute in attr_names:
				out[row.attribute] = row.attribute_value
		return out

	# Group consecutive rows by row_index (same as production_api).
	# groupby returns (key, iterator). The iterator MUST be consumed immediately
	# (with list()) because it becomes invalid on the next iteration.
	for _row_idx, variants_iter in groupby(rows, _row_group_key):
		variants = list(variants_iter)  # consume iterator immediately
		first = variants[0]

		# Resolve variant → parent item
		parent_item = frappe.db.get_value("Item Variant", first[item_field], "item")
		if not parent_item:
			continue
		attr_details = _get_attr_details(parent_item)
		first_variant_doc = frappe.get_doc("Item Variant", first[item_field])

		# Non-primary attributes for this item entry
		all_attrs = list(attr_details.get("attributes") or [])
		if attr_details.get("primary_attribute"):
			all_attrs_with_primary = all_attrs + [attr_details["primary_attribute"]]
		else:
			all_attrs_with_primary = all_attrs

		first_variant_attrs = _variant_attrs(first_variant_doc, all_attrs_with_primary)

		# Collect dimension values from the first row
		dimensions = {
			fn: first.get(fn)
			for fn in dim_fields
			if _child_has_field(child_doctype, fn)
		}

		# Build the item entry
		item_entry = {
			"name": parent_item,
			"dimensions": dimensions,
			"attributes": {a: first_variant_attrs.get(a, "") for a in all_attrs},
			"primary_attribute": attr_details.get("primary_attribute") or "",
			"values": {},
			"default_uom": first.get("uom") or attr_details.get("default_uom") or "",
		}
		item_entry.update(_copy_supported_fields(first, entry_fields, child_doctype))

		# Populate values
		if attr_details.get("primary_attribute") and attr_details.get("primary_attribute_values"):
			primary = attr_details["primary_attribute"]
			# Init all primary values with 0
			for pv in attr_details["primary_attribute_values"]:
				item_entry["values"][pv] = {"qty": 0, **{fn: 0 for fn in value_fields}}
			# Fill actual values from variants (multiple rows share same row_index)
			for variant_row in variants:
				variant_doc = frappe.get_doc("Item Variant", variant_row[item_field])
				v_attrs = _variant_attrs(variant_doc, [primary])
				pv = v_attrs.get(primary, "")
				if pv and pv in item_entry["values"]:
					value_detail = {"qty": variant_row.get(qty_field) or 0}
					value_detail.update(_copy_supported_fields(variant_row, value_fields, child_doctype))
					item_entry["values"][pv] = value_detail
		else:
			value_detail = {"qty": first.get(qty_field) or 0}
			value_detail.update(_copy_supported_fields(first, value_fields, child_doctype))
			item_entry["values"]["default"] = value_detail

		# Find or create a matching attribute-group
		grp_index = _get_item_group_index(item_details, attr_details)
		if grp_index == -1:
			item_details.append({
				"attributes": attr_details.get("attributes") or [],
				"primary_attribute": attr_details.get("primary_attribute") or "",
				"dependent_attribute": attr_details.get("dependent_attribute") or "",
				"dependent_attribute_details": attr_details.get("dependent_attribute_details") or {},
				"primary_attribute_values": attr_details.get("primary_attribute_values") or [],
				"items": [item_entry],
			})
		else:
			item_details[grp_index]["items"].append(item_entry)

	return item_details


def _get_item_group_index(item_details, attr_details):
	"""Find a group in item_details that matches attr_details's attribute structure."""
	for i, grp in enumerate(item_details):
		if sorted(grp.get("attributes") or []) != sorted(attr_details.get("attributes") or []):
			continue
		if grp.get("primary_attribute", "") != (attr_details.get("primary_attribute") or ""):
			continue
		if sorted(grp.get("primary_attribute_values") or []) != sorted(attr_details.get("primary_attribute_values") or []):
			continue
		return i
	return -1


def group_correction_items_for_ui(child_rows, parent_doctype):
	"""Partition a flat correction-row list by `work_order_correction` and group
	each subset with group_items_for_ui. Returns per-correction blocks.

	Output shape (the `correction_item_details` JSON that flows server → editor → server):
	  [
	    { "work_order_correction": "WOC-2026-00001",
	      "title": "Correction WOC-2026-00001",
	      "item_details": [ /* exact group_items_for_ui(...) output for that correction */ ] },
	    ...
	  ]
	"""
	from collections import OrderedDict
	buckets = OrderedDict()
	for r in child_rows or []:
		d = dict(r) if isinstance(r, dict) else r.as_dict()
		key = d.get("work_order_correction")
		if not key:
			continue
		buckets.setdefault(key, []).append(d)
	out = []
	for name, rows in buckets.items():
		out.append({
			"work_order_correction": name,
			"title": _("Correction {0}").format(name),
			"item_details": group_items_for_ui(rows, parent_doctype),
		})
	return out


# ====================================================================
# Ungroup: grouped JSON → flat rows for self.set("items", ...)
# ====================================================================

def ungroup_items_from_ui(item_details, parent_doctype, keep_zero=False):
	"""Flatten the editor's grouped JSON into rows for the parent's child table.

	CRITICAL: row_index increments once per LOGICAL ITEM (not per variant row).
	All primary-attribute variants of the same item share the same row_index,
	matching production_api's convention. This is what allows group_items_for_ui
	to batch them back together on reload.

	`keep_zero` (default False — preserves SE/SU/GRN/PO behaviour): when True,
	rows whose cell qty is 0 are RETAINED in the output. Used by Delivery
	Challan during draft saves so a user-zeroed item stays in the table and
	can be re-edited; the DC's `before_validate(docstatus==1)` filters them
	out on the submit pass. Stock Reconciliation already keeps qty=0 rows
	through the existing parent_doctype exemption — `keep_zero` extends the
	same behaviour to other doctypes opting in.
	"""
	if isinstance(item_details, str):
		try:
			item_details = json.loads(item_details or "[]")
		except (ValueError, json.JSONDecodeError):
			frappe.throw(_("Invalid item details format — please refresh and try again"))
	if not item_details:
		return []

	config = _get_map_config(parent_doctype)
	item_field = config["item_field"]
	qty_field = config["qty_field"]
	child_doctype = config.get("child_doctype")
	value_fields = config.get("value_fields") or []
	entry_fields = config.get("entry_fields") or []

	dim_fields = get_dimension_fieldnames()

	out = []
	row_index = 0
	for table_index, group in enumerate(item_details):
		for entry in group["items"]:
			parent_item = entry["name"]
			base_attrs = dict(entry.get("attributes") or {})
			dimensions = entry.get("dimensions") or {}
			default_uom = entry.get("default_uom")

			extra_fields = _copy_supported_fields(entry, entry_fields, child_doctype)

			values_dict = entry.get("values") or {}
			has_primary = (
				entry.get("primary_attribute")
				and "default" not in values_dict
				and len(values_dict) > 0
			)

			if has_primary:
				primary = entry["primary_attribute"]
				for pv, vals in values_dict.items():
					qty = (vals or {}).get("qty") or 0
					if not qty and not keep_zero:
						continue
					attrs = dict(base_attrs)
					attrs[primary] = pv
					variant_name = _resolve_or_create_variant(parent_item, attrs)
					row = {
						item_field: variant_name,
						qty_field: qty,
						"uom": default_uom,
						"table_index": table_index,
						"row_index": row_index,  # SAME for all variants of this item
						**extra_fields,
					}
					row.update(_copy_supported_fields(vals or {}, value_fields, child_doctype))
					for fn in dim_fields:
						if dimensions.get(fn) and _child_has_field(child_doctype, fn):
							row[fn] = dimensions[fn]
					out.append(row)
			else:
				vals = (entry.get("values") or {}).get("default") or {}
				qty = vals.get("qty") or 0
				# Skip zero-qty items — except for Stock Reconciliation where
				# qty=0 is valid (make_qty_zero or manual zero entry), or when
				# the caller opts into keep_zero (DC draft saves).
				if not qty and parent_doctype != "Stock Reconciliation" and not keep_zero:
					row_index += 1
					continue
				variant_name = _resolve_or_create_variant(parent_item, base_attrs)
				row = {
					item_field: variant_name,
					qty_field: qty,
					"uom": default_uom,
					"table_index": table_index,
					"row_index": row_index,
					**extra_fields,
				}
				row.update(_copy_supported_fields(vals, value_fields, child_doctype))
				for fn in dim_fields:
					if dimensions.get(fn) and _child_has_field(child_doctype, fn):
						row[fn] = dimensions[fn]
				out.append(row)

			# Increment AFTER all variants of this logical item
			row_index += 1

	return out


def ungroup_correction_items_from_ui(correction_details, parent_doctype, keep_zero=False):
	"""Inverse of group_correction_items_for_ui: flatten per-correction blocks to
	flat child rows, stamping `work_order_correction` on every emitted row."""
	if isinstance(correction_details, str):
		try:
			correction_details = json.loads(correction_details or "[]")
		except (ValueError, json.JSONDecodeError):
			frappe.throw(_("Invalid correction item details format — please refresh and try again"))
	out = []
	for block in correction_details or []:
		name = block.get("work_order_correction")
		rows = ungroup_items_from_ui(block.get("item_details") or [], parent_doctype, keep_zero=keep_zero)
		for row in rows:
			row["work_order_correction"] = name
		out.extend(rows)
	return out


def _resolve_or_create_variant(parent_item, attributes):
	"""Find an Item Variant matching parent_item + attributes; create if missing.

	Handles concurrent requests safely: if two threads try to create the
	same variant simultaneously, the second insert catches the duplicate error
	and falls back to re-querying.
	"""
	from yrp.yrp.doctype.item.item import get_variant, create_variant

	# Strip empty-value keys — dependent stages may leave inapplicable attributes blank
	attrs = {k: v for k, v in attributes.items() if v}

	name = get_variant(parent_item, attrs)
	if name:
		return name

	try:
		new_doc = create_variant(parent_item, attrs)
		new_doc.insert(ignore_permissions=True)
		return new_doc.name
	except frappe.DuplicateEntryError:
		# Another thread created it between our check and insert
		return get_variant(parent_item, attrs)
