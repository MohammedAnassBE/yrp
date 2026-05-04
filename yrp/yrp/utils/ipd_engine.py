"""Industry-agnostic IPD calculation engine.

Reads `IPD Process Matrix` (main I/O groups) and `Item Production Detail.item_bom`
(auxiliary consumables). No knowledge of garments, pharma, or any specific industry.
"""

import frappe


def get_process_io(ipd_name, process_name, output_demand):
	"""Compute input requirements and outputs produced for a process across all its matrices.

	A single process can have multiple `IPD Process Matrix` records, each owning a
	disjoint partition of outputs (e.g. Cutting matrix A: main fabric -> Front/Back/Sleeve;
	Cutting matrix B: rib fabric -> Neck Rib).

	Args:
	    ipd_name: name of `Item Production Detail`.
	    process_name: Process master name.
	    output_demand: list of {"attrs": {attr_name: attr_value}, "qty": float, "item": optional}.

	Returns dict {"inputs": [...], "outputs": [...]} where each list item is
	    {"item": str, "attrs": {...}, "qty": float, "uom": str}.
	"""
	matrix_names = frappe.get_all(
		"IPD Process Matrix",
		filters={"ipd": ipd_name, "process_name": process_name, "docstatus": ["<", 2]},
		pluck="name",
	)
	if not matrix_names:
		frappe.throw(f"No IPD Process Matrix found for IPD {ipd_name} / process {process_name}.")

	ipd_doc = frappe.get_doc("Item Production Detail", ipd_name)
	parent_item = ipd_doc.item
	dep_attr = ipd_doc.dependent_attribute
	# Look up in_stage / out_stage for this process from IPD Process row
	in_stage = out_stage = None
	for r in ipd_doc.ipd_processes:
		if r.process_name == process_name:
			in_stage = r.in_stage
			out_stage = r.out_stage
			break

	matrices = []
	for mname in matrix_names:
		mdoc = frappe.get_doc("IPD Process Matrix", mname)
		matrices.append({
			"name": mname,
			"input_item": mdoc.input_item or parent_item,
			"output_item": mdoc.output_item or parent_item,
			"groups": mdoc.get_combinations_grouped(),
		})

	inputs_required = []
	outputs_produced = []

	for demand in output_demand:
		# Strip dep attr from demand (matrices don't carry it; engine matches on the rest)
		demand_attrs_lookup = {k: v for k, v in (demand.get("attrs") or {}).items() if not dep_attr or k != dep_attr}
		match = _find_group_across_matrices(matrices, {"attrs": demand_attrs_lookup})
		if match is None:
			frappe.throw(
				f"No matrix group matches output demand {demand_attrs_lookup} "
				f"for IPD {ipd_name} / process {process_name}."
			)
		matrix, group, matching_combo = match
		scale = demand["qty"] / matching_combo["qty"]

		for inp in group["input"]:
			wastage = inp["wastage_pct"] or 0
			attrs = dict(inp["attrs"])
			if dep_attr and in_stage is not None:
				attrs[dep_attr] = in_stage
			inputs_required.append({
				"item": matrix["input_item"],
				"attrs": attrs,
				"qty": inp["qty"] * scale * (1 + wastage / 100.0),
				"uom": inp["uom"],
			})

		for out in group["output"]:
			attrs = dict(out["attrs"])
			if dep_attr and out_stage is not None:
				attrs[dep_attr] = out_stage
			outputs_produced.append({
				"item": matrix["output_item"],
				"attrs": attrs,
				"qty": out["qty"] * scale,
				"uom": out["uom"],
			})

	return {"inputs": inputs_required, "outputs": outputs_produced}


def _find_group_across_matrices(matrices, demand):
	demand_attrs = demand["attrs"]
	for matrix in matrices:
		for _gidx, g in matrix["groups"].items():
			for combo in g["output"]:
				if _attrs_match(combo["attrs"], demand_attrs):
					return matrix, g, combo
	return None


def _attrs_match(combo_attrs, demand_attrs):
	"""Combo attrs must match demand attrs on every key the combo declares."""
	for k, v in combo_attrs.items():
		if demand_attrs.get(k) != v:
			return False
	return True


def get_consumables(ipd_name, total_output_qty, variants=None, process_name=None):
	"""Compute consumables (auxiliary BOM) requirements for an IPD.

	Args:
	    ipd_name: name of `Item Production Detail`.
	    total_output_qty: total finished-product quantity (used for Mode A flat ratios).
	    variants: optional list of {"attrs": {...}, "qty": float} for Mode B per-variant lookup.
	    process_name: optional filter — return only consumables tagged for this process.

	Returns list of {"item": str, "qty": float, "uom": str, "process": str, "attrs": {...}}.
	"""
	ipd = frappe.get_doc("Item Production Detail", ipd_name)
	out = []

	for row in ipd.item_bom:
		if process_name and row.process_name and row.process_name != process_name:
			continue

		wastage_factor = 1 + (row.wastage_pct or 0) / 100.0

		if row.based_on_attribute_mapping and row.attribute_mapping:
			if not variants:
				continue
			out.extend(_resolve_mode_b(row, variants, wastage_factor))
		else:
			ratio = (row.qty_of_bom_item or 0) / (row.qty_of_product or 1)
			qty = total_output_qty * ratio * wastage_factor
			out.append({
				"item": row.item,
				"qty": qty,
				"uom": row.uom,
				"process": row.process_name,
				"attrs": {},
			})

	return out


def _resolve_mode_b(bom_row, variants, wastage_factor):
	"""Resolve Mode B per-variant qty by looking up Item BOM Attribute Mapping."""
	mapping = frappe.get_doc("Item BOM Attribute Mapping", bom_row.attribute_mapping)
	results = []
	for variant in variants:
		variant_attrs = variant["attrs"]
		variant_qty = variant["qty"]
		bom_combo = _lookup_mode_b(mapping, variant_attrs)
		if bom_combo is None:
			continue
		input_qty = (bom_row.qty_of_bom_item or 0) / (bom_row.qty_of_product or 1)
		qty = variant_qty * input_qty * bom_combo.get("quantity", 1) * wastage_factor
		results.append({
			"item": mapping.bom_item,
			"qty": qty,
			"uom": bom_row.uom,
			"process": bom_row.process_name,
			"attrs": bom_combo.get("bom_attrs", {}),
		})
	return results


def _lookup_mode_b(mapping, variant_attrs):
	"""Find the row in mapping.values whose item-side attribute values match variant_attrs.
	Return {"quantity": float, "bom_attrs": {...}} for the matched bom-side row."""
	values = list(mapping.values)
	by_index = {}
	for v in values:
		by_index.setdefault(v.index, []).append(v)

	for idx, rows in by_index.items():
		item_side = {r.attribute: r.attribute_value for r in rows if r.type == "item"}
		bom_side_rows = [r for r in rows if r.type == "bom"]
		if all(variant_attrs.get(k) == v for k, v in item_side.items()):
			bom_attrs = {r.attribute: r.attribute_value for r in bom_side_rows}
			qty = next((r.quantity for r in bom_side_rows if r.quantity), 1)
			return {"quantity": qty, "bom_attrs": bom_attrs}
	return None
