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
#   (child_table_field_on_parent, item_link_fieldname_on_child, qty_fieldname)
# ---------------------------------------------------------------------------
PARENT_CHILD_MAP = {
	"Stock Entry": ("items", "item", "qty"),
	"Stock Update": ("stock_update_details", "item_variant", "update_diff_qty"),
	"Stock Reconciliation": ("items", "item", "qty"),
}


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

	if parent_doctype not in PARENT_CHILD_MAP:
		frappe.throw(f"group_items_for_ui: unsupported parent doctype {parent_doctype}")
	_, item_field, qty_field = PARENT_CHILD_MAP[parent_doctype]

	# Normalise to dicts
	rows = []
	for idx, r in enumerate(child_rows):
		d = r if isinstance(r, dict) else r.as_dict()
		d.setdefault("row_index", idx)
		rows.append(d)

	rows.sort(key=lambda r: r.get("row_index") or 0)
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
	for _row_idx, variants_iter in groupby(rows, lambda r: r.get("row_index") or 0):
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
		dimensions = {fn: first.get(fn) for fn in dim_fields}

		# Build the item entry
		item_entry = {
			"name": parent_item,
			"dimensions": dimensions,
			"attributes": {a: first_variant_attrs.get(a, "") for a in all_attrs},
			"primary_attribute": attr_details.get("primary_attribute") or "",
			"values": {},
			"default_uom": first.get("uom") or attr_details.get("default_uom") or "",
		}
		# Carry per-item flags (otherInputs) from child rows
		for key in ("allow_zero_valuation_rate", "make_qty_zero"):
			if first.get(key):
				item_entry[key] = first.get(key)

		# Populate values
		if attr_details.get("primary_attribute") and attr_details.get("primary_attribute_values"):
			primary = attr_details["primary_attribute"]
			# Init all primary values with 0
			for pv in attr_details["primary_attribute_values"]:
				item_entry["values"][pv] = {"qty": 0, "rate": 0}
			# Fill actual values from variants (multiple rows share same row_index)
			for variant_row in variants:
				variant_doc = frappe.get_doc("Item Variant", variant_row[item_field])
				v_attrs = _variant_attrs(variant_doc, [primary])
				pv = v_attrs.get(primary, "")
				if pv and pv in item_entry["values"]:
					item_entry["values"][pv] = {
						"qty": variant_row.get(qty_field) or 0,
						"rate": variant_row.get("rate") or 0,
					}
		else:
			item_entry["values"]["default"] = {
				"qty": first.get(qty_field) or 0,
				"rate": first.get("rate") or 0,
			}

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


# ====================================================================
# Ungroup: grouped JSON → flat rows for self.set("items", ...)
# ====================================================================

def ungroup_items_from_ui(item_details, parent_doctype):
	"""Flatten the editor's grouped JSON into rows for the parent's child table.

	CRITICAL: row_index increments once per LOGICAL ITEM (not per variant row).
	All primary-attribute variants of the same item share the same row_index,
	matching production_api's convention. This is what allows group_items_for_ui
	to batch them back together on reload.
	"""
	if isinstance(item_details, str):
		try:
			item_details = json.loads(item_details or "[]")
		except (ValueError, json.JSONDecodeError):
			frappe.throw(_("Invalid item details format — please refresh and try again"))
	if not item_details:
		return []

	if parent_doctype not in PARENT_CHILD_MAP:
		frappe.throw(f"ungroup_items_from_ui: unsupported parent doctype {parent_doctype}")
	_, item_field, qty_field = PARENT_CHILD_MAP[parent_doctype]

	dim_fields = get_dimension_fieldnames()

	out = []
	row_index = 0
	for table_index, group in enumerate(item_details):
		for entry in group["items"]:
			parent_item = entry["name"]
			base_attrs = dict(entry.get("attributes") or {})
			dimensions = entry.get("dimensions") or {}
			default_uom = entry.get("default_uom")

			# Per-item flags from otherInputs (e.g. allow_zero_valuation_rate, make_qty_zero)
			extra_fields = {}
			for key in ("allow_zero_valuation_rate", "make_qty_zero"):
				if entry.get(key) is not None:
					extra_fields[key] = entry[key]

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
					if not qty:
						continue
					attrs = dict(base_attrs)
					attrs[primary] = pv
					variant_name = _resolve_or_create_variant(parent_item, attrs)
					row = {
						item_field: variant_name,
						qty_field: qty,
						"rate": (vals or {}).get("rate") or 0,
						"uom": default_uom,
						"table_index": table_index,
						"row_index": row_index,  # SAME for all variants of this item
						**extra_fields,
					}
					for fn in dim_fields:
						if dimensions.get(fn):
							row[fn] = dimensions[fn]
					out.append(row)
			else:
				vals = (entry.get("values") or {}).get("default") or {}
				qty = vals.get("qty") or 0
				# Skip zero-qty items — except for Stock Reconciliation where
				# qty=0 is valid (make_qty_zero or manual zero entry)
				if not qty and parent_doctype != "Stock Reconciliation":
					row_index += 1
					continue
				variant_name = _resolve_or_create_variant(parent_item, base_attrs)
				row = {
					item_field: variant_name,
					qty_field: qty,
					"rate": vals.get("rate") or 0,
					"uom": default_uom,
					"table_index": table_index,
					"row_index": row_index,
					**extra_fields,
				}
				for fn in dim_fields:
					if dimensions.get(fn):
						row[fn] = dimensions[fn]
				out.append(row)

			# Increment AFTER all variants of this logical item
			row_index += 1

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
