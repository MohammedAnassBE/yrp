"""Recreate 'Mens Sports Vest - 11225-1' as a Common IPD V2 on yrp2.site.

Source data is the dump of the production_api IPD; the target is the new
yrp Common IPD with matrices for Cutting, Stitching, Packing.

Run: bench --site yrp2.site execute yrp.yrp.utils.seed_vest_v2.run
"""

import json
import os

import frappe

DUMP_PATH = "/tmp/source_ipd_dump.json"

ITEM_CODE_LABEL = "Mens Sports Vest - 11225"
IPD_VERSION = "1"


def run():
	if not os.path.exists(DUMP_PATH):
		frappe.throw(f"Source dump not found at {DUMP_PATH}. Run inspector first.")
	with open(DUMP_PATH) as f:
		src = json.load(f)

	_cleanup_previous()
	item_name = _ensure_item(src)
	_ensure_processes()
	_ensure_box_items()

	ipd_name = _create_ipd(item_name, src)
	cutting = _create_cutting_matrix(ipd_name, src)
	stitching = _create_stitching_matrix(ipd_name, src)
	packing = _create_packing_matrix(ipd_name, src)
	_create_item_bom(ipd_name, src)

	frappe.db.commit()
	print(f"\n[OK] Recreated IPD: {ipd_name}")
	print(f"  - cutting matrix : {cutting}")
	print(f"  - stitching matrix: {stitching}")
	print(f"  - packing matrix : {packing}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup_previous():
	# Find items linked to the vest by name1, then any IPDs/matrices for them
	vest_items = frappe.get_all("Item", filters={"name1": ITEM_CODE_LABEL}, pluck="name")
	for item_name in vest_items:
		for n in frappe.get_all("IPD Process Matrix", filters={"ipd": ["like", f"%{item_name}%"]}, pluck="name"):
			_force_del("IPD Process Matrix", n)
		for n in frappe.get_all("Item Production Detail", filters={"item": item_name}, pluck="name"):
			_force_del("Item Production Detail", n)


def _force_del(dt, name):
	try:
		doc = frappe.get_doc(dt, name)
		if getattr(doc, "docstatus", 0) == 1:
			doc.cancel()
		frappe.delete_doc(dt, name, force=1, ignore_permissions=True)
	except Exception as e:
		print(f"  cleanup {dt}/{name}: {e}")


# ---------------------------------------------------------------------------
# Prereqs
# ---------------------------------------------------------------------------

def _ensure_item(src):
	"""Create the parent Item with Stage/Panel/Colour/Size attributes."""
	existing = frappe.get_all("Item", filters={"name1": ITEM_CODE_LABEL}, pluck="name", limit=1)
	if existing:
		print(f"[item] reusing existing: {existing[0]}")
		return existing[0]

	# Ensure attribute values exist (Item Attribute Value records)
	_ensure_attribute_values(src)

	item = frappe.new_doc("Item")
	item.name1 = ITEM_CODE_LABEL
	item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	for attr in ["Stage", "Panel", "Colour", "Size"]:
		item.append("attributes", {"attribute": attr})
	item.insert(ignore_permissions=True)

	# Populate Item Item Attribute Mapping for each attribute
	values_per_attr = _values_per_attr(src)
	item.reload()
	for ar in item.attributes:
		mapping_name = ar.mapping
		if not mapping_name:
			m = frappe.new_doc("Item Item Attribute Mapping")
			m.attribute_name = ar.attribute
			m.insert(ignore_permissions=True)
			mapping_name = m.name
			ar.mapping = mapping_name
		mapping = frappe.get_doc("Item Item Attribute Mapping", mapping_name)
		mapping.values = []
		for v in values_per_attr[ar.attribute]:
			mapping.append("values", {"attribute_value": v})
		mapping.save(ignore_permissions=True)
	item.save(ignore_permissions=True)
	print(f"[item] created: {item.name}")
	return item.name


def _values_per_attr(src):
	sizes = sorted({i["Size"] for i in src["cutting_items_json"]["items"]})
	panels = sorted({i["Panel"] for i in src["cutting_items_json"]["items"]})
	colours = sorted({r["major_attribute_value"] for r in src["stiching_item_combination_details"]})
	stages = ["Cut", "Piece", "Pack"]
	return {"Size": sizes, "Panel": panels, "Colour": colours, "Stage": stages}


def _ensure_attribute_values(src):
	values_per_attr = _values_per_attr(src)
	for attr, values in values_per_attr.items():
		for v in values:
			if not frappe.db.exists("Item Attribute Value", v):
				doc = frappe.new_doc("Item Attribute Value")
				doc.attribute_name = attr
				doc.attribute_value = v
				try:
					doc.insert(ignore_permissions=True)
				except Exception as e:
					print(f"  attr value {attr}/{v}: {e}")


def _ensure_processes():
	for p in ["Cutting", "Stitching", "Packing", "Yolk Fusing", "Tower Fusing", "Chest Fusing", "Ironing"]:
		if not frappe.db.exists("Process", p):
			doc = frappe.new_doc("Process")
			doc.process_name = p
			doc.insert(ignore_permissions=True)
			print(f"[process] created: {p}")


def _ensure_box_items():
	for code in [
		"Essdee Mens Sports Vest - 11225 Top Box",
		"Essdee Mens Sports Vest - 11225 Bottom Box",
	]:
		if frappe.get_all("Item", filters={"name1": code}, limit=1):
			continue
		doc = frappe.new_doc("Item")
		doc.name1 = code
		doc.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
		doc.insert(ignore_permissions=True)
		print(f"[item] created: {doc.name}")


# ---------------------------------------------------------------------------
# IPD parent
# ---------------------------------------------------------------------------

def _create_ipd(item_name, src):
	ipd = frappe.new_doc("Item Production Detail")
	ipd.item = item_name
	ipd.version = IPD_VERSION
	ipd.approval_status = "Approved"
	ipd.primary_attribute = "Size"
	# dependent_attribute left None to avoid Item Dependent Attribute Mapping setup
	for a in ["Stage", "Panel", "Colour", "Size"]:
		ipd.append("item_attributes", {"attribute": a})
	# Full process sequence covering every process the source IPD references
	# (scalar main processes + ipd_processes table + processes from BOM rows).
	# Sequence: Cutting -> Yolk Fusing -> Tower Fusing -> Chest Fusing ->
	#           Stitching -> Ironing -> Packing
	# Embellishment / finishing processes don't transform items (no matrix);
	# their consumables flow through Item BOM only, so mapping stays None.
	for p in [
		{"process_name": "Cutting", "in_stage": None, "out_stage": "Cut"},
		{"process_name": "Yolk Fusing", "in_stage": "Cut", "out_stage": "Cut"},
		{"process_name": "Tower Fusing", "in_stage": "Cut", "out_stage": "Cut"},
		{"process_name": "Chest Fusing", "in_stage": "Cut", "out_stage": "Cut"},
		{"process_name": "Stitching", "in_stage": "Cut", "out_stage": "Piece"},
		{"process_name": "Ironing", "in_stage": "Piece", "out_stage": "Piece"},
		{"process_name": "Packing", "in_stage": "Piece", "out_stage": "Pack"},
	]:
		ipd.append("ipd_processes", p)
	ipd.insert(ignore_permissions=True)
	print(f"[ipd] created: {ipd.name}")
	return ipd.name


# ---------------------------------------------------------------------------
# Cutting Matrix
# ---------------------------------------------------------------------------

def _create_cutting_matrix(ipd_name, src):
	"""One group per (Size, Panel). Input = fabric weight kg. Output = 1 panel."""
	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Cutting"
	for a in ["Size", "Panel"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Size", "Panel"]:
		m.append("output_attributes", {"attribute": a})

	cloth_item = "Dyed Fabric 36's RL"
	cloth_item_id = _resolve_item_by_name1(cloth_item)
	uom_kg = "kg"
	uom_nos = "Nos"

	gidx = 0
	for cut in src["cutting_items_json"]["items"]:
		gidx += 1
		# Input: cloth weight (kg) for this Size+Panel combination
		_add_combo(m, gidx, "Input", 1, cloth_item_id, cut["Weight"], uom_kg,
			[("Size", cut["Size"]), ("Panel", cut["Panel"])])
		# Output: 1 panel piece for this Size+Panel
		_add_combo(m, gidx, "Output", 1, None, 1, uom_nos,
			[("Size", cut["Size"]), ("Panel", cut["Panel"])])

	m.insert(ignore_permissions=True)
	print(f"[matrix] cutting: {m.name} ({gidx} groups)")
	return m.name


def _resolve_item_by_name1(name1):
	rows = frappe.get_all("Item", filters={"name1": name1}, pluck="name", limit=1)
	return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Stitching Matrix
# ---------------------------------------------------------------------------

def _create_stitching_matrix(ipd_name, src):
	"""One group per (major_Colour, Size). Inputs: 4 panel rows with colour
	mapping from stiching_item_combination_details. Output: 1 piece.
	Panel qty multipliers come from stiching_item_details."""
	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Stitching"
	for a in ["Panel", "Colour", "Size"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Colour", "Size"]:
		m.append("output_attributes", {"attribute": a})

	panel_qty = {r["stiching_attribute_value"]: r["quantity"] for r in src["stiching_item_details"]}
	# stiching_item_combination_details: per (major_colour, panel) -> actual_colour
	colour_map = {}
	for r in src["stiching_item_combination_details"]:
		colour_map.setdefault(r["major_attribute_value"], {})[r["set_item_attribute_value"]] = r["attribute_value"]

	sizes = sorted({i["Size"] for i in src["cutting_items_json"]["items"]})

	gidx = 0
	uom_nos = "Nos"
	for major_colour, panel_to_colour in colour_map.items():
		for size in sizes:
			gidx += 1
			combo = 0
			for panel, actual_colour in panel_to_colour.items():
				combo += 1
				qty = panel_qty.get(panel, 1)
				_add_combo(m, gidx, "Input", combo, None, qty, uom_nos,
					[("Panel", panel), ("Colour", actual_colour), ("Size", size)])
			_add_combo(m, gidx, "Output", 1, None, 1, uom_nos,
				[("Colour", major_colour), ("Size", size)])

	m.insert(ignore_permissions=True)
	print(f"[matrix] stitching: {m.name} ({gidx} groups)")
	return m.name


# ---------------------------------------------------------------------------
# Packing Matrix
# ---------------------------------------------------------------------------

def _create_packing_matrix(ipd_name, src):
	"""packing_combo=5: 5 pieces of the same Colour+Size pack into 1 unit."""
	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Packing"
	for a in ["Colour", "Size"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Colour", "Size"]:
		m.append("output_attributes", {"attribute": a})

	colours = sorted({r["major_attribute_value"] for r in src["stiching_item_combination_details"]})
	sizes = sorted({i["Size"] for i in src["cutting_items_json"]["items"]})
	combo = src["packing_combo"] or 5
	uom_nos = "Nos"

	gidx = 0
	for colour in colours:
		for size in sizes:
			gidx += 1
			_add_combo(m, gidx, "Input", 1, None, combo, uom_nos,
				[("Colour", colour), ("Size", size)])
			_add_combo(m, gidx, "Output", 1, None, 1, uom_nos,
				[("Colour", colour), ("Size", size)])
	m.insert(ignore_permissions=True)
	print(f"[matrix] packing: {m.name} ({gidx} groups)")
	return m.name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_combo(matrix, group_index, side, combo_index, item, qty, uom, attr_pairs):
	matrix.append("combinations", {
		"group_index": group_index,
		"side": side,
		"combo_index": combo_index,
		"item": item,
		"quantity": qty,
		"uom": uom,
	})
	for attr, value in attr_pairs:
		matrix.append("combination_attributes", {
			"group_index": group_index,
			"side": side,
			"combo_index": combo_index,
			"attribute": attr,
			"attribute_value": value,
		})


# ---------------------------------------------------------------------------
# Item BOM (consumables)
# ---------------------------------------------------------------------------

def _create_item_bom(ipd_name, src):
	ipd = frappe.get_doc("Item Production Detail", ipd_name)
	for r in src["item_bom"]:
		# Mode B mappings are not portable across sites; downgrade to Mode A flat ratio.
		# Resolve item by name1 if needed.
		item_id = r["item"] if frappe.db.exists("Item", r["item"]) else _resolve_item_by_name1(r["item"])
		if not item_id:
			print(f"  [skip] BOM item not found on yrp2.site: {r['item']}")
			continue
		ipd.append("item_bom", {
			"item": item_id,
			"qty_of_product": r["qty_of_product"],
			"qty_of_bom_item": r["qty_of_bom_item"],
			"uom": r["uom"],
			"process_name": r["process_name"] if frappe.db.exists("Process", r["process_name"]) else None,
			"dependent_attribute_value": r.get("dependent_attribute_value") if frappe.db.exists("Item Attribute Value", r.get("dependent_attribute_value") or "") else None,
			"based_on_attribute_mapping": 0,
			"attribute_mapping": None,
		})
	ipd.save(ignore_permissions=True)
	print(f"[bom] added {len(ipd.item_bom)} rows")
