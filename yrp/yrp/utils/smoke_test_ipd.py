"""End-to-end smoke test for the new universal IPD + matrix engine.

Creates a tiny synthetic IPD (T-shirt-like), builds a stitching Process Matrix
with one group (3 panel inputs → 1 piece output), runs the engine for a demand
of 100 pieces, and asserts the input quantities scale correctly.

Run: bench --site yrp2.site execute yrp.yrp.utils.smoke_test_ipd.run
"""

import frappe

from yrp.yrp.utils.ipd_engine import get_process_io

ITEM_CODE = "_SMOKE_TSHIRT"
ATTRS = {
	"_SMOKE_Colour": ["_smk_Red", "_smk_Blue"],
	"_SMOKE_Size": ["_smk_45", "_smk_50"],
	"_SMOKE_Panel": ["_smk_Front", "_smk_Back", "_smk_Sleeve"],
	"_SMOKE_Stage": ["_smk_Cut", "_smk_Stitched"],
}


def run():
	_clean()
	_seed_attributes()
	item = _seed_item()
	_seed_attribute_mapping(item)
	process = _seed_process()
	ipd_name = _seed_ipd(item, process)
	_seed_matrix(ipd_name, process)
	_run_engine_check(ipd_name, process)
	frappe.db.commit()
	print("\n[SMOKE TEST] All assertions passed.")


def _clean():
	smoke_items = frappe.get_all("Item", filters={"name1": ITEM_CODE}, pluck="name")
	for item_name in smoke_items:
		for n in frappe.get_all("IPD Process Matrix", filters={"ipd": ["like", f"IPD-{item_name}-%"]}, pluck="name"):
			_force_delete("IPD Process Matrix", n)
		for n in frappe.get_all("Item Production Detail", filters={"item": item_name}, pluck="name"):
			_force_delete("Item Production Detail", n)
		item = frappe.get_doc("Item", item_name)
		for ar in item.get("attributes") or []:
			if ar.mapping and frappe.db.exists("Item Item Attribute Mapping", ar.mapping):
				frappe.delete_doc("Item Item Attribute Mapping", ar.mapping, force=1, ignore_permissions=True)
		_force_delete("Item", item_name)
		for n in frappe.get_all("Item Dependent Attribute Mapping", filters={"item": item_name}, pluck="name"):
			_force_delete("Item Dependent Attribute Mapping", n)
	for attr_name, values in ATTRS.items():
		for v in values:
			if frappe.db.exists("Item Attribute Value", v):
				_force_delete("Item Attribute Value", v)
		if frappe.db.exists("Item Attribute", attr_name):
			_force_delete("Item Attribute", attr_name)
	if frappe.db.exists("Process", "_SMOKE_Stitching"):
		_force_delete("Process", "_SMOKE_Stitching")
	frappe.db.commit()


def _force_delete(dt, name):
	try:
		doc = frappe.get_doc(dt, name)
		if getattr(doc, "docstatus", 0) == 1:
			doc.cancel()
		frappe.delete_doc(dt, name, force=1, ignore_permissions=True)
	except Exception as e:
		print(f"  cleanup {dt}/{name}: {e}")


def _seed_attributes():
	for attr_name, values in ATTRS.items():
		if not frappe.db.exists("Item Attribute", attr_name):
			doc = frappe.new_doc("Item Attribute")
			doc.attribute_name = attr_name
			doc.insert(ignore_permissions=True)
		for v in values:
			value_name = f"{attr_name}-{v}"
			if not frappe.db.exists("Item Attribute Value", value_name):
				vdoc = frappe.new_doc("Item Attribute Value")
				vdoc.attribute_name = attr_name
				vdoc.attribute_value = v
				vdoc.insert(ignore_permissions=True)


def _seed_item():
	existing = frappe.get_all("Item", filters={"name1": ITEM_CODE}, pluck="name", limit=1)
	if existing:
		return existing[0]
	doc = frappe.new_doc("Item")
	doc.name1 = ITEM_CODE
	doc.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	for attr_name in ATTRS:
		doc.append("attributes", {"attribute": attr_name})
	doc.insert(ignore_permissions=True)
	return doc.name


def _seed_attribute_mapping(item):
	"""Create Item Item Attribute Mapping per attribute and link from Item.attributes rows."""
	item_doc = frappe.get_doc("Item", item)
	for attr_row in item_doc.attributes:
		if attr_row.mapping:
			# pre-existing — populate values
			mapping = frappe.get_doc("Item Item Attribute Mapping", attr_row.mapping)
			mapping.values = []
			for v in ATTRS[attr_row.attribute]:
				mapping.append("values", {"attribute_value": v})
			mapping.save(ignore_permissions=True)
		else:
			mapping = frappe.new_doc("Item Item Attribute Mapping")
			mapping.attribute_name = attr_row.attribute
			for v in ATTRS[attr_row.attribute]:
				mapping.append("values", {"attribute_value": v})
			mapping.insert(ignore_permissions=True)
			attr_row.mapping = mapping.name
	item_doc.save(ignore_permissions=True)


