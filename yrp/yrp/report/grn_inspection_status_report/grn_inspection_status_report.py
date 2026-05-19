import re

import frappe
from frappe import _
from frappe.utils import date_diff, flt, today

from yrp.stock.dimensions import assert_safe_fieldname, get_stock_dimensions


MAX_GRN_ROWS = 10000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dims = get_stock_dimensions()
	received_types = _get_received_types()
	target_field_map = _target_field_map(received_types)

	rows = _get_grn_rows(filters, dims)
	inspection_map, seen_target_types = _get_inspection_map([row.grn_item for row in rows])
	for received_type in seen_target_types:
		if received_type not in target_field_map:
			target_field_map[received_type] = _make_unique_target_fieldname(received_type, target_field_map)

	data = _build_data(rows, inspection_map, target_field_map, filters)
	columns = _get_columns(dims, target_field_map)
	return columns, data


def _get_columns(dims, target_field_map):
	columns = [
		{"label": _("GRN"), "fieldname": "grn", "fieldtype": "Link", "options": "Goods Received Note", "width": 150},
		{"label": _("GRN Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Against"), "fieldname": "against", "fieldtype": "Data", "width": 110},
		{"label": _("Against ID"), "fieldname": "against_id", "fieldtype": "Dynamic Link", "options": "against", "width": 150},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 140},
		{"label": _("Process"), "fieldname": "process_name", "fieldtype": "Link", "options": "Process", "width": 140},
		{"label": _("Item Variant"), "fieldname": "item_variant", "fieldtype": "Link", "options": "Item Variant", "width": 160},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Source Received Type"), "fieldname": "source_received_type", "fieldtype": "Link", "options": "Received Type", "width": 140},
	]
	for dim in dims:
		if dim["fieldname"] == "received_type":
			continue
		columns.append({
			"label": _(dim["label"]),
			"fieldname": dim["fieldname"],
			"fieldtype": "Link",
			"options": dim["dimension_doctype"],
			"width": 120,
		})

	columns.extend([
		{"label": _("GRN Qty"), "fieldname": "grn_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Inspected Qty"), "fieldname": "inspected_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Pending Inspection Qty"), "fieldname": "pending_inspection_qty", "fieldtype": "Float", "width": 150},
	])
	for received_type, fieldname in target_field_map.items():
		columns.append({
			"label": _("{0} Qty").format(received_type),
			"fieldname": fieldname,
			"fieldtype": "Float",
			"width": 110,
		})

	columns.extend([
		{"label": _("Inspection Status"), "fieldname": "inspection_status", "fieldtype": "Data", "width": 130},
		{"label": _("Conversion Status"), "fieldname": "conversion_status", "fieldtype": "Data", "width": 140},
		{"label": _("Inspection Entries"), "fieldname": "inspection_entries", "fieldtype": "Data", "width": 220},
		{"label": _("Last Inspection Entry"), "fieldname": "last_inspection_entry", "fieldtype": "Link", "options": "Inspection Entry", "width": 160},
		{"label": _("Inspector"), "fieldname": "inspector", "fieldtype": "Data", "width": 160},
		{"label": _("Last Inspection Date"), "fieldname": "last_inspection_date", "fieldtype": "Date", "width": 130},
		{"label": _("Age Days"), "fieldname": "age_days", "fieldtype": "Int", "width": 90},
		{"label": _("GRN Item Row"), "fieldname": "grn_item", "fieldtype": "Data", "width": 120},
	])
	return columns


