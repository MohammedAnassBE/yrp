"""Utilities to split or merge IPD Process Matrices by reference variant."""

import frappe

from yrp.yrp.doctype.item.item import get_or_create_variant
from yrp.yrp.doctype.item_dependent_attribute_mapping.item_dependent_attribute_mapping import (
	get_dependent_attribute_details,
)


def run(ipd, reference_stage=None, delete_original=True, dry_run=False):
	"""Split existing process matrices into one matrix per reference item variant.

	The reference variant is inferred from each group's input/output attributes,
	filtered to the attributes valid for `reference_stage`.
	"""
	ipd_doc = frappe.get_doc("Item Production Detail", ipd)
	reference_stage = reference_stage or getattr(ipd_doc, "pack_in_stage", None) or getattr(
		ipd_doc, "pack_out_stage", None
	)
	allowed_attrs = _reference_attributes(ipd_doc, reference_stage)

	matrix_names = frappe.get_all(
		"IPD Process Matrix",
		filters={
			"ipd": ipd,
			"reference_item_variant": ["in", ["", None]],
		},
		pluck="name",
		order_by="process_name asc, name asc",
	)
	summary = []
	for matrix_name in matrix_names:
		matrix = frappe.get_doc("IPD Process Matrix", matrix_name)
		group_refs = _group_reference_variants(matrix, ipd_doc, reference_stage, allowed_attrs)
		created = []
		for reference_variant, group_indexes in sorted(group_refs.items()):
			if dry_run:
				created.append({
					"reference_item_variant": reference_variant,
					"groups": len(group_indexes),
				})
				continue
			new_doc = _copy_matrix_for_groups(matrix, reference_variant, group_indexes)
			new_doc.insert(ignore_permissions=True)
			created.append(new_doc.name)
		if not dry_run and delete_original and created:
			frappe.delete_doc("IPD Process Matrix", matrix.name, ignore_permissions=True, force=True)
		summary.append({
			"source": matrix_name,
			"process": matrix.process_name,
			"created": created,
			"reference_count": len(group_refs),
		})
	if not dry_run:
		frappe.db.commit()
	return summary


def merge_process_to_single_matrix(ipd, process_name, delete_original=True, dry_run=False):
	"""Merge all matrices for one IPD/process into one generic matrix.

	This is useful when previously split exact-reference matrices should become a
	single pool-style process matrix. The merged matrix intentionally leaves
	`reference_item_variant` blank.
	"""
	matrix_names = frappe.get_all(
		"IPD Process Matrix",
		filters={
			"ipd": ipd,
			"process_name": process_name,
			"docstatus": ["<", 2],
		},
		pluck="name",
		order_by="name asc",
	)
	if not matrix_names:
		frappe.throw(f"No IPD Process Matrix found for IPD {ipd} / process {process_name}.")

	matrices = [frappe.get_doc("IPD Process Matrix", name) for name in matrix_names]
	if dry_run:
		return {
			"ipd": ipd,
			"process_name": process_name,
			"source_matrices": len(matrices),
			"source_groups": sum(_group_count(matrix) for matrix in matrices),
			"will_delete_original": bool(delete_original),
		}

	new_doc = frappe.new_doc("IPD Process Matrix")
	new_doc.ipd = ipd
	new_doc.process_name = process_name
	new_doc.reference_item_variant = None
	new_doc.input_item = matrices[0].input_item
	new_doc.output_item = matrices[0].output_item

	for attribute in _unique_child_values(matrices, "input_attributes", "attribute"):
		new_doc.append("input_attributes", {"attribute": attribute})
	for attribute in _unique_child_values(matrices, "output_attributes", "attribute"):
		new_doc.append("output_attributes", {"attribute": attribute})

	group_index = 0
	for matrix in matrices:
		for old_group_index in _matrix_group_indexes(matrix):
			group_index += 1
			_append_group(new_doc, matrix, old_group_index, group_index)

	new_doc.insert(ignore_permissions=True)

	if delete_original:
		for matrix_name in matrix_names:
			frappe.delete_doc("IPD Process Matrix", matrix_name, ignore_permissions=True, force=True)

	frappe.db.commit()
	return {
		"ipd": ipd,
		"process_name": process_name,
		"created": new_doc.name,
		"deleted": matrix_names if delete_original else [],
		"groups": group_index,
	}


