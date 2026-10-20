"""Read-only Stock Ageing comparison against mrp3.site for both approved dates.

Run from either bench's Python; the parent invokes each site's own environment:
  env/bin/python apps/yrp/yrp/yrp_stock/report/yrp_stock_ageing/stock_ageing_parity.py --phase after

Raw MRP output and MRP FIFO with the approved 3-decimal batch rule are saved
separately. The source database and source implementation are never changed.
The default exit status requires raw MRP equality. --adjusted-rounding selects
the approved FIFO rounding diagnostic explicitly; both results are always saved.
"""

import argparse
import importlib
import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

BENCHES = Path("/home/anas/pilot/benches")
DEFAULT_OUTPUT = Path("/home/anas/report-build/port/parity/worker5-stock-ageing")
HISTORY = Path("/home/anas/report-compare/evidence3_shard1_ageing.json")
DATES = ("2026-10-17", "2026-10-10")
ITEM = "Thermal Fabric-34 Dia-Black"


def capture(side, output):
	import frappe
	from frappe.utils import flt

	bench = BENCHES / ("frappe-15" if side == "mrp" else "frappe-16")
	os.chdir(bench / "sites")
	frappe.init(site="mrp3.site" if side == "mrp" else "erp-migration-test.site")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.db.sql("START TRANSACTION READ ONLY")
	module = importlib.import_module(
		"production_api.mrp_stock.report.stock_ageing.stock_ageing"
		if side == "mrp"
		else "yrp.yrp_stock.report.yrp_stock_ageing.yrp_stock_ageing"
	)
	try:
		for day in DATES:
			filters = frappe._dict(
				to_date=day, item=ITEM, range1=30, range2=60, range3=90, show_warehouse_wise_stock=0
			)
			result = module.execute(filters)
			slots = module.FIFOSlots(filters).generate()
			payload = {
				"filters": filters,
				"columns": result[0],
				"rows": result[1],
				"chart": result[3],
				"fifo": slots,
			}
			if side == "mrp":
				rounded = deepcopy(slots)
				for value in rounded.values():
					value["fifo_queue"] = [
						[flt(qty, 3), posted] for qty, posted in value["fifo_queue"] if flt(qty, 3)
					]
				payload["rounded_fifo_rows"] = module.format_report_data(filters, rounded, day)
				payload["rounded_fifo_chart"] = module.get_chart_data(payload["rounded_fifo_rows"], filters)
			(output / f"{day}-{side}.json").write_text(frappe.as_json(payload, indent=2) + "\n")
		if HISTORY.exists():
			capture_archived_history(side, output, module)
	finally:
		frappe.db.rollback()
		frappe.destroy()


def capture_archived_history(side, output, module):
	"""Replay the original comparison snapshot without writing either site."""
	import frappe
	from frappe.utils import flt, getdate

	evidence = json.loads(HISTORY.read_text())
	parent = evidence["master"]["mrp"]["parent"]["message"]
	for day in DATES:
		filters = frappe._dict(
			to_date=day, item=ITEM, range1=30, range2=60, range3=90, show_warehouse_wise_stock=0
		)
		entries = []
		for value in evidence["history"]["mrp" if side == "mrp" else "staging"]["message"]:
			if value["posting_date"] > day or value["is_cancelled"]:
				continue
			row = frappe._dict(deepcopy(value))
			row.posting_date = getdate(row.posting_date)
			if side == "mrp":
				row.update(
					name=row.item,
					item_name=parent["name"],
					item_group=parent["item_group"],
					stock_uom=parent["default_unit_of_measure"],
					brand=parent.get("brand"),
				)
			entries.append(row)
		slots = module.FIFOSlots(filters, entries).generate()
		args = [] if side == "mrp" else [module.get_dimension_fieldnames()]
		rows = module.format_report_data(filters, slots, day, *args)
		payload = {"filters": filters, "rows": rows, "fifo": slots}
		if side == "mrp":
			rounded = deepcopy(slots)
			for value in rounded.values():
				value["fifo_queue"] = [
					[flt(qty, 3), posted] for qty, posted in value["fifo_queue"] if flt(qty, 3)
				]
			payload["rounded_fifo_rows"] = module.format_report_data(filters, rounded, day)
		(output / f"archived-{day}-{side}.json").write_text(frappe.as_json(payload, indent=2) + "\n")


