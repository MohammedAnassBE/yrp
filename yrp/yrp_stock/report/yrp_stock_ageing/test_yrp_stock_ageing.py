"""Regression checks for the approved three-decimal FIFO ageing contract."""

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.stock.ageing import FIFOSlots
from yrp.yrp_stock.report.yrp_stock_ageing import stock_ageing_parity as parity
from yrp.yrp_stock.report.yrp_stock_ageing import yrp_stock_ageing as report
from yrp.yrp_stock.report.yrp_stock_balance import yrp_stock_balance as balance


def ledger(qty, day, voucher=None, **values):
	return frappe._dict(
		item="Fabric-Black",
		warehouse="Store",
		lot="L1",
		received_type="Accepted",
		qty=qty,
		qty_after_transaction=qty,
		posting_date=date.fromisoformat(day),
		voucher_type="YRP Stock Entry",
		voucher_no=voucher or day,
		**values,
	)


class TestStockAgeingFIFO(TestCase):
	def generate(self, entries, warehouse_wise=False):
		with patch("yrp.stock.ageing.get_dimension_fieldnames", return_value=["lot", "received_type"]):
			return FIFOSlots({"show_warehouse_wise_stock": warehouse_wise}, deepcopy(entries)).generate()

	def test_residual_dust_of_either_sign_is_removed_before_item_aggregation(self):
		for dust in [5.329070518200751e-15, -4.6407322429331543e-14, 0.00049, -0.00049]:
			with self.subTest(dust=dust):
				entries = [ledger(dust, "2025-08-13"), ledger(7.04, "2025-08-22")]
				entries[1].lot = "L2"
				queue = self.generate(entries)["Fabric-Black"]["fifo_queue"]
				self.assertEqual(queue, [[7.04, date(2025, 8, 22)]])

	def test_real_point_zero_zero_one_batch_is_kept(self):
		queue = self.generate([ledger(0.001, "2025-08-13"), ledger(7.04, "2025-08-22")])["Fabric-Black"][
			"fifo_queue"
		]
		self.assertEqual(queue, [[0.001, date(2025, 8, 13)], [7.04, date(2025, 8, 22)]])

	def test_half_ties_use_legacy_rounding_independently_of_system_settings(self):
		for setting in ("Banker's Rounding", "Banker's Rounding (legacy)", "Commercial Rounding"):
			for qty, expected in [(0.0005, 0.001), (-0.0005, 0), (0.0025, 0.003), (-0.0015, -0.001)]:
				with (
					self.subTest(setting=setting, qty=qty),
					patch.object(frappe, "get_system_settings", return_value=setting),
				):
					queue = self.generate([ledger(qty, "2025-08-13")])["Fabric-Black"]["fifo_queue"]
					self.assertEqual(queue, [[expected, date(2025, 8, 13)]] if expected else [])

	def test_partial_consumption_rounds_each_remaining_batch(self):
		entries = [ledger(0.3, "2025-08-13"), ledger(-0.1, "2025-08-14"), ledger(0.1236, "2025-08-22")]
		result = self.generate(entries)["Fabric-Black"]
		self.assertEqual(result["fifo_queue"], [[0.2, date(2025, 8, 13)], [0.124, date(2025, 8, 22)]])
		self.assertAlmostEqual(result["total_qty"], 0.3236)

	def test_partial_consumption_dust_is_dropped_and_real_negative_batch_is_kept(self):
		entries = [ledger(0.3, "2025-08-13"), ledger(-0.1, "2025-08-14"), ledger(-0.2, "2025-08-15")]
		self.assertEqual(self.generate(entries)["Fabric-Black"]["fifo_queue"], [])
		self.assertEqual(
			self.generate([ledger(-0.001, "2025-08-13")])["Fabric-Black"]["fifo_queue"],
			[[-0.001, date(2025, 8, 13)]],
		)

	def test_whole_batch_transfer_keeps_original_age_and_dimension_keys(self):
		entries = [
			ledger(3, "2025-08-13"),
			ledger(-3, "2025-08-22", "TRANSFER"),
			ledger(3, "2025-08-22", "TRANSFER"),
		]
		result = self.generate(entries, warehouse_wise=True)
		self.assertEqual(list(result), [("Fabric-Black", "Store", "L1", "Accepted")])
		self.assertEqual(
			result[("Fabric-Black", "Store", "L1", "Accepted")]["fifo_queue"], [[3, date(2025, 8, 13)]]
		)

	def test_negative_stock_and_reconciliation_keep_existing_balances(self):
		entries = [ledger(-2, "2025-08-13"), ledger(5, "2025-08-22")]
		reconcile = ledger(0, "2025-08-23")
		reconcile.update(voucher_type="YRP Stock Reconciliation", qty_after_transaction=6)
		result = self.generate([*entries, reconcile])["Fabric-Black"]
		self.assertEqual(result["fifo_queue"], [[3, date(2025, 8, 22)], [1, date(2025, 8, 23)]])
		self.assertEqual(result["total_qty"], 4)


