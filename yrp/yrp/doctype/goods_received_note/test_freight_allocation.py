"""Tests for D-012 freight allocation on Goods Received Note.

Covers:
  - By Quantity allocation (single + multi-row)
  - By Value allocation (multi-row)
  - By Value -> By Quantity fallback when total_amount == 0 (Gap #11)
  - By Quantity with total_stock_qty == 0 blocks submit (Gap #12)
  - Negative freight_charges rejected
  - SLE valuation_rate reflects freight
  - Cancel reverses freight-inclusive SLEs cleanly
  - Zero freight (default) is a no-op
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
	_item_uom,
	_production_group_dimensions,
	_purchase_order,
	_supplier,
	_test_item_variant,
	_warehouse,
)


def _po_with_two_lines(warehouse, qty_a=4, qty_b=6, rate_a=10, rate_b=30):
	"""Build a submitted PO with two item rows (same item variant, distinct rates).

	Two-row tests need distinct amounts; rate_a != rate_b. We reuse the same item
	since PO mandates exact item match — uniqueness comes from the rate.
	"""
	item_variant = _test_item_variant()
	uom = _item_uom(item_variant)
	po = frappe.get_doc({
		"doctype": "Purchase Order",
		"supplier": _supplier(f"_T_Freight_Supplier_{frappe.generate_hash(length=6)}"),
		"delivery_warehouse": warehouse,
		**_production_group_dimensions(),
		"items": [
			{
				"item_variant": item_variant,
				"qty": qty_a,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"rate": rate_a,
				"table_index": 0,
				"row_index": 0,
			},
			{
				"item_variant": item_variant,
				"qty": qty_b,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"rate": rate_b,
				"table_index": 0,
				"row_index": 1,
			},
		],
	})
	po.insert(ignore_permissions=True)
	po.submit()
	return po


def _grn_from_po(po, freight=0, full=True):
	"""Build a draft GRN against the PO with optional freight_charges. Receives
	each PO line at PO qty when full=True."""
	rows = []
	for i, item in enumerate(po.items):
		rows.append({
			"item_variant": item.item_variant,
			"quantity": item.qty if full else flt(item.qty) / 2,
			"uom": item.uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": item.conversion_factor,
			"rate": item.rate,
			"ref_doctype": "Purchase Order Item",
			"ref_docname": item.name,
			"table_index": 0,
			"row_index": str(i),
		})
	grn = frappe.get_doc({
		"doctype": "Goods Received Note",
		"against": "Purchase Order",
		"against_id": po.name,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"to_warehouse": po.delivery_warehouse,
		"freight_charges": freight,
		"items": rows,
	})
	grn.insert(ignore_permissions=True)
	return grn


class TestGRNFreightAllocation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()
		cls._original_method = frappe.db.get_single_value(
			"YRP Stock Settings", "freight_allocation_method"
		)

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value(
			"YRP Stock Settings", "freight_allocation_method",
			cls._original_method or "By Quantity",
		)
		super().tearDownClass()

	def _set_method(self, method):
		frappe.db.set_single_value(
			"YRP Stock Settings", "freight_allocation_method", method
		)

	# ---------- By Quantity allocation ----------

	def test_01_by_quantity_single_row(self):
		"""Single row, freight=100, qty=10 → +10 per unit."""
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_BQ1_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		# _purchase_order uses rate=25 per unit
		grn = _grn_from_po(po, freight=100)
		grn.submit()
		grn.reload()

		self.assertAlmostEqual(flt(grn.items[0].rate), 25 + 10, places=4)
		self.assertAlmostEqual(flt(grn.items[0].amount), 350, places=2)
		self.assertAlmostEqual(flt(grn.total), 350, places=2)

	def test_02_by_quantity_two_rows_equal_split(self):
		"""Two rows: 4 @ 10 + 6 @ 30, freight=100. Total stock qty=10 → +10/unit.
		Row A new amount: 40 + 4*10 = 80. Row B: 180 + 6*10 = 240."""
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_BQ2_{frappe.generate_hash(length=6)}")
		po = _po_with_two_lines(warehouse)
		grn = _grn_from_po(po, freight=100)
		grn.submit()
		grn.reload()

		amounts = sorted(flt(r.amount) for r in grn.items)
		self.assertAlmostEqual(amounts[0], 80, places=2)
		self.assertAlmostEqual(amounts[1], 240, places=2)
		self.assertAlmostEqual(flt(grn.total), 320, places=2)

	# ---------- By Value allocation ----------

	def test_03_by_value_two_rows(self):
		"""Two rows: amount=40 + amount=180, freight=110. Shares: 40/220, 180/220.
		Row A: 40 + 110*(40/220) = 40 + 20 = 60. Row B: 180 + 110*(180/220) = 180 + 90 = 270."""
		self._set_method("By Value")
		warehouse = _warehouse(f"_T_Freight_BV_{frappe.generate_hash(length=6)}")
		po = _po_with_two_lines(warehouse)
		grn = _grn_from_po(po, freight=110)
		grn.submit()
		grn.reload()

		amounts = sorted(flt(r.amount) for r in grn.items)
		self.assertAlmostEqual(amounts[0], 60, places=2)
		self.assertAlmostEqual(amounts[1], 270, places=2)
		self.assertAlmostEqual(flt(grn.total), 330, places=2)

	# ---------- By Value fallback when total_amount == 0 ----------

	def test_04_by_value_falls_back_to_by_quantity_when_amount_zero(self):
		"""Free-sample receipt (rate=0): By Value cannot allocate; falls back to
		By Quantity. With freight=50 and 10 units → +5/unit."""
		self._set_method("By Value")
		warehouse = _warehouse(f"_T_Freight_FB_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		# Force the PO rate to 0 to simulate free samples
		frappe.db.set_value("Purchase Order Item", po.items[0].name, "rate", 0)
		po.reload()
		grn = _grn_from_po(po, freight=50)
		# GRN inherits rate=0 from PO via _grn_from_po
		grn.items[0].rate = 0
		grn.save(ignore_permissions=True)
		grn.submit()
		grn.reload()

		self.assertAlmostEqual(flt(grn.items[0].rate), 5, places=4)
		self.assertAlmostEqual(flt(grn.items[0].amount), 50, places=2)

	# ---------- Validation guards ----------

	def test_05_zero_qty_with_freight_blocks_submit(self):
		"""By Quantity allocation with all-zero stock_qty and non-zero freight
		must throw (Gap #12). We can't actually build such a GRN (validate_items
		rejects qty<=0), but we can mock by stubbing the items list pre-submit.
		Test directly via the helper method."""
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_ZQ_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _grn_from_po(po, freight=50)
		# Zero out the stock_qty pre-allocation
		for row in grn.items:
			row.stock_qty = 0
		with self.assertRaisesRegex(frappe.ValidationError, "total qty is zero"):
			grn.apply_freight_allocation()

	def test_06_negative_freight_rejected(self):
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_Neg_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _grn_from_po(po, freight=-10)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be negative"):
			grn.submit()

	# ---------- Zero freight is a no-op ----------

	def test_07_zero_freight_leaves_rate_unchanged(self):
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_Zero_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _grn_from_po(po, freight=0)
		original_rate = flt(grn.items[0].rate)
		grn.submit()
		grn.reload()
		self.assertAlmostEqual(flt(grn.items[0].rate), original_rate, places=4)

	# ---------- SLE valuation_rate includes freight ----------

	def test_08_sle_rate_includes_freight(self):
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_SLE_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		grn = _grn_from_po(po, freight=100)
		grn.submit()
		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": grn.name, "is_cancelled": 0},
			fields=["qty", "rate"],
		)
		self.assertEqual(len(sles), 1)
		# Total in = 10 units, rate = 25 (base) + 10 (freight per unit) = 35
		self.assertAlmostEqual(flt(sles[0].rate), 35, places=4)
		self.assertAlmostEqual(flt(sles[0].qty), 10, places=4)

	# ---------- Idempotency: apply twice doesn't double-allocate ----------

	def test_09_apply_is_idempotent(self):
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_Idemp_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		grn = _grn_from_po(po, freight=100)
		grn.apply_freight_allocation()
		rate_after_first = flt(grn.items[0].rate)
		amount_after_first = flt(grn.items[0].amount)
		grn.apply_freight_allocation()  # second call is a no-op
		self.assertAlmostEqual(flt(grn.items[0].rate), rate_after_first, places=4)
		self.assertAlmostEqual(flt(grn.items[0].amount), amount_after_first, places=2)

	# ---------- Residual reconciliation: 3-row uneven split ----------

	def test_10_three_row_split_sums_to_freight(self):
		"""Three rows: 100 + 200 + 700 stock_qty, freight=10. Naive split would
		drift via float rounding. Residual must land on last row so the SLE
		total exactly matches freight."""
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_3Row_{frappe.generate_hash(length=6)}")
		item_variant = _test_item_variant()
		uom = _item_uom(item_variant)
		po = frappe.get_doc({
			"doctype": "Purchase Order",
			"supplier": _supplier(f"_T_Freight_3R_{frappe.generate_hash(length=6)}"),
			"delivery_warehouse": warehouse,
			**_production_group_dimensions(),
			"items": [
				{"item_variant": item_variant, "qty": 100, "uom": uom, "stock_uom": uom, "conversion_factor": 1, "rate": 1, "table_index": 0, "row_index": 0},
				{"item_variant": item_variant, "qty": 200, "uom": uom, "stock_uom": uom, "conversion_factor": 1, "rate": 1, "table_index": 0, "row_index": 1},
				{"item_variant": item_variant, "qty": 700, "uom": uom, "stock_uom": uom, "conversion_factor": 1, "rate": 1, "table_index": 0, "row_index": 2},
			],
		})
		po.insert(ignore_permissions=True)
		po.submit()
		grn = _grn_from_po(po, freight=10)
		grn.submit()
		grn.reload()
		# Sum of (amount - base_amount) must equal freight exactly
		base_total = 100 + 200 + 700  # rate=1
		freight_assigned = sum(flt(r.amount) for r in grn.items) - base_total
		self.assertAlmostEqual(freight_assigned, 10, places=6)

	# ---------- Cancel reverses freight-inclusive SLEs ----------

	def test_11_cancel_reverses_freight_inclusive_sles(self):
		self._set_method("By Quantity")
		warehouse = _warehouse(f"_T_Freight_Cancel_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _grn_from_po(po, freight=25)
		grn.submit()
		grn.reload()
		grn.cancel()

		non_cancelled = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": grn.name, "is_cancelled": 0},
			fields=["qty"],
		)
		# After cancel, the bin balance for this GRN should be zero
		self.assertEqual(sum(flt(s.qty) for s in non_cancelled), 0)
