"""Industry-agnostic IPD calculation engine.

Reads `IPD Process Matrix` (main I/O groups) and `Item Production Detail.item_bom`
(auxiliary consumables). No knowledge of garments, pharma, or any specific industry.
"""

import frappe

from yrp.yrp.doctype.item.item import get_or_create_variant


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
			"reference_item_variant": mdoc.reference_item_variant,
			"input_item": mdoc.input_item or parent_item,
			"output_item": mdoc.output_item or parent_item,
			"groups": mdoc.get_combinations_grouped(),
		})

	inputs_required = []
	outputs_produced = []

	for demand in output_demand:
		# Strip dep attr from demand (matrices don't carry it; engine matches on the rest)
		demand_attrs_lookup = {k: v for k, v in (demand.get("attrs") or {}).items() if not dep_attr or k != dep_attr}
		match = _find_group_across_matrices(
			matrices,
			{
				"attrs": demand_attrs_lookup,
				"reference_item_variant": demand.get("reference_item_variant") or demand.get("item_variant"),
			},
		)
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
				"reference_item_variant": demand.get("reference_item_variant") or demand.get("item_variant"),
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
				"reference_item_variant": demand.get("reference_item_variant") or demand.get("item_variant"),
			})

	return {"inputs": inputs_required, "outputs": outputs_produced}


def _find_group_across_matrices(matrices, demand):
	demand_attrs = demand["attrs"]
	demand_reference = demand.get("reference_item_variant")
	exact_matrices = [
		matrix for matrix in matrices
		if demand_reference and matrix.get("reference_item_variant") == demand_reference
	]
	generic_matrices = [
		matrix for matrix in matrices
		if not matrix.get("reference_item_variant")
	]
	search_matrices = exact_matrices if exact_matrices else generic_matrices
	for matrix in search_matrices:
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


def calculate_major_deliverables(ipd_name, variant_demands, process_names=None, include_outputs=False):
	"""Scale IPD Process Matrix rows for Lot-style BOM calculation.

	Args:
	    ipd_name: `Item Production Detail` name.
	    variant_demands: list of {"item_variant": str, "qty": float}.
	    process_names: optional process name or list of process names.
	    include_outputs: when true, include produced outputs as well as required inputs.

	Returns aggregated rows with `process_name`, `item_variant`, `required_qty`, and `uom`.
	"""
	ipd = frappe.get_doc("Item Production Detail", ipd_name)
	demands = _normalize_variant_demands(ipd, variant_demands)
	process_filter = _normalize_process_filter(process_names)
	stage_by_process = _get_process_stage_map(ipd)
	matrices_by_process = _get_process_matrices(ipd_name, process_filter)
	aggregated = {}

	if not matrices_by_process:
		frappe.throw(f"No IPD Process Matrix found for IPD {ipd_name}.")

	for process_name, matrices in matrices_by_process.items():
		exact_matrices = [matrix for matrix in matrices if matrix.reference_item_variant]
		generic_matrices = [matrix for matrix in matrices if not matrix.reference_item_variant]
		has_exact_match = False

		for demand in demands:
			reference_variant = demand["item_variant"]
			for matrix in exact_matrices:
				if matrix.reference_item_variant != reference_variant:
					continue
				has_exact_match = True
				_calculate_reference_matrix(
					aggregated,
					ipd,
					stage_by_process,
					matrix,
					demand,
					include_outputs=include_outputs,
				)

		if has_exact_match:
			continue

		if generic_matrices:
			_calculate_generic_matrices(
				aggregated,
				ipd,
				stage_by_process,
				generic_matrices,
				demands,
				include_outputs=include_outputs,
			)
			continue

		frappe.throw(
			f"No exact or generic IPD Process Matrix found for IPD {ipd_name} / process {process_name}."
		)

	return list(aggregated.values())


def _calculate_reference_matrix(aggregated, ipd, stage_by_process, matrix, demand, include_outputs=False):
	reference_variant = demand["item_variant"]
	stages = stage_by_process.get(matrix.process_name, {})
	scale_side = _get_scale_side(ipd, stages)
	for group_index, group in matrix.get_combinations_grouped().items():
		scale = _get_group_scale(ipd, demand, group, scale_side)
		_add_matrix_group_rows(
			aggregated,
			ipd,
			matrix,
			reference_variant,
			group_index,
			group,
			scale,
			side="Input",
			stage=stages.get("in_stage"),
		)
		if include_outputs:
			_add_matrix_group_rows(
				aggregated,
				ipd,
				matrix,
				reference_variant,
				group_index,
				group,
				scale,
				side="Output",
				stage=stages.get("out_stage"),
			)


