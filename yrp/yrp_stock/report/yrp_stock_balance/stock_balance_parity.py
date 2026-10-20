"""Read-only dimension filter parity against mrp3.site, with reproducible evidence.

Run with the F16 bench Python; each child uses its own bench environment.
MRP supports a scalar received_type, so its multi-type baseline is the union
of separate report executions. Neither source code nor either database is changed.
"""

import argparse
import importlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

BENCHES = Path("/home/anas/pilot/benches")
LOTS = ("C0925-63", "C0925-64", "F1225-38", "F1225-37", "C1025-81")
RECEIVED_TYPES = ("Accepted", "Adas", "Oil Mark")
QUANTITIES = ("opening_qty", "in_qty", "out_qty", "bal_qty")
VALUES = ("opening_val", "in_val", "out_val", "bal_val")


def cases():
	for lot in LOTS:
		for received_type in RECEIVED_TYPES:
			yield f"{lot}-{received_type}", [lot], [received_type], False
	for received_type in RECEIVED_TYPES:
		yield f"five-lots-{received_type}", list(LOTS), [received_type], False
	yield "five-lots-three-types", list(LOTS), list(RECEIVED_TYPES), False
	yield "five-lots-three-types-fifo", list(LOTS), list(RECEIVED_TYPES), True


def capture(side, output, float_precision=None, item_filters_only=False):
	import frappe

	bench = BENCHES / ("frappe-15" if side == "mrp" else "frappe-16")
	os.chdir(bench / "sites")
	frappe.init(site="mrp3.site" if side == "mrp" else "erp-migration-test.site")
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.db.sql("START TRANSACTION READ ONLY")
	module = importlib.import_module(
		"production_api.mrp_stock.report.stock_balance.stock_balance"
		if side == "mrp"
		else "yrp.yrp_stock.report.yrp_stock_balance.yrp_stock_balance"
	)
	filename = f"{side}-precision{float_precision}.json" if float_precision is not None else f"{side}.json"
	results = json.loads((output / filename).read_text()) if item_filters_only else {}
	get_default = frappe.db.get_default

	def report_default(key, *args, **kwargs):
		if key == "float_precision" and float_precision is not None:
			return float_precision
		return get_default(key, *args, **kwargs)

	try:
		with patch.object(frappe.db, "get_default", side_effect=report_default):
			if not item_filters_only:
				_capture_cases(module, side, results)
			_capture_item_cases(module, side, results, output)
		(output / filename).write_text(frappe.as_json(results, indent=2) + "\n")
	finally:
		frappe.db.rollback()
		frappe.destroy()


def _capture_cases(module, side, results):
	import frappe

	for name, lots, received_types, fifo in cases():
		base = {
			"from_date": "2026-10-01",
			"to_date": "2026-10-17",
			"lot": lots,
			"remove_zero_balance_item": int(fifo),
			"show_stock_ageing_data": int(fifo),
			"show_inward_date_split": int(fifo),
		}
		rows = []
		requests = [[value] for value in received_types] if side == "mrp" else [received_types]
		for values in requests:
			filters = {**base, "received_type": values[0] if side == "mrp" else values}
			rows.extend(module.execute(frappe._dict(filters))[1])
		unexpected = [
			row
			for row in rows
			if row.get("lot") not in lots or row.get("received_type") not in received_types
		]
		assert not unexpected, f"{side}: filters leaked rows in {name}"
		results[name] = {"filters": {**base, "received_type": received_types}, "rows": rows}
		print(f"{side}: {name}: {len(rows)} rows", flush=True)


def _capture_item_cases(module, side, results, output):
	"""Use identical physical item codes on both sites; MRP multi-parent is a scalar union.

	The legacy MRP detail query compares parent_item with equality, so passing an
	array there is unreliable. A union of individual parents is its baseline.
	"""
	import frappe

	manifest_path = output / "item-filter-fixtures.json"
	if side == "mrp":
		manifest = {}
		for lot in LOTS:
			rows = results[f"{lot}-Accepted"]["rows"]
			# MRP models standalone items as same-code Item Variants. Select genuine
			# variants here so all five lots also exercise the new YRP variant picker.
			active = [row for row in rows if row.get("bal_qty") and row["item"] != row["item_name"]]
			parents = sorted({row["item_name"] for row in active})[:2]
			variants = sorted({row["item"] for row in active if row["item_name"] in parents})[:2]
			assert parents and variants, f"No item filter fixtures for {lot}"
			manifest[lot] = {"parents": parents, "variants": variants}
		manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
	else:
		manifest = json.loads(manifest_path.read_text())
		assert all(
			frappe.db.get_value("Item", item, "variant_of")
			for fixture in manifest.values()
			for item in fixture["variants"]
		), "Variant parity fixtures must be selectable variants in YRP"
	for lot, fixture in manifest.items():
		parents, variants = fixture["parents"], fixture["variants"]
		for suffix, selected_parents, selected_variants, fifo in (
			("item", parents[:1], [], False),
			("items", parents, [], False),
			("variant", [], variants[:1], False),
			("variants", [], variants, False),
			("items-variants", parents, variants, False),
			("items-variants-fifo", parents, variants, True),
		):
			name = f"{lot}-{suffix}"
			base = {
				"from_date": "2026-10-01",
				"to_date": "2026-10-17",
				"lot": [lot],
				"received_type": "Accepted" if side == "mrp" else ["Accepted"],
				"remove_zero_balance_item": int(fifo),
				"show_stock_ageing_data": int(fifo),
				"show_inward_date_split": int(fifo),
				"parent_item": selected_parents,
				"item": selected_variants,
			}
			requests = (
				[{**base, "parent_item": parent} for parent in selected_parents]
				if side == "mrp" and selected_parents
				else [base]
			)
			rows = [row for filters in requests for row in module.execute(frappe._dict(filters))[1]]
			assert rows, f"{side}: {name} unexpectedly empty"
			assert all(row.get("lot") == lot and row.get("received_type") == "Accepted" for row in rows), name
			if selected_variants:
				assert all(row["item"] in selected_variants for row in rows), name
			if selected_parents:
				assert all(
					row["item_name" if side == "mrp" else "parent_item"] in selected_parents for row in rows
				), name
			results[name] = {"filters": {**base, "received_type": ["Accepted"]}, "rows": rows}
			print(f"{side}: {name}: {len(rows)} rows", flush=True)


