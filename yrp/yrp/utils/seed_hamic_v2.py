"""Recreate 'Hamic - Racer Collar T-Shirt RNS-1' as a Common IPD V2 on yrp2.site.

Data source: /tmp/hamic_dump.json (produced by production_api.inspect_full_ipd.run on yrp.site).

Structure built (driven entirely by the dump):
  - All Item Attribute Values referenced by the dump
  - All Items referenced (parent + cloth + BOM + Collar + boxes etc.) with their attribute mappings
  - One Item Production Detail with Stage/Panel/Colour/Size, primary=Size, dependent=Stage, 4 processes
  - Four IPD Process Matrices: Cutting, Yolk Fusing, Stitching, Ironing and Packing
  - Item BOM rows (Mode B downgraded to Mode A flat ratio for portability)

Run: bench --site yrp2.site execute yrp.yrp.utils.seed_hamic_v2.run
"""

import json
import os

import frappe

DUMP_PATH = "/tmp/hamic_dump.json"


def run():
	if not os.path.exists(DUMP_PATH):
		frappe.throw(f"Dump not found at {DUMP_PATH}. Run inspect_full_ipd on yrp.site first.")
	with open(DUMP_PATH) as f:
		dump = json.load(f)

	src = dump["ipd"]
	items_full = dump["items_full"]
	wos = dump["work_orders"]

	_cleanup(src)
	for u in ["Nos", "Pieces", "Kg", "Box", "Piece", "Unit"]:
		_ensure_uom(u)
	_seed_attribute_values(dump["attribute_values"])
	item_id_map = _seed_items(items_full, dump["attribute_values"])
	ipd_name = _create_ipd(src, item_id_map)
	attr_values = dump["attribute_values"]
	cutting = _create_cutting_matrix(ipd_name, src, item_id_map, attr_values)
	yolk = _create_yolk_fusing_matrix(ipd_name, src, item_id_map, wos, attr_values)
	stitching = _create_stitching_matrix(ipd_name, src, item_id_map, attr_values)
	packing = _create_packing_matrix(ipd_name, src, item_id_map, attr_values)

	frappe.db.commit()
	print(f"\n[OK] Recreated IPD: {ipd_name}")
	print(f"  cutting matrix : {cutting}")
	print(f"  yolk matrix    : {yolk}")
	print(f"  stitching matrix: {stitching}")
	print(f"  packing matrix : {packing}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup(src):
	parent_label = src["item"]
	parent_items = frappe.get_all("Item", filters={"name1": parent_label}, pluck="name")
	for item_name in parent_items:
		# delete IPDs for that item
		for ipd_n in frappe.get_all("Item Production Detail", filters={"item": item_name}, pluck="name"):
			# delete matrices for that IPD
			for m in frappe.get_all("IPD Process Matrix", filters={"ipd": ipd_n}, pluck="name"):
				_force_del("IPD Process Matrix", m)
			_force_del("Item Production Detail", ipd_n)


def _force_del(dt, name):
	try:
		# cancel first if submittable & submitted
		ds = frappe.db.get_value(dt, name, "docstatus")
		if ds == 1:
			d = frappe.get_doc(dt, name)
			d.cancel()
		frappe.delete_doc(dt, name, force=1, ignore_permissions=True, delete_permanently=True)
	except Exception as e:
		print(f"  [skip-delete] {dt} {name}: {e}")


# ---------------------------------------------------------------------------
# Attribute values
# ---------------------------------------------------------------------------

def _seed_attribute_values(attr_values_map):
	for attr, values in attr_values_map.items():
		if not frappe.db.exists("Item Attribute", attr):
			print(f"  [warn] Item Attribute {attr} not on yrp2.site; skipping value seed for it")
			continue
		for v in values:
			if frappe.db.exists("Item Attribute Value", v):
				continue
			doc = frappe.new_doc("Item Attribute Value")
			doc.attribute_value = v
			doc.attribute_name = attr
			doc.flags.name_set = True
			doc.name = v
			doc.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _ensure_uom(uom):
	if not uom:
		return None
	if not frappe.db.exists("UOM", uom):
		u = frappe.new_doc("UOM")
		u.uom_name = uom
		u.insert(ignore_permissions=True)
	return uom


def _seed_items(items_full, attr_values):
	"""Create or update each Item from the dump. Returns name1->item_id map.

	For each Item attribute, populate the Item Item Attribute Mapping with values.
	If the source mapping is empty (production_api leaves Panel/Colour empty on the
	parent), fall back to the global attribute_values map collected from the dump.
	"""
	id_map = {}
	for src_id, info in items_full.items():
		name1 = info.get("name1") or src_id
		# Match by name1; reuse existing Item if any
		existing = frappe.get_all("Item", filters={"name1": name1}, pluck="name")
		if existing:
			item_id = existing[0]
		else:
			doc = frappe.new_doc("Item")
			doc.name1 = name1
			grp = info.get("item_group")
			if not grp or not frappe.db.exists("Item Group", grp):
				# pick any existing group
				existing_groups = frappe.get_all("Item Group", pluck="name", limit=1)
				grp = existing_groups[0] if existing_groups else None
				if not grp:
					ig = frappe.new_doc("Item Group")
					ig.item_group_name = "Hamic Seed"
					ig.insert(ignore_permissions=True)
					grp = ig.name
			doc.item_group = grp
			doc.default_unit_of_measure = _ensure_uom(info.get("default_uom") or "Nos")
			# attributes
			for ar in info.get("attributes") or []:
				if not frappe.db.exists("Item Attribute", ar["attribute"]):
					continue
				doc.append("attributes", {"attribute": ar["attribute"]})
			doc.insert(ignore_permissions=True)
			item_id = doc.name

		# Build/update Item Item Attribute Mapping per attribute
		idoc = frappe.get_doc("Item", item_id)
		for ar in info.get("attributes") or []:
			if not frappe.db.exists("Item Attribute", ar["attribute"]):
				continue
			values = ar.get("values") or []
			# Fallback to global values seen across the dump if source mapping is empty
			if not values:
				values = attr_values.get(ar["attribute"], [])
			# find or create mapping
			row = next((r for r in idoc.attributes if r.attribute == ar["attribute"]), None)
			if not row:
				continue
			if row.mapping and frappe.db.exists("Item Item Attribute Mapping", row.mapping):
				m = frappe.get_doc("Item Item Attribute Mapping", row.mapping)
				m.values = []
			else:
				m = frappe.new_doc("Item Item Attribute Mapping")
				m.attribute_name = ar["attribute"]
			for v in values:
				if not frappe.db.exists("Item Attribute Value", v):
					continue
				m.append("values", {"attribute_value": v})
			if not m.name or not frappe.db.exists("Item Item Attribute Mapping", m.name):
				m.insert(ignore_permissions=True)
			else:
				m.save(ignore_permissions=True)
			row.mapping = m.name
		idoc.save(ignore_permissions=True)
		id_map[name1] = item_id
		print(f"  item: {item_id} ({name1})  attrs={len(info.get('attributes') or [])}")
	return id_map


# ---------------------------------------------------------------------------
# IPD parent doc
# ---------------------------------------------------------------------------

def _create_idam(parent_item_id, dependent_attribute, stages_in_order, depending_attrs_per_stage):
	"""Create or refresh an Item Dependent Attribute Mapping for the parent item.

	Args:
	    stages_in_order: list of dependent_attribute_value names ordered by flow
	        (e.g. ["Cut", "Piece", "Pack"]).
	    depending_attrs_per_stage: {stage: [attribute_name, ...]} — which attributes are
	        in scope at that stage (e.g. Cut needs Panel; Piece doesn't).
	"""
	# Drop existing IDAMs for this item
	for n in frappe.get_all("Item Dependent Attribute Mapping", filters={"item": parent_item_id}, pluck="name"):
		_force_del("Item Dependent Attribute Mapping", n)
	d = frappe.new_doc("Item Dependent Attribute Mapping")
	d.item = parent_item_id
	d.dependent_attribute = dependent_attribute
	for stage in stages_in_order:
		for attr in depending_attrs_per_stage.get(stage, []):
			d.append("mapping", {"dependent_attribute_value": stage, "depending_attribute": attr})
		stage_uom = "Box" if stage == "Pack" else "Pieces"
		d.append("details", {
			"attribute_value": stage,
			"uom": _ensure_uom(stage_uom),
			"display_name": stage,
			"is_final": 1 if stage == stages_in_order[-1] else 0,
		})
	d.insert(ignore_permissions=True)
	return d.name


def _create_ipd(src, id_map):
	parent_item_id = id_map[src["item"]]
	# Build IDAM for Stage:
	#   Cut -> [Panel, Colour, Size] (panels exist at this stage)
	#   Piece -> [Colour, Size]      (panels collapsed into a piece)
	#   Pack -> [Size]                (colour collapsed into a pack)
	stages_in_order = ["Cut", "Piece", "Pack"]
	depending_attrs = {
		"Cut": ["Panel", "Colour", "Size"],
		"Piece": ["Colour", "Size"],
		"Pack": ["Size"],
	}
	idam = _create_idam(parent_item_id, "Stage", stages_in_order, depending_attrs)

	doc = frappe.new_doc("Item Production Detail")
	doc.item = parent_item_id
	doc.version = str(src.get("version") or "1")
	doc.approval_status = "Approved"
	doc.primary_attribute = src.get("primary_item_attribute")
	doc.dependent_attribute = "Stage"
	doc.dependent_attribute_mapping = idam
	for a in src["item_attributes"]:
		doc.append("item_attributes", {"attribute": a["attribute"]})
	processes = [
		{"process_name": src.get("cutting_process") or "Cutting", "in_stage": None, "out_stage": "Cut"},
		{"process_name": "Yolk Fusing", "in_stage": "Cut", "out_stage": "Cut"},
		{"process_name": src.get("stiching_process") or "Stitching", "in_stage": "Cut", "out_stage": "Piece"},
		{"process_name": "Ironing and Packing", "in_stage": "Piece", "out_stage": "Pack"},
	]
	for p in processes:
		_ensure_process_master(p["process_name"])
		doc.append("ipd_processes", p)
	# Item BOM rows are locked after submit, so add them now (Mode A flat ratio).
	for r in src["item_bom"]:
		bom_item_label = r.get("item_name1") or r["item"]
		bom_item_id = id_map.get(bom_item_label)
		if not bom_item_id:
			print(f"  [skip-bom] '{bom_item_label}' not seeded")
			continue
		doc.append("item_bom", {
			"item": bom_item_id,
			"qty_of_product": r["qty_of_product"],
			"qty_of_bom_item": r["qty_of_bom_item"],
			"uom": _ensure_uom(r["uom"]),
			"process_name": r["process_name"],
			"dependent_attribute_value": r.get("dependent_attribute_value"),
			"based_on_attribute_mapping": 0,
			"attribute_mapping": None,
		})
	doc.insert(ignore_permissions=True)
	doc.submit()
	print(f"  ipd: {doc.name}  bom_rows={len(doc.item_bom)}")
	return doc.name


def _ensure_process_master(name):
	if frappe.db.exists("Process", name):
		return
	pdoc = frappe.new_doc("Process")
	pdoc.process_name = name
	pdoc.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Cutting matrix
# ---------------------------------------------------------------------------

def _create_cutting_matrix(ipd_name, src, id_map, attr_values):
	"""Per (Panel, Colour, Size) group: input fabric kg → output 1 panel piece.
	Fabric weight per (Panel, Colour) comes from cutting_items_json.
	"""
	cij = src.get("cutting_items_json") or {}
	weights = {}  # (panel, colour) -> kg
	for entry in cij.get("items", []):
		if entry.get("Panel") and entry.get("Colour"):
			weights[(entry["Panel"], entry["Colour"])] = float(entry.get("Weight") or 0)

	parent_item_id = id_map[src["item"]]
	cloth = next((c for c in src["cloth_detail"] if c.get("cloth_type")), None)
	fabric_name1 = cloth.get("cloth_type") if cloth else None
	fabric_item_id = id_map.get(fabric_name1) if fabric_name1 else None
	if not fabric_item_id:
		frappe.throw(f"Cutting matrix: fabric item '{fabric_name1}' not seeded")

	# Sizes from the global attribute values map
	sizes = [s for s in attr_values.get("Size", []) if " cm" in s]  # only the t-shirt sizes
	colours = sorted({c for (_p, c) in weights})
	panels = sorted({p for (p, _c) in weights})

	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Cutting"
	m.input_item = fabric_item_id
	m.output_item = parent_item_id
	for a in ["Colour", "Dia"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Panel", "Colour", "Size"]:
		m.append("output_attributes", {"attribute": a})

	gidx = 0
	for panel in panels:
		for colour in colours:
			weight = weights.get((panel, colour), 0)
			if not weight:
				continue
			for size in sizes:
				gidx += 1
				_add_combo(m, gidx, "Input", 1, weight, "Kg", [("Colour", colour), ("Dia", "60 Dia")])
				_add_combo(m, gidx, "Output", 1, 1, "Pieces", [("Panel", panel), ("Colour", colour), ("Size", size)])
	m.insert(ignore_permissions=True)
	print(f"  cutting matrix: {m.name}  groups={gidx}")
	return m.name


# ---------------------------------------------------------------------------
# Yolk Fusing matrix
# ---------------------------------------------------------------------------

def _create_yolk_fusing_matrix(ipd_name, src, id_map, wos, attr_values):
	"""1 cut panel (Top Back) -> 1 fused cut panel (Top Back). Source data: which panels
	get fused is derivable from the Yolk Fusing WO's deliverables. For a 100%-replication
	fallback, we cover Top Back across all (Colour, Size) — that matches the dump. If
	different panels were fused in the source, adjust here.
	"""
	parent_item_id = id_map[src["item"]]
	wo = next((w for w in wos if w["process_name"] == "Yolk Fusing"), None)
	# Build set of (Panel, Colour, Size) actually delivered to the WO
	combos = set()
	if wo:
		for d in wo["deliverables"]:
			a = d["attrs"]
			panel, colour, size = a.get("Panel"), a.get("Colour"), a.get("Size")
			if panel and colour and size:
				combos.add((panel, colour, size))

	# Fallback: if WO had no data, use Top Back × all colours × all sizes
	if not combos:
		colours = [c for c in attr_values.get("Colour", [])]
		sizes = [s for s in attr_values.get("Size", []) if " cm" in s]
		for c in colours:
			for s in sizes:
				combos.add(("Top Back", c, s))

	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Yolk Fusing"
	m.input_item = parent_item_id
	m.output_item = parent_item_id
	for a in ["Panel", "Colour", "Size"]:
		m.append("input_attributes", {"attribute": a})
		m.append("output_attributes", {"attribute": a})

	gidx = 0
	for panel, colour, size in sorted(combos):
		gidx += 1
		_add_combo(m, gidx, "Input", 1, 1, "Pieces", [("Panel", panel), ("Colour", colour), ("Size", size)])
		_add_combo(m, gidx, "Output", 1, 1, "Pieces", [("Panel", panel), ("Colour", colour), ("Size", size)])
	m.insert(ignore_permissions=True)
	print(f"  yolk fusing matrix: {m.name}  groups={gidx}")
	return m.name


# ---------------------------------------------------------------------------
# Stitching matrix
# ---------------------------------------------------------------------------

def _create_stitching_matrix(ipd_name, src, id_map, attr_values):
	"""Per (Colour, Size) group: cut panels in (with stitching multipliers) → 1 piece."""
	parent_item_id = id_map[src["item"]]
	multipliers = {r["stiching_attribute_value"]: int(r["quantity"] or 1) for r in src["stiching_item_details"]}
	colours = [c for c in attr_values.get("Colour", [])]
	sizes = [s for s in attr_values.get("Size", []) if " cm" in s]

	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Stitching"
	m.input_item = parent_item_id
	m.output_item = parent_item_id
	for a in ["Panel", "Colour", "Size"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Colour", "Size"]:
		m.append("output_attributes", {"attribute": a})

	gidx = 0
	for colour in colours:
		for size in sizes:
			gidx += 1
			ci = 0
			for panel, mult in multipliers.items():
				ci += 1
				_add_combo(m, gidx, "Input", ci, mult, "Pieces", [("Panel", panel), ("Colour", colour), ("Size", size)])
			_add_combo(m, gidx, "Output", 1, 1, "Pieces", [("Colour", colour), ("Size", size)])
	m.insert(ignore_permissions=True)
	print(f"  stitching matrix: {m.name}  groups={gidx}")
	return m.name


# ---------------------------------------------------------------------------
# Ironing and Packing matrix
# ---------------------------------------------------------------------------

def _create_packing_matrix(ipd_name, src, id_map, attr_values):
	"""Per (Colour, Size): packing_combo pieces → 1 pack (Stage=Pack, Size only)."""
	parent_item_id = id_map[src["item"]]
	combo = int(src.get("packing_combo") or 5)
	colours = [c for c in attr_values.get("Colour", [])]
	sizes = [s for s in attr_values.get("Size", []) if " cm" in s]

	m = frappe.new_doc("IPD Process Matrix")
	m.ipd = ipd_name
	m.process_name = "Ironing and Packing"
	m.input_item = parent_item_id
	m.output_item = parent_item_id
	for a in ["Colour", "Size"]:
		m.append("input_attributes", {"attribute": a})
	for a in ["Size"]:
		m.append("output_attributes", {"attribute": a})

	gidx = 0
	for colour in colours:
		for size in sizes:
			gidx += 1
			_add_combo(m, gidx, "Input", 1, combo, "Pieces", [("Colour", colour), ("Size", size)])
			_add_combo(m, gidx, "Output", 1, 1, "Box", [("Size", size)])
	m.insert(ignore_permissions=True)
	print(f"  packing matrix: {m.name}  groups={gidx}")
	return m.name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_combo(matrix, group_index, side, combo_index, qty, uom, attr_pairs):
	matrix.append("combinations", {
		"group_index": group_index,
		"side": side,
		"combo_index": combo_index,
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


def _values_for_attr_on_item(item_id, attribute):
	idoc = frappe.get_doc("Item", item_id)
	for ar in idoc.attributes or []:
		if ar.attribute == attribute and ar.mapping:
			m = frappe.get_doc("Item Item Attribute Mapping", ar.mapping)
			return [v.attribute_value for v in m.values]
	return []