def _calculate_generic_matrices(aggregated, ipd, stage_by_process, matrices, demands, include_outputs=False):
	available = _build_available_pool(demands)
	for matrix in matrices:
		stages = stage_by_process.get(matrix.process_name, {})
		for group_index, group in matrix.get_combinations_grouped().items():
			scale, consumed_refs = _get_pool_group_scale(ipd, matrix, group, stages, available)
			if scale <= 0:
				continue
			_add_matrix_group_rows(
				aggregated,
				ipd,
				matrix,
				reference_variant=None,
				group_index=group_index,
				group=group,
				scale=scale,
				side="Input",
				stage=stages.get("in_stage"),
				reference_variants=consumed_refs,
			)
			if include_outputs:
				_add_matrix_group_rows(
					aggregated,
					ipd,
					matrix,
					reference_variant=None,
					group_index=group_index,
					group=group,
					scale=scale,
					side="Output",
					stage=stages.get("out_stage"),
					reference_variants=consumed_refs,
				)
			_consume_pool_group(ipd, matrix, group, stages, available, scale)


def _build_available_pool(demands):
	available = {}
	for demand in demands:
		variant = demand["item_variant"]
		available.setdefault(variant, {
			"qty": 0.0,
			"attrs": demand.get("attrs") or {},
		})
		available[variant]["qty"] += float(demand.get("qty") or 0)
	return available


def _get_pool_group_scale(ipd, matrix, group, stages, available):
	input_rows = group.get("input") or []
	if not input_rows:
		return 0, []

	scales = []
	consumed_refs = []
	for combo in input_rows:
		variant = _matrix_combo_variant(ipd, matrix, combo, side="Input", stage=stages.get("in_stage"))
		available_qty = float((available.get(variant) or {}).get("qty") or 0)
		required_qty = _scaled_combo_qty(combo, scale=1, side="Input")
		if required_qty <= 0 or available_qty <= 0:
			return 0, []
		scales.append(available_qty / required_qty)
		consumed_refs.append(variant)

	if not scales:
		return 0, []
	return min(scales), consumed_refs


def _consume_pool_group(ipd, matrix, group, stages, available, scale):
	for combo in group.get("input") or []:
		variant = _matrix_combo_variant(ipd, matrix, combo, side="Input", stage=stages.get("in_stage"))
		if variant not in available:
			continue
		available[variant]["qty"] -= _scaled_combo_qty(combo, scale=scale, side="Input")
		if available[variant]["qty"] < 0:
			available[variant]["qty"] = 0


def _matrix_combo_variant(ipd, matrix, combo, side, stage):
	matrix_item = (matrix.input_item if side == "Input" else matrix.output_item) or ipd.item
	attrs = dict(combo.get("attrs") or {})
	if matrix_item == ipd.item and ipd.dependent_attribute and stage is not None:
		attrs[ipd.dependent_attribute] = stage
	return get_or_create_variant(
		matrix_item,
		attrs,
		dependent_attr=ipd.dependent_attribute_mapping,
	)


def _scaled_combo_qty(combo, scale, side):
	wastage_factor = 1 + ((combo.get("wastage_pct") or 0) / 100.0) if side == "Input" else 1
	return float(combo.get("qty") or 0) * scale * wastage_factor


def calculate_accessory_bom(ipd_name, variant_demands, process_name=None):
	"""Scale `Item Production Detail.item_bom` rows for the same Lot demand payload."""
	ipd = frappe.get_doc("Item Production Detail", ipd_name)
	demands = _normalize_variant_demands(ipd, variant_demands)
	variants = [
		{"attrs": demand["attrs"], "qty": demand["qty"]}
		for demand in demands
	]
	aggregated = {}
	for bom_row in ipd.item_bom:
		if process_name and bom_row.process_name and bom_row.process_name != process_name:
			continue

		wastage_factor = 1 + (bom_row.wastage_pct or 0) / 100.0
		if bom_row.based_on_attribute_mapping and bom_row.attribute_mapping:
			for row in _resolve_mode_b(bom_row, variants, wastage_factor):
				_add_accessory_row(
					aggregated,
					row["item"],
					row.get("process"),
					row.get("uom"),
					row.get("qty") or 0,
					row.get("attrs") or {},
				)
			continue

		ratio = (bom_row.qty_of_bom_item or 0) / (bom_row.qty_of_product or 1)
		for demand in demands:
			attrs = _project_attrs_for_item(bom_row.item, demand["attrs"])
			_add_accessory_row(
				aggregated,
				bom_row.item,
				bom_row.process_name,
				bom_row.uom,
				demand["qty"] * ratio * wastage_factor,
				attrs,
			)
	return list(aggregated.values())