def row_differences(source, target):
	differences = []
	if len(source) != len(target):
		differences.append({"row_counts": [len(source), len(target)]})
	for index, (left, right) in enumerate(zip(source, target, strict=False)):
		# The pre-fix ERP report called MRP's fourth age bucket above_range3.
		right = {("range4" if key == "above_range3" else key): value for key, value in right.items()}
		for field in sorted(set(left) | set(right)):
			a, b = left.get(field), right.get(field)
			if isinstance(a, (int, float)) and isinstance(b, (int, float)):
				equal = abs(a - b) < 0.0005
			else:
				equal = a == b
			if not equal:
				differences.append({"row": index, "field": field, "mrp": a, "erp": b})
	return differences


def run(output, adjusted_rounding=False):
	output.mkdir(parents=True, exist_ok=True)
	for side, version in [("mrp", "frappe-15"), ("erp", "frappe-16")]:
		subprocess.run(
			[
				str(BENCHES / version / "env/bin/python"),
				str(Path(__file__).resolve()),
				"--side",
				side,
				"--output",
				str(output),
			],
			check=True,
		)
	checks = []
	for day in DATES:
		mrp = json.loads((output / f"{day}-mrp.json").read_text())
		erp = json.loads((output / f"{day}-erp.json").read_text())
		columns_equal = mrp["columns"] == erp["columns"]
		raw_diffs = row_differences(mrp["rows"], erp["rows"])
		rounded_diffs = row_differences(mrp["rounded_fifo_rows"], erp["rows"])
		checks.append(
			{
				"date": day,
				"filters": mrp["filters"],
				"mrp_rows": len(mrp["rows"]),
				"erp_rows": len(erp["rows"]),
				"columns_equal": columns_equal,
				"raw_differences": raw_diffs,
				"rounded_mrp_differences": rounded_diffs,
				"raw_chart_equal": mrp["chart"] == erp["chart"],
				"rounded_chart_equal": mrp["rounded_fifo_chart"] == erp["chart"],
				"raw_equal": columns_equal and not raw_diffs and mrp["chart"] == erp["chart"],
				"approved_rounding_equal": columns_equal
				and not rounded_diffs
				and mrp["rounded_fifo_chart"] == erp["chart"],
			}
		)
		if HISTORY.exists():
			old_mrp = json.loads((output / f"archived-{day}-mrp.json").read_text())
			old_erp = json.loads((output / f"archived-{day}-erp.json").read_text())
			checks[-1]["archived_replay"] = {
				"raw_mrp_earliest": old_mrp["rows"][0]["earliest"],
				"rounded_mrp_earliest": old_mrp["rounded_fifo_rows"][0]["earliest"],
				"erp_earliest": old_erp["rows"][0]["earliest"],
				"raw_differences": row_differences(old_mrp["rows"], old_erp["rows"]),
				"rounded_mrp_differences": row_differences(old_mrp["rounded_fifo_rows"], old_erp["rows"]),
			}
	(output / "summary.json").write_text(json.dumps(checks, indent=2) + "\n")
	print(json.dumps(checks, indent=2))
	if not adjusted_rounding:
		return 0 if all(check["raw_equal"] for check in checks) else 1
	return (
		0
		if all(
			check["approved_rounding_equal"]
			and not check.get("archived_replay", {}).get("rounded_mrp_differences")
			for check in checks
		)
		else 1
	)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--side", choices=["mrp", "erp"])
	parser.add_argument("--phase", default="after")
	parser.add_argument("--output", type=Path)
	parser.add_argument(
		"--adjusted-rounding",
		action="store_true",
		help="Gate on adjusted FIFO output instead of raw MRP parity",
	)
	args = parser.parse_args()
	output = (args.output or DEFAULT_OUTPUT / args.phase).resolve()
	if args.side:
		capture(args.side, output)
	else:
		raise SystemExit(run(output, adjusted_rounding=args.adjusted_rounding))
