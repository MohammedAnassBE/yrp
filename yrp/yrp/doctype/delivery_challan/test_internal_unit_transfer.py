"""Tests for the Delivery Challan internal-unit transit flow.

Covers the 12 assertions from the design plan:
  1-3:  is_internal_unit computation
  4:    DC submit routes SLE to transit warehouse
  5-6:  make_dc_completion endpoint + double-draft guard
  7-9:  partial / full completion STE behavior
  10-11: cancel rollback paths (STE + DC)
  12:   missing transit warehouse blocks DC submit
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_address,
	_default_received_type,
	_item_uom,
	_process,
	_production_group_dimensions,
	_supplier,
	_supplier_warehouse,
	_test_item_variant,
	_warehouse,
)


def _test_lot():
	existing = frappe.db.get_value("Lot", {"lot_name": ["like", "_T_DC_Lot%"]}, "name")
	if existing:
		return existing
	doc = frappe.get_doc({
		"doctype": "Lot",
		"lot_name": f"_T_DC_Lot_{frappe.generate_hash(length=6)}",
	})
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	return doc.name


def _company_supplier(prefix):
	sup = _supplier(f"{prefix}_{frappe.generate_hash(length=6)}")
	frappe.db.set_value("Supplier", sup, "is_company_location", 1)
	return sup


def _non_company_supplier(prefix):
	sup = _supplier(f"{prefix}_{frappe.generate_hash(length=6)}")
	frappe.db.set_value("Supplier", sup, "is_company_location", 0)
	return sup


def _seed_stock(item_variant, warehouse, qty, lot=None, posting_date=None):
	ste = frappe.get_doc({
		"doctype": "Stock Entry",
		"purpose": "Material Receipt",
		"to_warehouse": warehouse,
		"posting_date": posting_date,
		"items": [{
			"item": item_variant,
			"qty": qty,
			"uom": _item_uom(item_variant),
			"conversion_factor": 1,
			"rate": 10,
			"lot": lot or _test_lot(),
		}],
	})
	ste.insert(ignore_permissions=True)
	ste.submit()
	return ste


def _make_wo(from_location, to_supplier, qty=10):
	item_variant = _test_item_variant()
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	uom = _item_uom(item_variant)
	from_wh = _supplier_warehouse(from_location, f"_T_DC_From_WH_{frappe.generate_hash(length=6)}")
	to_wh = _supplier_warehouse(to_supplier, f"_T_DC_To_WH_{frappe.generate_hash(length=6)}")
	wo = frappe.get_doc({
		"doctype": "Work Order",
		"is_rework": 1,
		"rework_type": "No Cost",
		"supplier": to_supplier,
		"delivery_location": from_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_T_DC_To_Addr_{frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_T_DC_From_Addr_{frappe.generate_hash(length=6)}"),
		"process_name": _process("_Test DC Internal Process"),
		"item": parent_item,
		**_production_group_dimensions(),
		"deliverables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"table_index": 0,
			"row_index": 0,
			"lot": _test_lot(),
		}],
		"receivables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"cost": 12,
			"table_index": 0,
			"row_index": 0,
			"lot": _test_lot(),
		}],
		"work_order_calculated_items": [{
			"item_variant": item_variant,
			"quantity": qty,
			"received_qty": 0,
			"billed_qty": 0,
			"set_combination": {},
		}],
	})
	wo.insert(ignore_permissions=True)
	wo.submit()
	return wo, from_wh, to_wh, item_variant, uom


def _make_dc(wo, from_wh, to_wh, item_variant, uom, qty=5):
	deliverable = wo.deliverables[0]
	dc = frappe.get_doc({
		"doctype": "Delivery Challan",
		"work_order": wo.name,
		"from_location": wo.delivery_location,
		"supplier": wo.supplier,
		"from_warehouse": from_wh,
		"to_warehouse": to_wh,
		"process_name": wo.process_name,
		"item": wo.item,
		"items": [{
			"item_variant": item_variant,
			"qty": qty,
			"delivered_quantity": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"ref_doctype": "Work Order Deliverables",
			"ref_docname": deliverable.name,
			"table_index": 0,
			"row_index": "0",
			"lot": _test_lot(),
		}],
	})
	dc.insert(ignore_permissions=True)
	return dc


class TestDCInternalUnitTransfer(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._original_transit = frappe.db.get_single_value(
			"YRP Stock Settings", "transit_warehouse"
		)
		cls.transit_wh = _warehouse(f"_T_DC_Transit_{frappe.generate_hash(length=6)}")
		frappe.db.set_single_value(
			"YRP Stock Settings", "transit_warehouse", cls.transit_wh
		)
		_default_received_type()

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value(
			"YRP Stock Settings", "transit_warehouse", cls._original_transit
		)
		super().tearDownClass()

	# ---------- Tests 1-3: is_internal_unit computation ----------

	def test_01_internal_unit_false_when_one_supplier_not_company(self):
		from_loc = _company_supplier("_T_DC_From")
		to_sup = _non_company_supplier("_T_DC_ToExt")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom)
		self.assertEqual(dc.is_internal_unit, 0)

	def test_02_internal_unit_true_when_both_company_and_different(self):
		from_loc = _company_supplier("_T_DC_From2")
		to_sup = _company_supplier("_T_DC_To2")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom)
		self.assertEqual(dc.is_internal_unit, 1)

	def test_03_internal_unit_false_when_same_location(self):
		# Both ends are the same company-location supplier — no transit needed
		loc = _company_supplier("_T_DC_Same")
		wo, from_wh, to_wh, iv, uom = _make_wo(loc, loc)
		# from_wh == to_wh is rejected by DC.validate_items, so override to_wh
		other_wh = _supplier_warehouse(loc, f"_T_DC_Same_Other_WH_{frappe.generate_hash(length=6)}")
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, other_wh, iv, uom)
		self.assertEqual(dc.is_internal_unit, 0)

	# ---------- Test 4: DC submit routes to transit ----------

	def test_04_dc_submit_routes_to_transit(self):
		from_loc = _company_supplier("_T_DC4_From")
		to_sup = _company_supplier("_T_DC4_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		self.assertEqual(dc.is_internal_unit, 1)
		self.assertEqual(dc.transfer_complete, 0)

		# Confirm SLEs: -from_wh, +transit_wh; NO entry at to_wh
		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": dc.name, "is_cancelled": 0},
			fields=["warehouse", "qty"],
		)
		warehouses = {s.warehouse for s in sles}
		self.assertIn(self.transit_wh, warehouses)
		self.assertIn(from_wh, warehouses)
		self.assertNotIn(to_wh, warehouses)

	# ---------- Tests 5-6: make_dc_completion endpoint ----------

	def test_05_make_dc_completion_builds_correct_ste(self):
		from_loc = _company_supplier("_T_DC5_From")
		to_sup = _company_supplier("_T_DC5_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)

		ste = frappe.get_doc("Stock Entry", ste_name)
		self.assertEqual(ste.purpose, "DC Completion")
		self.assertEqual(ste.against, "Delivery Challan")
		self.assertEqual(ste.against_id, dc.name)
		self.assertEqual(ste.from_warehouse, from_wh)
		self.assertEqual(ste.to_warehouse, to_wh)
		self.assertEqual(len(ste.items), 1)
		self.assertEqual(ste.items[0].qty, 5)
		self.assertEqual(ste.items[0].against, "Delivery Challan Item")
		self.assertEqual(ste.items[0].against_id_detail, dc.items[0].name)

	def test_06_double_make_dc_completion_rejected(self):
		from_loc = _company_supplier("_T_DC6_From")
		to_sup = _company_supplier("_T_DC6_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		make_fn(dc.name)  # first call succeeds, leaves draft
		with self.assertRaises(frappe.ValidationError):
			make_fn(dc.name)  # second call must throw

	# ---------- Tests 7-9: completion STE behavior ----------

	def test_07_partial_completion_keeps_transfer_complete_zero(self):
		from_loc = _company_supplier("_T_DC7_From")
		to_sup = _company_supplier("_T_DC7_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=6)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.items[0].qty = 2  # partial — only 2 of 6
		ste.items[0].stock_qty = 2
		ste.save(ignore_permissions=True)
		ste.submit()

		dc.reload()
		self.assertEqual(dc.transfer_complete, 0)
		self.assertAlmostEqual(dc.ste_transferred, 2, places=3)
		self.assertAlmostEqual(dc.ste_transferred_percent, 2 / 6 * 100, places=2)
		self.assertAlmostEqual(dc.items[0].ste_delivered_quantity, 2, places=3)

	def test_08_ste_qty_exceeding_pending_throws(self):
		from_loc = _company_supplier("_T_DC8_From")
		to_sup = _company_supplier("_T_DC8_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=4)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.items[0].qty = 99  # way over pending of 4
		ste.items[0].stock_qty = 99
		with self.assertRaises(frappe.ValidationError):
			ste.save(ignore_permissions=True)

	def test_09_full_completion_flips_transfer_complete(self):
		from_loc = _company_supplier("_T_DC9_From")
		to_sup = _company_supplier("_T_DC9_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()  # full qty

		dc.reload()
		self.assertEqual(dc.transfer_complete, 1)
		self.assertAlmostEqual(dc.ste_transferred, 5, places=3)
		self.assertAlmostEqual(dc.ste_transferred_percent, 100, places=1)

		# SLE: transit -5, to_wh +5
		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": ste.name, "is_cancelled": 0},
			fields=["warehouse", "qty"],
		)
		balances = {s.warehouse: flt(s.qty) for s in sles}
		self.assertAlmostEqual(balances.get(self.transit_wh, 0), -5, places=3)
		self.assertAlmostEqual(balances.get(to_wh, 0), 5, places=3)

	# ---------- Test 10: STE cancel rollback ----------

	def test_10_ste_cancel_rolls_back(self):
		from_loc = _company_supplier("_T_DC10_From")
		to_sup = _company_supplier("_T_DC10_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()

		dc.reload()
		self.assertEqual(dc.transfer_complete, 1)

		ste.reload()
		ste.cancel()

		dc.reload()
		self.assertEqual(dc.transfer_complete, 0)
		self.assertAlmostEqual(dc.ste_transferred, 0, places=3)
		self.assertAlmostEqual(dc.ste_transferred_percent, 0, places=2)
		self.assertAlmostEqual(dc.items[0].ste_delivered_quantity, 0, places=3)

	# ---------- Test 11: DC cancel cascades STE cancel ----------

	def test_11_dc_cancel_cascades_ste_cancel(self):
		from_loc = _company_supplier("_T_DC11_From")
		to_sup = _company_supplier("_T_DC11_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.delivery_challan.delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()

		dc.reload()
		dc.cancel()

		ste.reload()
		self.assertEqual(ste.docstatus, 2)  # cancelled by cascade
		dc.reload()
		self.assertEqual(dc.transfer_complete, 0)
		self.assertAlmostEqual(dc.ste_transferred, 0, places=3)

	# ---------- Test 12: missing transit warehouse blocks DC submit ----------

	def test_12_missing_transit_warehouse_blocks_submit(self):
		# Temporarily clear transit warehouse
		frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", None)
		try:
			from_loc = _company_supplier("_T_DC12_From")
			to_sup = _company_supplier("_T_DC12_To")
			wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
			_seed_stock(iv, from_wh, 50)
			dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
			with self.assertRaises(frappe.ValidationError):
				dc.submit()
		finally:
			frappe.db.set_single_value(
				"YRP Stock Settings", "transit_warehouse", self.transit_wh
			)

	# ---------- Tests 13-14: explicit RIV enqueue gating ----------

	def test_13_normal_dc_does_not_enqueue_riv(self):
		# Fresh DC at current time with no later SLEs in its buckets — no RIV.
		from_loc = _company_supplier("_T_DC13_From")
		to_sup = _company_supplier("_T_DC13_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		riv_count = frappe.db.count(
			"Repost Item Valuation",
			{"voucher_type": "Delivery Challan", "voucher_no": dc.name},
		)
		self.assertEqual(riv_count, 0)

	def test_14_backdated_dc_enqueues_riv(self):
		# Seed stock 2 days ago, then create a "future" SLE today, then submit a
		# backdated DC yesterday touching the same bucket. The backdated DC should
		# enqueue an RIV because today's SLE is strictly later than yesterday.
		from frappe.utils import add_days

		from_loc = _company_supplier("_T_DC14_From")
		to_sup = _company_supplier("_T_DC14_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=20)
		# Seed at 3 days ago so stock is available on the backdated DC's date
		_seed_stock(iv, from_wh, 100, posting_date=add_days(nowdate(), -3))
		# Add a "future" SLE at today's date
		_seed_stock(iv, from_wh, 0.01)  # tiny extra at today

		# DC backdated to yesterday
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.posting_date = add_days(nowdate(), -1)
		dc.posting_time = "00:00:00"
		dc.save(ignore_permissions=True)
		dc.submit()

		rivs = frappe.db.get_all(
			"Repost Item Valuation",
			filters={"voucher_type": "Delivery Challan", "voucher_no": dc.name},
			fields=["name", "based_on", "status"],
		)
		self.assertEqual(len(rivs), 1, f"Expected 1 RIV, got {len(rivs)}: {rivs}")
		self.assertEqual(rivs[0].based_on, "Transaction")