def calculate_lot_bom(ipd_name, variant_demands, process_names=None, include_outputs=False):
	"""Return both matrix deliverables and accessory BOM rows for a Lot demand."""
	return {
		"major_deliverables": calculate_major_deliverables(
			ipd_name,
			variant_demands,
			process_names=process_names,
			include_outputs=include_outputs,
		),
		"accessories": calculate_accessory_bom(ipd_name, variant_demands),
	}


def _normalize_variant_demands(ipd, variant_demands):
	variant_demands = frappe.parse_json(variant_demands) if isinstance(variant_demands, str) else variant_demands
	if isinstance(variant_demands, dict):
		variant_demands = [variant_demands]
	if not variant_demands:
		frappe.throw("Please provide at least one Item Variant and Qty.")

	demands = []
	for row in variant_demands:
		variant = row.get("item_variant") or row.get("variant") or row.get("name")
		qty = float(row.get("qty") or row.get("quantity") or row.get("required_qty") or 0)
		if not variant:
			frappe.throw("Item Variant is required to calculate BOM.")
		if qty <= 0:
			continue
		variant_item = frappe.db.get_value("Item Variant", variant, "item")
		if variant_item != ipd.item:
			frappe.throw(f"Item Variant {variant} does not belong to IPD item {ipd.item}.")
		demands.append({
			"item_variant": variant,
			"qty": qty,
			"attrs": _get_variant_attrs(variant),
		})

	if not demands:
		frappe.throw("Please provide a Qty greater than zero to calculate BOM.")
	return demands


def _normalize_process_filter(process_names):
	process_names = frappe.parse_json(process_names) if isinstance(process_names, str) else process_names
	if not process_names:
		return None
	if isinstance(process_names, str):
		return {process_names}
	return set(process_names)


def _get_variant_attrs(variant):
	rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": variant, "parenttype": "Item Variant"},
		fields=["attribute", "attribute_value"],
	)
	return {row.attribute: row.attribute_value for row in rows}


def _project_attrs_for_item(item, source_attrs):
	item_doc = frappe.get_cached_doc("Item", item)
	item_attrs = {row.attribute for row in item_doc.get("attributes") or []}
	return {
		attr: value
		for attr, value in (source_attrs or {}).items()
		if attr in item_attrs
	}


def _add_accessory_row(aggregated, item, process_name, uom, qty, attrs):
	item_variant = get_or_create_variant(item, attrs or {})
	key = (process_name, item_variant, uom)
	if key not in aggregated:
		aggregated[key] = {
			"source": "Item BOM",
			"process_name": process_name,
			"item": item,
			"item_variant": item_variant,
			"required_qty": 0.0,
			"uom": uom,
			"attrs": attrs or {},
		}
	aggregated[key]["required_qty"] += float(qty or 0)


def _get_process_stage_map(ipd):
	return {
		row.process_name: {
			"in_stage": row.in_stage,
			"out_stage": row.out_stage,
		}
		for row in ipd.get("ipd_processes") or []
	}


def _get_process_matrices(ipd_name, process_filter=None):
	filters = {
		"ipd": ipd_name,
		"docstatus": ["<", 2],
	}
	if process_filter:
		filters["process_name"] = ["in", list(process_filter)]

	matrix_names = frappe.get_all(
		"IPD Process Matrix",
		filters=filters,
		pluck="name",
		order_by="process_name asc, idx asc, name asc",
	)
	matrices_by_process = {}
	for matrix_name in matrix_names:
		matrix = frappe.get_doc("IPD Process Matrix", matrix_name)
		matrices_by_process.setdefault(matrix.process_name, []).append(matrix)
	return matrices_by_process


def _get_scale_side(ipd, stages):
	pack_out_stage = getattr(ipd, "pack_out_stage", None)
	if ipd.dependent_attribute and pack_out_stage and stages.get("out_stage") == pack_out_stage:
		return "input"
	return "output"


def _get_group_scale(ipd, demand, group, scale_side):
	combos = group.get(scale_side) or []
	if not combos:
		frappe.throw(f"IPD Process Matrix group does not have {scale_side} rows.")

	demand_attrs = {
		attr: value
		for attr, value in (demand.get("attrs") or {}).items()
		if not ipd.dependent_attribute or attr != ipd.dependent_attribute
	}
	matching_combo = next(
		(combo for combo in combos if _attrs_match(combo.get("attrs") or {}, demand_attrs)),
		None,
	)
	if matching_combo is None:
		matching_combo = combos[0]

	combo_qty = float(matching_combo.get("qty") or 0)
	if combo_qty <= 0:
		frappe.throw("IPD Process Matrix combination quantity must be greater than zero.")
	return float(demand["qty"] or 0) / combo_qty


