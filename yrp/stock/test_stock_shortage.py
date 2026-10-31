import json
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, get_datetime, nowdate

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.shortage import StockShortageReport, get_stock_shortage
from yrp.stock.stock_ledger import NegativeStockError
from yrp.stock.utils import get_stock_balance


class TestStockShortage(UnitTestCase):
	def test_float_drift_within_precision_is_not_a_shortage(self):
		self.assertEqual(get_stock_shortage(16.949999, 16.95, 3), 0)
		self.assertEqual(get_stock_shortage(21.9 - 4.95, 16.95, 3), 0)

	def test_real_shortage_is_rounded_to_precision(self):
		self.assertEqual(get_stock_shortage(16.9, 16.95, 3), 0.05)

	def test_rounding_boundaries_compare_rounded_operands(self):
		self.assertEqual(get_stock_shortage(1.00051, 1.00149, 3), 0)
		self.assertEqual(get_stock_shortage(1.0004, 1.0006, 3), 0.001)

	def test_report_lists_every_shortage_once(self):
		report = StockShortageReport(precision=3)
		self.assertTrue(report.add("40's Fabric <Navy>", "S-0163", 16.9, 16.95, " (Lot F1)"))
		self.assertFalse(report.add("Rib", "S-0163", 16.949999, 16.95))
		self.assertTrue(report.add("Collar", "S-0164", 0, 2))
		with self.assertRaises(NegativeStockError) as raised:
			report.throw()
		message = str(raised.exception)
		self.assertIn("Insufficient stock for 2 items", message)
		self.assertIn(
			"40&apos;s Fabric &lt;Navy&gt; (Lot F1) at S-0163: balance 16.9, requested 16.95, short 0.05",
			message,
		)
		self.assertIn("Collar at S-0164: balance 0.0, requested 2.0, short 2.0", message)
		self.assertNotIn("Rib", message)

	def test_empty_report_does_not_throw(self):
		StockShortageReport(precision=3).throw()


