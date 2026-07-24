"""Regression tests for Moving Average stock-entry safety rails."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from yrp.stock.dimensions import get_stock_dimensions


def _stock_item_variant():
	rows = frappe.db.sql(
		"""
		SELECT iv.name, COALESCE(i.default_unit_of_measure, 'Piece')
		FROM `tabItem Variant` iv
		INNER JOIN `tabItem` i ON i.name = iv.item
		WHERE i.is_stock_item = 1
		ORDER BY iv.creation
		LIMIT 1
		"""
	)
	if not rows:
		frappe.throw("A stock Item Variant is required for Moving Average tests.")
	return rows[0]


ITEM_VARIANT, ITEM_UOM = _stock_item_variant()


def _dimension_values():
	values = {}
	for dimension in get_stock_dimensions():
		fieldname = dimension["fieldname"]
		if fieldname == "received_type":
			value = frappe.db.get_single_value(
				"YRP Stock Settings", "default_received_type"
			)
		else:
			value = frappe.db.get_value(
				dimension["dimension_doctype"], {}, "name"
			)
		if dimension.get("mandatory") and not value:
			frappe.throw(
				f"A {dimension['label']} record is required for stock tests."
			)
		if value:
			values[fieldname] = value
	return values


DIMENSIONS = _dimension_values()


def _warehouse(suffix):
	name = f"_Test_MA_{suffix}"
	if not frappe.db.exists("Warehouse", name):
		frappe.get_doc({"doctype": "Warehouse", "name1": name}).insert(
			ignore_permissions=True
		)
	return name


def _stock_entry(
	purpose,
	qty,
	rate,
	*,
	from_warehouse=None,
	to_warehouse=None,
	posting_time="10:00:00.000000",
	skip_transit=0,
):
	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": purpose,
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"skip_transit": skip_transit,
			"edit_posting_date_and_time": 1,
			"posting_date": nowdate(),
			"posting_time": posting_time,
			"items": [
				{
					"item": ITEM_VARIANT,
					"qty": qty,
					"rate": rate,
					"uom": ITEM_UOM,
					"row_index": 0,
					"table_index": 0,
					**DIMENSIONS,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


class TestMovingAverageSafety(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_method = frappe.db.get_single_value(
			"YRP Stock Settings", "default_valuation_method"
		)
		frappe.db.set_single_value(
			"YRP Stock Settings", "default_valuation_method", "Moving Average"
		)

	def tearDown(self):
		frappe.db.set_single_value(
			"YRP Stock Settings",
			"default_valuation_method",
			self.original_method or "FIFO",
		)
		super().tearDown()

	def test_material_receipt_requires_positive_rate(self):
		warehouse = _warehouse("Receipt_Rate")
		with self.assertRaisesRegex(
			frappe.ValidationError, "Rate must be greater than zero"
		):
			_stock_entry(
				"Material Receipt",
				10,
				0,
				to_warehouse=warehouse,
			)

	def test_material_receipt_preserves_rate_from_item_editor(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		warehouse = _warehouse("Receipt_Editor_Rate")
		_stock_entry(
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
			posting_time="08:00:00.000000",
		).submit()
		template = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"items": [
					{
						"item": ITEM_VARIANT,
						"qty": 5,
						"rate": 37,
						"uom": ITEM_UOM,
						"row_index": 0,
						"table_index": 0,
						**DIMENSIONS,
					}
				],
			}
		)
		grouped = group_items_for_ui(template.items, "Stock Entry")
		receipt = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"to_warehouse": warehouse,
				"item_details": json.dumps(grouped),
			}
		)
		receipt.insert(ignore_permissions=True)
		self.assertAlmostEqual(flt(receipt.items[0].rate), 37.0)

	def test_transfer_carries_actual_source_value(self):
		source = _warehouse("Transfer_Source")
		target = _warehouse("Transfer_Target")
		_stock_entry(
			"Material Receipt",
			10,
			10,
			to_warehouse=source,
			posting_time="09:00:00.000000",
		).submit()
		_stock_entry(
			"Material Receipt",
			10,
			20,
			to_warehouse=source,
			posting_time="09:01:00.000000",
		).submit()

		transfer = _stock_entry(
			"Send to Warehouse",
			5,
			999,  # Must not become the destination valuation rate.
			from_warehouse=source,
			to_warehouse=target,
			posting_time="09:02:00.000000",
			skip_transit=1,
		)
		transfer.submit()

		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": transfer.name, "is_cancelled": 0},
			fields=[
				"qty",
				"rate",
				"outgoing_rate",
				"stock_value_difference",
			],
			order_by="creation asc",
		)
		self.assertEqual(len(sles), 2)
		outgoing, incoming = sles
		self.assertAlmostEqual(flt(outgoing.stock_value_difference), -75.0)
		self.assertAlmostEqual(flt(incoming.stock_value_difference), 75.0)
		self.assertAlmostEqual(flt(outgoing.outgoing_rate), 15.0)
		self.assertAlmostEqual(flt(incoming.rate), 15.0)
		self.assertAlmostEqual(
			sum(flt(row.stock_value_difference) for row in sles), 0.0
		)

	def test_same_timestamp_entries_are_processed_once(self):
		warehouse = _warehouse("Same_Timestamp")
		posting_time = "11:22:33.123456"
		first = _stock_entry(
			"Material Receipt",
			10,
			10,
			to_warehouse=warehouse,
			posting_time=posting_time,
		)
		first.submit()
		second = _stock_entry(
			"Material Receipt",
			10,
			20,
			to_warehouse=warehouse,
			posting_time=posting_time,
		)
		second.submit()

		latest = frappe.db.get_value(
			"Stock Ledger Entry",
			{"voucher_no": second.name, "is_cancelled": 0},
			[
				"posting_datetime",
				"qty_after_transaction",
				"stock_value",
				"valuation_rate",
				"stock_queue",
			],
			as_dict=True,
		)
		self.assertEqual(latest.posting_datetime.microsecond, 123456)
		self.assertAlmostEqual(flt(latest.qty_after_transaction), 20.0)
		self.assertAlmostEqual(flt(latest.stock_value), 300.0)
		self.assertAlmostEqual(flt(latest.valuation_rate), 15.0)
		queue = json.loads(latest.stock_queue)
		self.assertAlmostEqual(sum(flt(row[0]) for row in queue), 20.0)
		self.assertAlmostEqual(
			sum(flt(row[0]) * flt(row[1]) for row in queue), 300.0
		)
