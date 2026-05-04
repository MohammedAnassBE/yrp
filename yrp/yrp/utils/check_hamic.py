"""Spot-check the Hamic IPD V2 build by comparing engine output against source WOs.

Run: bench --site yrp2.site execute yrp.yrp.utils.check_hamic.run
"""

import frappe

from yrp.yrp.utils.ipd_engine import get_process_io


def run():
	ipd_name = "IPD-Item-00021-1"
	parent_item = frappe.db.get_value("Item Production Detail", ipd_name, "item")
	print(f"IPD: {ipd_name}  parent_item: {parent_item}")
	print(f"  bom_rows: {len(frappe.get_all('Item BOM', filters={'parent': ipd_name}))}")
	for p in frappe.get_all("IPD Process", filters={"parent": ipd_name}, fields=["process_name", "in_stage", "out_stage"], order_by="idx"):
		ms = frappe.get_all("IPD Process Matrix", filters={"ipd": ipd_name, "process_name": p.process_name}, pluck="name")
		groups_total = 0
		for m in ms:
			groups_total += len({c.group_index for c in frappe.get_doc("IPD Process Matrix", m).combinations})
		print(f"  {p.process_name}: stage {p.in_stage}->{p.out_stage}  matrices={len(ms)}  total_groups={groups_total}")

	# Source WO-2526-02527: Stitching, Steel Blue, qty 213 across 8 sizes — consumes panels, produces 8 pieces
	# Engine call: demand 8 piece outputs (Steel Blue × all sizes, 213 each), engine returns input cut panels.
	print("\n--- Stitching engine call: 213 pieces of Steel Blue × each size ---")
	sizes = ["45 cm", "50 cm", "55 cm", "60 cm", "65 cm", "70 cm", "75 cm", "80 cm"]
	demand = [{"attrs": {"Stage": "Piece", "Colour": "Steel Blue", "Size": s}, "qty": 213} for s in sizes]
	result = get_process_io(ipd_name, "Stitching", demand)
	# Sum inputs by Panel
	totals = {}
	for inp in result["inputs"]:
		key = inp["attrs"].get("Panel")
		totals[key] = totals.get(key, 0) + inp["qty"]
	print("  inputs by panel:", {k: round(v, 2) for k, v in sorted(totals.items())})
	print("  outputs total:", sum(o["qty"] for o in result["outputs"]))

	print("\n--- Cutting engine call: 213 cut panels per (panel,size) for Steel Blue ---")
	# Use stitching's input as cutting's demand — i.e. how much fabric for those panels
	demand = []
	for inp in result["inputs"]:
		demand.append({"attrs": inp["attrs"], "qty": inp["qty"]})
	cut_result = get_process_io(ipd_name, "Cutting", demand)
	# Sum fabric by Colour
	fabric_totals = {}
	for inp in cut_result["inputs"]:
		key = inp["attrs"].get("Colour")
		fabric_totals[key] = fabric_totals.get(key, 0) + inp["qty"]
	print("  fabric kg by colour:", {k: round(v, 3) for k, v in sorted(fabric_totals.items())})
	print("  source WO-2526-02213-2 Steel Blue: 265.824 Kg (compare)")