def _seed_process():
	if not frappe.db.exists("Process", "_SMOKE_Stitching"):
		doc = frappe.new_doc("Process")
		doc.process_name = "_SMOKE_Stitching"
		doc.insert(ignore_permissions=True)
	return "_SMOKE_Stitching"


def _seed_ipd(item, process):
	ipd = frappe.new_doc("Item Production Detail")
	ipd.item = item
	ipd.version = "smoke"
	ipd.approval_status = "Approved"
	ipd.primary_attribute = "_SMOKE_Size"
	ipd.dependent_attribute = "_SMOKE_Stage"
	for attr_name in ATTRS:
		ipd.append("item_attributes", {"attribute": attr_name})
	ipd.append("ipd_processes", {
		"process_name": process,
		"in_stage": "_smk_Cut",
		"out_stage": "_smk_Stitched",
	})
	# dependent_attribute_mapping is required when dependent_attribute is set
	dam = frappe.new_doc("Item Dependent Attribute Mapping")
	dam.item = item
	dam.dependent_attribute = "_SMOKE_Stage"
	try:
		dam.insert(ignore_permissions=True)
		ipd.dependent_attribute_mapping = dam.name
	except Exception as e:
		print(f"  dam insert: {e}")
		# Fall back: drop dependent attribute on IPD
		ipd.dependent_attribute = None
	ipd.insert(ignore_permissions=True)
	return ipd.name


def _seed_matrix(ipd_name, process):
	matrix = frappe.new_doc("IPD Process Matrix")
	matrix.ipd = ipd_name
	matrix.process_name = process
	for a in ["_SMOKE_Panel", "_SMOKE_Colour", "_SMOKE_Size"]:
		matrix.append("input_attributes", {"attribute": a})
	for a in ["_SMOKE_Colour", "_SMOKE_Size"]:
		matrix.append("output_attributes", {"attribute": a})

	# Group 1: Red-45 piece — 1 Front-Red-45 + 1 Back-Red-45 + 2 Sleeve-Blue-45 → 1 Red-45 piece
	group_data = [
		(1, "Input", 1, 1, [("_SMOKE_Panel", "_smk_Front"), ("_SMOKE_Colour", "_smk_Red"), ("_SMOKE_Size", "_smk_45")]),
		(1, "Input", 2, 1, [("_SMOKE_Panel", "_smk_Back"), ("_SMOKE_Colour", "_smk_Red"), ("_SMOKE_Size", "_smk_45")]),
		(1, "Input", 3, 2, [("_SMOKE_Panel", "_smk_Sleeve"), ("_SMOKE_Colour", "_smk_Blue"), ("_SMOKE_Size", "_smk_45")]),
		(1, "Output", 1, 1, [("_SMOKE_Colour", "_smk_Red"), ("_SMOKE_Size", "_smk_45")]),
	]
	for gidx, side, ci, qty, attrs in group_data:
		matrix.append("combinations", {
			"group_index": gidx,
			"side": side,
			"combo_index": ci,
			"quantity": qty,
		})
		for attr, val in attrs:
			matrix.append("combination_attributes", {
				"group_index": gidx,
				"side": side,
				"combo_index": ci,
				"attribute": attr,
				"attribute_value": val,
			})
	matrix.insert(ignore_permissions=True)
	return matrix.name


def _run_engine_check(ipd_name, process_name):
	demand = [{"attrs": {"_SMOKE_Colour": "_smk_Red", "_SMOKE_Size": "_smk_45"}, "qty": 100}]
	result = get_process_io(ipd_name, process_name, demand)
	inputs = {(_key(i["attrs"]),): i["qty"] for i in result["inputs"]}
	outputs = {(_key(o["attrs"]),): o["qty"] for o in result["outputs"]}
	print("INPUTS  :", inputs)
	print("OUTPUTS :", outputs)
	expected = {
		(_key({"_SMOKE_Panel": "_smk_Front", "_SMOKE_Colour": "_smk_Red", "_SMOKE_Size": "_smk_45"}),): 100,
		(_key({"_SMOKE_Panel": "_smk_Back", "_SMOKE_Colour": "_smk_Red", "_SMOKE_Size": "_smk_45"}),): 100,
		(_key({"_SMOKE_Panel": "_smk_Sleeve", "_SMOKE_Colour": "_smk_Blue", "_SMOKE_Size": "_smk_45"}),): 200,
	}
	for k, v in expected.items():
		assert k in inputs, f"missing input {k}"
		assert abs(inputs[k] - v) < 1e-6, f"input {k}: got {inputs[k]}, expected {v}"
	out_red_45 = outputs.get((_key({"_SMOKE_Colour": "_smk_Red", "_SMOKE_Size": "_smk_45"}),))
	assert out_red_45 == 100, f"output Red-45: got {out_red_45}, expected 100"


def _key(attrs):
	return tuple(sorted(attrs.items()))