class TestStockShortageLedger(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.uom = "_Test Shortage Unit"
		if not frappe.db.exists("UOM", cls.uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": cls.uom, "enabled": 1}).insert(
				ignore_permissions=True
			)
		suffix = frappe.generate_hash(length=8)
		cls.item_group = (
			frappe.get_doc(
				{"doctype": "Item Group", "item_group_name": f"_Test Shortage {suffix}", "is_group": 0}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.items = [cls._make_item(f"_Test Shortage {label} {suffix}") for label in ("Fabric", "Rib")]
		cls.dimensions = cls._dimension_values()

	@classmethod
	def _make_item(cls, item_code):
		values = {
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": cls.item_group,
			"stock_uom": cls.uom,
			"is_stock_item": 1,
		}
		if frappe.get_meta("Item").has_field("gst_hsn_code") and frappe.db.exists("GST HSN Code", "999900"):
			values["gst_hsn_code"] = "999900"
		return frappe.get_doc(values).insert(ignore_permissions=True).name

	@staticmethod
	def _dimension_values():
		values = {}
		for dimension in get_stock_dimensions():
			fieldname = dimension["fieldname"]
			value = None
			if fieldname == "received_type":
				value = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
			value = value or frappe.db.get_value(dimension["dimension_doctype"], {}, "name")
			if value:
				values[fieldname] = value
		return values

	def _warehouse(self):
		name = f"_Test Shortage WH {frappe.generate_hash(length=8)}"
		return (
			frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name})
			.insert(ignore_permissions=True)
			.name
		)

	def _stock_entry(self, purpose, warehouse, quantities, posting=None):
		rows = [
			{
				"item": item,
				"qty": qty,
				"rate": 10,
				"uom": self.uom,
				"row_index": 0,
				"table_index": 0,
				**self.dimensions,
			}
			for item, qty in quantities.items()
		]
		warehouse_field = "to_warehouse" if purpose == "Material Receipt" else "from_warehouse"
		values = {
			"doctype": "YRP Stock Entry",
			"purpose": purpose,
			"posting_date": nowdate(),
			warehouse_field: warehouse,
			"items": rows,
		}
		if posting:
			values.update(edit_posting_date_and_time=1, posting_date=posting[0], posting_time=posting[1])
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=True)
		return doc

	def _move(self, purpose, warehouse, qty, posting=None):
		self._stock_entry(purpose, warehouse, {self.items[0]: qty}, posting).submit()

	def _ledger_rows(self, warehouse):
		return frappe.get_all(
			"YRP Stock Ledger Entry",
			filters={"item": self.items[0], "warehouse": warehouse, "is_cancelled": 0},
			fields=["qty", "qty_after_transaction", "stock_queue", "stock_value"],
			order_by="posting_datetime, creation, name",
		)

	def _assert_ledger_consistent(self, warehouse, expected_qty):
		"""Each SLE's queue matches its balance and value, balances never go negative, and qty sums."""
		rows = self._ledger_rows(warehouse)
		running = 0.0
		for row in rows:
			queue = json.loads(row.stock_queue or "[]")
			running += flt(row.qty)
			self.assertAlmostEqual(flt(row.qty_after_transaction), running, 9)
			self.assertGreaterEqual(flt(row.qty_after_transaction), 0)
			self.assertAlmostEqual(sum(flt(qty) for qty, _rate in queue), flt(row.qty_after_transaction), 9)
			self.assertAlmostEqual(sum(flt(qty) * flt(rate) for qty, rate in queue), flt(row.stock_value), 6)
		self.assertEqual(flt(rows[-1].qty_after_transaction), expected_qty)
		return rows

	def _for_each_valuation_method(self, scenario):
		original = frappe.db.get_single_value("YRP Stock Settings", "default_valuation_method")
		try:
			for method in ("FIFO", "Moving Average"):
				with self.subTest(method=method):
					frappe.db.set_single_value("YRP Stock Settings", "default_valuation_method", method)
					scenario(self._warehouse())
		finally:
			frappe.db.set_single_value("YRP Stock Settings", "default_valuation_method", original)

	def _reserve(self, warehouse, qty):
		reservation = frappe.get_doc(
			{
				"doctype": "YRP Stock Reservation Entry",
				"item_code": self.items[0],
				"warehouse": warehouse,
				"reserved_qty": qty,
				"available_qty": 9999,
				"voucher_type": "YRP Stock Update",
				"voucher_no": f"_Test Shortage Owner {frappe.generate_hash(length=8)}",
				**self.dimensions,
			}
		)
		reservation.flags.ignore_links = True
		reservation.insert(ignore_permissions=True)
		reservation.flags.ignore_links = True
		reservation.submit()
		return reservation

	def test_drifted_full_issue_clamps_to_the_exact_balance(self):
		def scenario(warehouse):
			self._move("Material Receipt", warehouse, 16.949999)
			self._move("Material Issue", warehouse, 16.95)
			rows = self._assert_ledger_consistent(warehouse, 0)
			self.assertEqual(flt(rows[-1].qty), -16.949999)
			self.assertEqual(sum(flt(qty) for qty, _rate in json.loads(rows[-1].stock_queue)), 0)
			self.assertEqual(flt(rows[-1].stock_value), 0)
			self.assertEqual(get_stock_balance(self.items[0], warehouse, **self.dimensions), 0)

		self._for_each_valuation_method(scenario)

	def test_many_tiny_overdraws_keep_qty_and_queue_equal(self):
		from yrp.stock import stock_ledger

		create_sle_document = stock_ledger._create_sle_document

		def create_unlinked_sle(args):
			# One draft voucher per posted row would dominate the run time; the ledger only needs distinct names.
			with patch.object(frappe.model.document.Document, "_validate_links", lambda doc: None):
				return create_sle_document(args)

		def scenario(warehouse):
			moment = get_datetime(f"{add_days(nowdate(), -1)} 00:00:00")

			def post(qty, offset):
				at = moment + timedelta(seconds=offset)
				entry = {
					"item": self.items[0],
					"warehouse": warehouse,
					"uom": self.uom,
					"voucher_type": "YRP Stock Entry",
					"voucher_no": f"_Test Shortage Cycle {warehouse} {offset}",
					"posting_date": at.date(),
					"posting_time": at.time(),
					"qty": qty,
					"rate": 10,
					**self.dimensions,
				}
				stock_ledger.make_sl_entries([entry], force_inline=True)

			post(1, 0)
			for cycle in range(1000):
				post(-1.00000005, 2 * cycle + 1)
				post(1, 2 * cycle + 2)
			self._assert_ledger_consistent(warehouse, 1)

		with patch.object(stock_ledger, "_create_sle_document", side_effect=create_unlinked_sle):
			self._for_each_valuation_method(scenario)

	def test_positive_remainder_below_precision_is_preserved(self):
		def scenario(warehouse):
			self._move("Material Receipt", warehouse, 1.0004)
			self._move("Material Issue", warehouse, 1)
			self._assert_ledger_consistent(warehouse, 0.0004)

		self._for_each_valuation_method(scenario)

	def test_preflight_and_ledger_agree_at_rounding_boundaries(self):
		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 1.00051)
		self._move("Material Issue", warehouse, 1.00149)
		self._assert_ledger_consistent(warehouse, 0)

		short = self._warehouse()
		self._move("Material Receipt", short, 1.0004)
		with self.assertRaisesRegex(NegativeStockError, "balance 1.0, requested 1.001, short 0.001"):
			self._move("Material Issue", short, 1.0006)

	def test_backdated_issue_replays_future_rows_with_the_same_rule(self):
		yesterday = add_days(nowdate(), -1)
		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 5, (yesterday, "01:00:00"))
		self._move("Material Issue", warehouse, 5, (yesterday, "03:00:00"))
		self._move("Material Issue", warehouse, 0.0004, (yesterday, "02:00:00"))
		rows = self._assert_ledger_consistent(warehouse, 0)
		self.assertEqual(flt(rows[-1].qty), -4.9996)
		with self.assertRaisesRegex(NegativeStockError, "balance 4.0, requested 5.0"):
			self._move("Material Issue", warehouse, 1, (yesterday, "02:30:00"))

	def test_cancelling_a_clamped_issue_restores_the_exact_balance(self):
		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 16.949999)
		issue = self._stock_entry("Material Issue", warehouse, {self.items[0]: 16.95})
		issue.submit()
		issue.cancel()
		self._assert_ledger_consistent(warehouse, 16.949999)
		self.assertEqual(get_stock_balance(self.items[0], warehouse, **self.dimensions), 16.949999)

	def test_drifted_issue_leaves_no_balance_below_the_closing_tolerance(self):
		from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
			QTY_TOLERANCE,
		)

		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 1)
		self._move("Material Issue", warehouse, 1.0004)
		rows = self._assert_ledger_consistent(warehouse, 0)
		self.assertFalse([row for row in rows if flt(row.qty_after_transaction) < -QTY_TOLERANCE])

	def test_reservation_can_be_released_after_a_drifted_issue(self):
		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 16.949999)
		reservation = self._reserve(warehouse, 0.0001)
		self._move("Material Issue", warehouse, 16.95)
		reservation.cancel()
		bin_name = frappe.db.get_value("YRP Bin", {"item_code": self.items[0], "warehouse": warehouse})
		self.assertEqual(flt(frappe.db.get_value("YRP Bin", bin_name, "actual_qty")), 0)
		self.assertEqual(flt(frappe.db.get_value("YRP Bin", bin_name, "reserved_qty")), 0)

	def test_reservation_rule_agrees_between_preflight_and_ledger(self):
		warehouse = self._warehouse()
		self._move("Material Receipt", warehouse, 2.00051)
		self._reserve(warehouse, 1.00051)
		update = frappe.get_doc(
			{
				"doctype": "YRP Stock Update",
				"warehouse": warehouse,
				"update_type": "Reduce",
				"stock_update_details": [
					{
						"item_variant": self.items[0],
						"update_diff_qty": 1.00049,
						"uom": self.uom,
						**self.dimensions,
					}
				],
			}
		)
		with self.assertRaisesRegex(NegativeStockError, "after reservation 1.001.*short 0.001"):
			update.validate()
		with self.assertRaisesRegex(NegativeStockError, "reserved quantity"):
			self._move("Material Issue", warehouse, 1.00049)

	def test_every_short_item_is_reported_together(self):
		warehouse = self._warehouse()
		fabric, rib = self.items
		self._stock_entry("Material Receipt", warehouse, {fabric: 5, rib: 1}).submit()
		issue = self._stock_entry("Material Issue", warehouse, {fabric: 7, rib: 3})
		with self.assertRaises(NegativeStockError) as raised:
			issue.submit()
		message = str(raised.exception)
		self.assertIn("Insufficient stock for 2 items", message)
		self.assertIn(f"{frappe.utils.escape_html(fabric)}", message)
		self.assertIn("balance 5.0, requested 7.0, short 2.0", message)
		self.assertIn("balance 1.0, requested 3.0, short 2.0", message)

	def test_stock_update_reduce_sums_rows_per_bucket_and_lists_every_short_item(self):
		warehouse = self._warehouse()
		fabric, rib = self.items
		self._stock_entry("Material Receipt", warehouse, {fabric: 10, rib: 1}).submit()
		doc = frappe.get_doc(
			{
				"doctype": "YRP Stock Update",
				"warehouse": warehouse,
				"update_type": "Reduce",
				"stock_update_details": [
					{"item_variant": item, "update_diff_qty": qty, "uom": self.uom, **self.dimensions}
					for item, qty in ((fabric, 6), (fabric, 6), (rib, 3))
				],
			}
		)
		with self.assertRaises(NegativeStockError) as raised:
			doc.validate()
		message = str(raised.exception)
		self.assertIn("Insufficient stock for 2 items", message)
		self.assertIn("balance 10.0, requested 12.0, short 2.0", message)
		self.assertIn("balance 1.0, requested 3.0, short 2.0", message)