def _get_grn_rows(filters, dims):
	grn_meta = frappe.get_meta("Goods Received Note")
	item_meta = frappe.get_meta("Goods Received Note Item")

	select_cols = [
		"grn.name AS grn",
		"grn.posting_date",
		"grn.against",
		"grn.against_id",
		"grn.supplier",
		"grn.process_name",
		"gri.name AS grn_item",
		"gri.item_variant",
		"grn.to_warehouse AS warehouse",
		"COALESCE(gri.quantity, 0) AS grn_qty",
	]
	for dim in dims:
		fn = dim["fieldname"]
		assert_safe_fieldname(fn)
		expr = _dimension_expr(fn, grn_meta, item_meta)
		if expr:
			select_cols.append(f"{expr} AS `{fn}`")
		else:
			select_cols.append(f"NULL AS `{fn}`")

	conds = ["grn.docstatus = 1"]
	values = {}
	if filters.get("from_date"):
		conds.append("grn.posting_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conds.append("grn.posting_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if filters.get("against"):
		conds.append("grn.against = %(against)s")
		values["against"] = filters["against"]
	if filters.get("against_id"):
		conds.append("grn.against_id = %(against_id)s")
		values["against_id"] = filters["against_id"]
	if filters.get("supplier"):
		conds.append("grn.supplier = %(supplier)s")
		values["supplier"] = filters["supplier"]
	if filters.get("process_name"):
		conds.append("grn.process_name = %(process_name)s")
		values["process_name"] = filters["process_name"]
	if filters.get("item_variant"):
		conds.append("gri.item_variant = %(item_variant)s")
		values["item_variant"] = filters["item_variant"]
	if filters.get("warehouse"):
		conds.append("grn.to_warehouse = %(warehouse)s")
		values["warehouse"] = filters["warehouse"]
	if filters.get("source_received_type") and item_meta.get_field("received_type"):
		conds.append("gri.received_type = %(source_received_type)s")
		values["source_received_type"] = filters["source_received_type"]

	rows = frappe.db.sql(
		f"""
		SELECT {", ".join(select_cols)}
		FROM `tabGoods Received Note Item` gri
		INNER JOIN `tabGoods Received Note` grn ON grn.name = gri.parent
		WHERE {" AND ".join(conds)}
		ORDER BY grn.posting_date DESC, grn.name DESC, gri.idx ASC
		LIMIT {MAX_GRN_ROWS + 1}
		""",
		values,
		as_dict=True,
	)
	if len(rows) > MAX_GRN_ROWS:
		frappe.throw(_("Too many GRN rows. Please narrow the report filters."))
	return rows


def _dimension_expr(fn, grn_meta, item_meta):
	if item_meta.get_field(fn):
		return f"gri.`{fn}`"
	if grn_meta.get_field(fn):
		return f"grn.`{fn}`"
	return None


def _get_inspection_map(grn_items):
	if not grn_items:
		return {}, set()

	rows = frappe.db.sql(
		"""
		SELECT iei.ref_docname AS grn_item,
		       iei.target_received_type,
		       COALESCE(iei.qty, 0) AS qty,
		       ie.name AS inspection_entry,
		       ie.inspector,
		       ie.posting_date,
		       ie.status,
		       COALESCE(ie.is_converted, 0) AS is_converted
		FROM `tabInspection Entry Item` iei
		INNER JOIN `tabInspection Entry` ie ON ie.name = iei.parent
		WHERE ie.docstatus = 1
		  AND ie.against = 'Goods Received Note'
		  AND iei.ref_doctype = 'Goods Received Note Item'
		  AND iei.ref_docname IN %(grn_items)s
		ORDER BY ie.posting_date ASC, ie.name ASC, iei.idx ASC
		""",
		{"grn_items": tuple(grn_items)},
		as_dict=True,
	)

	inspection_map = {}
	seen_target_types = set()
	for row in rows:
		grn_item = row.grn_item
		target = row.target_received_type or _("Unspecified")
		seen_target_types.add(target)
		info = inspection_map.setdefault(
			grn_item,
			{
				"target_qty": {},
				"inspected_qty": 0.0,
				"converted_qty": 0.0,
				"inspection_entries": [],
				"inspectors": [],
				"last_inspection_entry": None,
				"last_inspection_date": None,
			},
		)
		qty = flt(row.qty)
		info["target_qty"][target] = flt(info["target_qty"].get(target)) + qty
		info["inspected_qty"] += qty
		if row.is_converted or row.status == "Converted":
			info["converted_qty"] += qty
		if row.inspection_entry not in info["inspection_entries"]:
			info["inspection_entries"].append(row.inspection_entry)
		if row.inspector and row.inspector not in info["inspectors"]:
			info["inspectors"].append(row.inspector)
		info["last_inspection_entry"] = row.inspection_entry
		info["last_inspection_date"] = row.posting_date

	return inspection_map, seen_target_types


def _build_data(rows, inspection_map, target_field_map, filters):
	data = []
	as_of_date = filters.get("to_date") or today()
	target_filter = filters.get("target_received_type")

	for row in rows:
		info = inspection_map.get(row.grn_item, {})
		target_qty = info.get("target_qty") or {}
		if target_filter and flt(target_qty.get(target_filter)) <= 0:
			continue

		grn_qty = flt(row.grn_qty)
		raw_inspected_qty = flt(info.get("inspected_qty"))
		inspected_qty = min(raw_inspected_qty, grn_qty) if grn_qty else raw_inspected_qty
		pending_qty = max(grn_qty - inspected_qty, 0)
		conversion_status = _conversion_status(raw_inspected_qty, flt(info.get("converted_qty")))
		inspection_status = _inspection_status(inspected_qty, pending_qty)

		if filters.get("inspection_status") and inspection_status != filters["inspection_status"]:
			continue
		if filters.get("conversion_status") and conversion_status != filters["conversion_status"]:
			continue

		out = frappe._dict(row)
		out.source_received_type = row.get("received_type")
		out.inspected_qty = inspected_qty
		out.pending_inspection_qty = pending_qty
		out.inspection_status = inspection_status
		out.conversion_status = conversion_status
		out.inspection_entries = ", ".join(info.get("inspection_entries") or [])
		out.last_inspection_entry = info.get("last_inspection_entry")
		out.inspector = ", ".join(info.get("inspectors") or [])
		out.last_inspection_date = info.get("last_inspection_date")
		out.age_days = date_diff(as_of_date, row.posting_date) if row.posting_date else 0

		for received_type, fieldname in target_field_map.items():
			out[fieldname] = flt(target_qty.get(received_type))
		data.append(out)

	return data


def _inspection_status(inspected_qty, pending_qty):
	if inspected_qty <= 0:
		return "Not Inspected"
	if pending_qty > 0:
		return "Partially Inspected"
	return "Inspected"


def _conversion_status(inspected_qty, converted_qty):
	if inspected_qty <= 0:
		return "No Inspection"
	if converted_qty <= 0:
		return "Not Converted"
	if converted_qty < inspected_qty:
		return "Partially Converted"
	return "Converted"


def _get_received_types():
	if not frappe.db.exists("DocType", "Received Type"):
		return []
	return frappe.get_all(
		"Received Type",
		pluck="name",
		order_by="is_default desc, name asc",
	)


def _target_field_map(received_types):
	field_map = {}
	for received_type in received_types:
		field_map[received_type] = _make_unique_target_fieldname(received_type, field_map)
	return field_map


def _make_unique_target_fieldname(received_type, existing_map):
	base = "qty_" + re.sub(r"[^a-z0-9_]+", "_", (received_type or "unspecified").lower()).strip("_")
	if base == "qty_":
		base = "qty_unspecified"
	used = set(existing_map.values())
	fieldname = base
	idx = 2
	while fieldname in used:
		fieldname = f"{base}_{idx}"
		idx += 1
	return fieldname