def _add_matrix_group_rows(
	aggregated,
	ipd,
	matrix,
	reference_variant,
	group_index,
	group,
	scale,
	side,
	stage,
	reference_variants=None,
):
	parent_item = ipd.item
	dep_attr = ipd.dependent_attribute
	matrix_item = (matrix.input_item if side == "Input" else matrix.output_item) or parent_item
	side_key = side.lower()

	for combo in group[side_key]:
		attrs = dict(combo["attrs"] or {})
		if matrix_item == parent_item and dep_attr and stage is not None:
			attrs[dep_attr] = stage
		wastage_factor = 1 + ((combo.get("wastage_pct") or 0) / 100.0) if side == "Input" else 1
		required_qty = float(combo.get("qty") or 0) * scale * wastage_factor
		item_variant = get_or_create_variant(
			matrix_item,
			attrs,
			dependent_attr=ipd.dependent_attribute_mapping,
		)
		key = (side, matrix.process_name, item_variant, combo.get("uom"))
		if key not in aggregated:
			aggregated[key] = {
				"source": "IPD Process Matrix",
				"side": side,
				"process_name": matrix.process_name,
				"item": matrix_item,
				"item_variant": item_variant,
				"required_qty": 0.0,
				"uom": combo.get("uom"),
				"attrs": attrs,
				"reference_item_variant": reference_variant,
				"reference_item_variants": [],
				"matrix": matrix.name,
				"matrices": [],
				"group_indexes": [],
			}
		aggregated[key]["required_qty"] += required_qty
		for ref_variant in reference_variants or ([reference_variant] if reference_variant else []):
			if ref_variant not in aggregated[key]["reference_item_variants"]:
				aggregated[key]["reference_item_variants"].append(ref_variant)
		if matrix.name not in aggregated[key]["matrices"]:
			aggregated[key]["matrices"].append(matrix.name)
		if group_index not in aggregated[key]["group_indexes"]:
			aggregated[key]["group_indexes"].append(group_index)


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
		qty_of_bom_item = bom_combo.get("qty_of_bom_item") or bom_row.qty_of_bom_item or 0
		qty_of_product = bom_row.qty_of_product or 1
		qty = variant_qty * (qty_of_bom_item / qty_of_product) * wastage_factor
		results.append({
			"item": bom_row.item or mapping.bom_item,
			"qty": qty,
			"uom": bom_row.uom,
			"process": bom_row.process_name,
			"attrs": bom_combo.get("bom_attrs", {}),
		})
	return results


def _lookup_mode_b(mapping, variant_attrs):
	"""Find the row in mapping.values whose item-side attribute values match variant_attrs.
	Return {"qty_of_bom_item": float, "bom_attrs": {...}} for the matched bom-side row."""
	values = list(mapping.values)
	by_index = {}
	for v in values:
		by_index.setdefault(v.index, []).append(v)

	same_attrs = _get_same_mapping_attributes(mapping)
	item_key_attrs = [
		row.attribute
		for row in mapping.item_attributes
		if row.attribute not in same_attrs
	]
	variant_key = {
		attr: variant_attrs[attr]
		for attr in item_key_attrs
		if attr in variant_attrs
	}

	for idx, rows in by_index.items():
		item_side = {
			r.attribute: r.attribute_value
			for r in rows
			if r.type == "item" and r.attribute not in same_attrs
		}
		bom_side_rows = [r for r in rows if r.type == "bom"]
		if item_side == variant_key:
			bom_attrs = {r.attribute: r.attribute_value for r in bom_side_rows}
			for attr in same_attrs:
				if variant_attrs.get(attr):
					bom_attrs[attr] = variant_attrs[attr]
			qty_of_bom_item = next((r.quantity for r in rows if r.quantity), 0)
			return {"qty_of_bom_item": qty_of_bom_item, "bom_attrs": bom_attrs}
	return None


def _get_same_mapping_attributes(mapping):
	item_same_attrs = {
		row.attribute
		for row in mapping.item_attributes
		if row.same_attribute
	}
	return {
		row.attribute
		for row in mapping.bom_item_attributes
		if row.same_attribute and row.attribute in item_same_attrs
	}