def _reference_attributes(ipd_doc, reference_stage):
	if not (ipd_doc.dependent_attribute_mapping and reference_stage):
		return [
			row.attribute
			for row in ipd_doc.item_attributes
			if row.attribute != ipd_doc.dependent_attribute
		]
	details = get_dependent_attribute_details(ipd_doc.dependent_attribute_mapping)
	return [
		attr
		for attr in details.get("attr_list", {}).get(reference_stage, {}).get("attributes", [])
		if attr != ipd_doc.dependent_attribute
	]


def _unique_child_values(matrices, table_field, fieldname):
	values = []
	for matrix in matrices:
		for row in matrix.get(table_field) or []:
			value = getattr(row, fieldname, None)
			if value and value not in values:
				values.append(value)
	return values


def _matrix_group_indexes(matrix):
	return sorted({row.group_index for row in matrix.combinations})


def _group_count(matrix):
	return len(_matrix_group_indexes(matrix))


def _append_group(new_doc, matrix, old_group_index, new_group_index):
	for row in matrix.combinations:
		if row.group_index != old_group_index:
			continue
		new_doc.append("combinations", {
			"group_index": new_group_index,
			"group_name": row.group_name,
			"side": row.side,
			"combo_index": row.combo_index,
			"quantity": row.quantity,
			"uom": row.uom,
			"wastage_pct": row.wastage_pct,
		})

	for row in matrix.combination_attributes:
		if row.group_index != old_group_index:
			continue
		new_doc.append("combination_attributes", {
			"group_index": new_group_index,
			"side": row.side,
			"combo_index": row.combo_index,
			"attribute": row.attribute,
			"attribute_value": row.attribute_value,
		})


def _group_reference_variants(matrix, ipd_doc, reference_stage, allowed_attrs):
	group_refs = {}
	group_indexes = sorted({row.group_index for row in matrix.combinations})
	for group_index in group_indexes:
		reference_variant = _infer_reference_variant(
			matrix,
			ipd_doc,
			reference_stage,
			allowed_attrs,
			group_index,
		)
		if not reference_variant:
			frappe.throw(
				f"Could not infer reference variant for {matrix.name} group {group_index}."
			)
		group_refs.setdefault(reference_variant, []).append(group_index)
	return group_refs


def _infer_reference_variant(matrix, ipd_doc, reference_stage, allowed_attrs, group_index):
	for side in ("Output", "Input"):
		combo_indexes = sorted({
			row.combo_index
			for row in matrix.combination_attributes
			if row.group_index == group_index and row.side == side
		})
		for combo_index in combo_indexes:
			raw_attrs = {
				row.attribute: row.attribute_value
				for row in matrix.combination_attributes
				if row.group_index == group_index
				and row.side == side
				and row.combo_index == combo_index
			}
			attrs = {k: v for k, v in raw_attrs.items() if k in allowed_attrs}
			if ipd_doc.dependent_attribute and reference_stage:
				attrs[ipd_doc.dependent_attribute] = reference_stage
			try:
				return get_or_create_variant(
					ipd_doc.item,
					attrs,
					dependent_attr=ipd_doc.dependent_attribute_mapping,
				)
			except Exception:
				continue
	return None


def _copy_matrix_for_groups(matrix, reference_variant, group_indexes):
	group_indexes = list(group_indexes)
	group_map = {old: idx + 1 for idx, old in enumerate(group_indexes)}

	new_doc = frappe.new_doc("IPD Process Matrix")
	new_doc.ipd = matrix.ipd
	new_doc.process_name = matrix.process_name
	new_doc.reference_item_variant = reference_variant
	new_doc.input_item = matrix.input_item
	new_doc.output_item = matrix.output_item

	for row in matrix.input_attributes:
		new_doc.append("input_attributes", {"attribute": row.attribute})
	for row in matrix.output_attributes:
		new_doc.append("output_attributes", {"attribute": row.attribute})

	for row in matrix.combinations:
		if row.group_index not in group_map:
			continue
		new_doc.append("combinations", {
			"group_index": group_map[row.group_index],
			"group_name": row.group_name,
			"side": row.side,
			"combo_index": row.combo_index,
			"quantity": row.quantity,
			"uom": row.uom,
			"wastage_pct": row.wastage_pct,
		})

	for row in matrix.combination_attributes:
		if row.group_index not in group_map:
			continue
		new_doc.append("combination_attributes", {
			"group_index": group_map[row.group_index],
			"side": row.side,
			"combo_index": row.combo_index,
			"attribute": row.attribute,
			"attribute_value": row.attribute_value,
		})

	return new_doc