def quantities(rows, lots, received_types):
	groups = {
		f"{lot} | {received_type}": dict.fromkeys(QUANTITIES, Decimal(0))
		for lot in lots
		for received_type in received_types
	}
	for row in rows:
		key = f"{row['lot']} | {row['received_type']}"
		group = groups.setdefault(key, dict.fromkeys(QUANTITIES, Decimal(0)))
		for field in QUANTITIES:
			group[field] += Decimal(str(row.get(field) or 0))
	return {
		key: {field: str(value.quantize(Decimal("0.001"))) for field, value in group.items()}
		for key, group in sorted(groups.items())
	}


def compare_rows(mrp_rows, yrp_rows):
	"""Compare physical stock groups, so offsetting per-item differences cannot cancel."""
	fields = (*QUANTITIES, *VALUES)

	def grouped(rows):
		groups = {}
		for row in rows:
			key = tuple(row.get(field) or "" for field in ("item", "warehouse", "lot", "received_type"))
			group = groups.setdefault(key, dict.fromkeys(fields, Decimal(0)))
			for field in fields:
				group[field] += Decimal(str(row.get(field) or 0))
		return groups

	mrp, yrp = grouped(mrp_rows), grouped(yrp_rows)
	keys = mrp.keys() | yrp.keys()
	max_difference, differences = Decimal(0), []
	for key in sorted(keys):
		deltas = {
			field: abs(mrp.get(key, {}).get(field, Decimal(0)) - yrp.get(key, {}).get(field, Decimal(0)))
			for field in fields
		}
		max_difference = max(max_difference, *deltas.values())
		if any(delta > Decimal("0.01") for delta in deltas.values()):
			differences.append(
				{"key": key, "deltas": {field: str(delta) for field, delta in deltas.items() if delta}}
			)
	return {
		"compared_rows": len(keys),
		"matching_at_0_01": len(keys) - len(differences),
		"max_difference": str(max_difference),
		"sample_differences": differences[:10],
	}


def compare(output, float_precision=None):
	yrp_file = f"yrp-precision{float_precision}.json" if float_precision is not None else "yrp.json"
	data = {
		side: json.loads((output / filename).read_text())
		for side, filename in (("mrp", "mrp.json"), ("yrp", yrp_file))
	}
	comparison = {}
	for name, case in data["mrp"].items():
		lots, received_types = case["filters"]["lot"], case["filters"]["received_type"]
		mrp = quantities(data["mrp"][name]["rows"], lots, received_types)
		yrp = quantities(data["yrp"][name]["rows"], lots, received_types)
		row_metrics = compare_rows(data["mrp"][name]["rows"], data["yrp"][name]["rows"])
		comparison[name] = {
			"match": mrp == yrp and row_metrics["matching_at_0_01"] == row_metrics["compared_rows"],
			"mrp": mrp,
			"yrp": yrp,
			"row_counts": {side: len(data[side][name]["rows"]) for side in data},
			"row_metrics": row_metrics,
		}
	filename = (
		f"comparison-precision{float_precision}.json" if float_precision is not None else "comparison.json"
	)
	(output / filename).write_text(json.dumps(comparison, indent=2) + "\n")
	print(
		json.dumps(
			{
				name: {"match": value["match"], "row_counts": value["row_counts"]}
				for name, value in comparison.items()
			},
			indent=2,
		)
	)
	return all(case["match"] for case in comparison.values())


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--side", choices=("mrp", "yrp"))
	parser.add_argument("--compare-only", action="store_true")
	parser.add_argument(
		"--item-filters-only",
		action="store_true",
		help="Refresh item cases using existing dimension captures",
	)
	parser.add_argument(
		"--float-precision",
		type=int,
		help="YRP-only in-memory rounding diagnostic; never changes site settings",
	)
	parser.add_argument(
		"--output", type=Path, default=Path("/home/anas/report-build/port/parity/stock-balance-dimensions")
	)
	args = parser.parse_args()
	args.output.mkdir(parents=True, exist_ok=True)
	if args.float_precision is not None and not (args.compare_only or args.side == "yrp"):
		parser.error("--float-precision requires --side yrp or --compare-only")
	if args.compare_only:
		raise SystemExit(0 if compare(args.output, args.float_precision) else 1)
	if args.side:
		if args.side == "mrp" and args.float_precision is not None:
			parser.error("MRP capture always uses its original precision")
		capture(args.side, args.output, args.float_precision, args.item_filters_only)
		return
	for side in ("mrp", "yrp"):
		bench = BENCHES / ("frappe-15" if side == "mrp" else "frappe-16")
		subprocess.run(
			[
				str(bench / "env/bin/python"),
				str(Path(__file__).resolve()),
				"--side",
				side,
				"--output",
				str(args.output),
				*(["--item-filters-only"] if args.item_filters_only else []),
			],
			check=True,
			cwd=bench,
		)
	raise SystemExit(0 if compare(args.output) else 1)


if __name__ == "__main__":
	main()