class TestStockAgeingMetadata(TestCase):
	def test_standalone_item_metadata_and_empty_input(self):
		item = frappe._dict(name="Fabric", variant_of=None, item_group="Cloth", stock_uom="Kg", brand="")
		with patch.object(frappe, "get_all", return_value=[item]):
			self.assertEqual(
				report.get_item_metadata({"Fabric": {}}),
				{"Fabric": {"item_name": "Fabric", "item_group": "Cloth", "stock_uom": "Kg", "brand": ""}},
			)
		self.assertEqual(report.get_item_metadata({}), {})

	def test_parent_item_metadata_and_mrp_column_order(self):
		variant = frappe._dict(
			name="Fabric-Black", variant_of="Fabric", item_group="M_Cloth", stock_uom="Kg", brand=""
		)
		parent = frappe._dict(
			name="Fabric", variant_of=None, item_group="Cloth", stock_uom="Kg", brand="Essdee"
		)
		entries = [ledger(1.001, "2026-10-01")]
		queries = []

		def read_items(doctype, **kwargs):
			self.assertEqual(doctype, "Item")
			queries.append(kwargs["filters"]["name"])
			return [variant] if len(queries) == 1 else [parent]

		with (
			patch("yrp.stock.ageing.get_dimension_fieldnames", return_value=[]),
			patch("yrp.stock.ageing.FIFOSlots._FIFOSlots__get_stock_ledger_entries", return_value=entries),
			patch.object(report, "get_stock_dimensions", return_value=[]),
			patch.object(report, "get_dimension_fieldnames", return_value=[]),
			patch.object(frappe, "get_all", side_effect=read_items),
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)"),
		):
			columns, rows, *_ = report.execute({"to_date": "2026-10-17"})
		self.assertEqual(
			[(c["fieldname"], c["label"]) for c in columns],
			[
				("item", "Item"),
				("item_name", "Item Name"),
				("item_group", "Item Group"),
				("brand", "Brand"),
				("qty", "Available Qty"),
				("average_age", "Average Age"),
				("range1", "Age (0 - 30)"),
				("range2", "Age (31 - 60)"),
				("range3", "Age (61 - 90)"),
				("range4", "Age (91 - Above)"),
				("earliest", "Earliest"),
				("latest", "Latest"),
				("stock_uom", "UOM"),
			],
		)
		self.assertEqual(rows[0]["item_name"], "Fabric")
		self.assertEqual(rows[0]["item_group"], "Cloth")
		self.assertEqual(rows[0]["stock_uom"], "Kg")
		self.assertEqual(rows[0]["brand"], "Essdee")
		self.assertEqual(rows[0]["qty"], 1.001)
		self.assertEqual(queries, [["in", ["Fabric-Black"]], ["in", ["Fabric"]]])

	def test_chart_orders_report_rows_by_descending_average_age_as_mrp(self):
		rows = [{"item": "New", "qty": 1, "average_age": 2}, {"item": "Old", "qty": 1, "average_age": 100}]
		report.get_chart_data(rows, {})
		self.assertEqual([r["item"] for r in rows], ["Old", "New"])


class TestStockAgeingParityGate(TestCase):
	def test_adjusted_rounding_cannot_hide_a_raw_mrp_difference(self):
		with TemporaryDirectory() as directory:
			output = Path(directory)
			for day in parity.DATES:
				mrp = {
					"filters": {},
					"columns": [],
					"rows": [{"earliest": 430}],
					"chart": None,
					"rounded_fifo_rows": [{"earliest": 421}],
					"rounded_fifo_chart": None,
				}
				erp = {"columns": [], "rows": [{"earliest": 421}], "chart": None}
				(output / f"{day}-mrp.json").write_text(json.dumps(mrp))
				(output / f"{day}-erp.json").write_text(json.dumps(erp))
			with (
				patch.object(parity.subprocess, "run"),
				patch.object(parity, "HISTORY", output / "absent.json"),
			):
				self.assertEqual(parity.run(output), 1)
				self.assertEqual(parity.run(output, adjusted_rounding=True), 0)


class TestStockBalanceAgeingCaller(TestCase):
	def test_balance_age_and_inward_dates_ignore_dust_but_keep_point_zero_zero_one(self):
		entries = [
			ledger(0.000000000000005, "2025-08-13"),
			ledger(0.001, "2025-08-19"),
			ledger(7.04, "2025-08-22"),
		]
		for entry in entries:
			entry.update(stock_value_difference=0, valuation_rate=0, stock_value=0)
		filters = {
			"from_date": "2025-01-01",
			"to_date": "2026-10-17",
			"show_stock_ageing_data": 1,
			"show_inward_date_split": 1,
		}
		with (
			patch("yrp.stock.ageing.get_dimension_fieldnames", return_value=["lot", "received_type"]),
			patch.object(balance, "get_dimension_fieldnames", return_value=["lot", "received_type"]),
			patch.object(balance, "get_stock_dimensions", return_value=[]),
			patch.object(balance, "get_items", return_value=["Fabric-Black"]),
			patch.object(balance, "get_stock_ledger_entries", return_value=entries),
			patch.object(balance, "get_item_details", return_value={"Fabric-Black": {"item_name": "Fabric"}}),
			patch.object(frappe.db, "get_default", return_value=3),
			patch.object(balance, "get_opening_vouchers", return_value={}),
		):
			rows = balance.execute(filters)[1]
		self.assertAlmostEqual(rows[0]["bal_qty"], 7.041)
		self.assertEqual(rows[0]["earliest_age"], 424)
		self.assertEqual(rows[0]["latest_age"], 421)
		self.assertEqual(
			rows[0]["inward_date_breakdown"],
			[{"date": "19-08-2025", "qty": 0.001}, {"date": "22-08-2025", "qty": 7.04}],
		)
