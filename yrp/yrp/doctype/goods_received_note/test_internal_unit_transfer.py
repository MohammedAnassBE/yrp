"""Tests for the Goods Received Note internal-unit transit flow.

Mirrors the Delivery Challan internal-unit tests but for the receiving side:
  1-3:  is_internal_unit computation
  4:    GRN submit routes SLE to transit warehouse
  5-6:  make_grn_completion endpoint + double-draft guard
  7-9:  partial / full completion STE behavior
  10-11: cancel rollback paths (STE + GRN)
  12:   missing transit warehouse blocks GRN submit
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_address,
	_default_received_type,
	_item_uom,
	_process,
	_process_cost,
	_production_group_dimensions,
	_supplier,
	_supplier_warehouse,
	_test_item_variant,
	_warehouse,
)


def _test_lot():
	existing = frappe.db.get_value("Lot", {"lot_name": ["like", "_T_GRN_Lot%"]}, "name")
	if existing:
		return existing
	doc = frappe.get_doc({
		"doctype": "Lot",
		"lot_name": f"_T_GRN_Lot_{frappe.generate_hash(length=6)}",
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


def _make_wo(sender_supplier, receiver_location, qty=10):
	"""Build a submitted WO whose supplier (sender) and delivery_location
	(receiver) match the GRN's flow direction: goods originate at `sender_supplier`
	and arrive at `receiver_location`."""
	item_variant = _test_item_variant()
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	uom = _item_uom(item_variant)
	from_wh = _supplier_warehouse(sender_supplier, f"_T_GRN_From_WH_{frappe.generate_hash(length=6)}")
	to_wh = _supplier_warehouse(receiver_location, f"_T_GRN_To_WH_{frappe.generate_hash(length=6)}")
	process_name = _process("_Test GRN Internal Process")
	dimensions = _production_group_dimensions()
	_process_cost(process_name, parent_item, sender_supplier, dimensions)
	wo = frappe.get_doc({
		"doctype": "Work Order",
		"supplier": sender_supplier,
		"delivery_location": receiver_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_T_GRN_Sup_Addr_{frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_T_GRN_Dest_Addr_{frappe.generate_hash(length=6)}"),
		"process_name": process_name,
		"item": parent_item,
		**dimensions,
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


def _make_grn(wo, from_wh, to_wh, item_variant, uom, qty=5):
	receivable = wo.receivables[0]
	grn = frappe.get_doc({
		"doctype": "Goods Received Note",
		"against": "Work Order",
		"against_id": wo.name,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"supplier": wo.supplier,
		"delivery_location": wo.delivery_location,
		"from_warehouse": from_wh,
		"to_warehouse": to_wh,
		"process_name": wo.process_name,
		"item": wo.item,
		"items": [{
			"item_variant": item_variant,
			"quantity": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"rate": 12,
			"ref_doctype": "Work Order Receivables",
			"ref_docname": receivable.name,
			"table_index": 0,
			"row_index": "0",
			"lot": _test_lot(),
		}],
	})
	grn.insert(ignore_permissions=True)
	return grn


class TestGRNInternalUnitTransfer(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._original_transit = frappe.db.get_single_value(
			"YRP Stock Settings", "transit_warehouse"
		)
		cls.transit_wh = _warehouse(f"_T_GRN_Transit_{frappe.generate_hash(length=6)}")
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

	def test_01_internal_unit_false_when_supplier_not_company(self):
		sender = _non_company_supplier("_T_GRN_SenderExt")
		receiver = _company_supplier("_T_GRN_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom)
		self.assertEqual(grn.is_internal_unit, 0)

	def test_02_internal_unit_true_when_both_company_and_different(self):
		sender = _company_supplier("_T_GRN_Sender2")
		receiver = _company_supplier("_T_GRN_Receiver2")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom)
		self.assertEqual(grn.is_internal_unit, 1)

	def test_03_internal_unit_false_when_same_location(self):
		loc = _company_supplier("_T_GRN_Same")
		wo, from_wh, to_wh, iv, uom = _make_wo(loc, loc)
		other_wh = _supplier_warehouse(loc, f"_T_GRN_Same_Other_WH_{frappe.generate_hash(length=6)}")
		grn = _make_grn(wo, from_wh, other_wh, iv, uom)
		self.assertEqual(grn.is_internal_unit, 0)

	# ---------- Test 4: GRN submit routes to transit ----------

	def test_04_grn_submit_routes_to_transit(self):
		sender = _company_supplier("_T_GRN4_Sender")
		receiver = _company_supplier("_T_GRN4_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		self.assertEqual(grn.is_internal_unit, 1)
		self.assertEqual(grn.transfer_complete, 0)

		sles = frappe.db.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": grn.name, "is_cancelled": 0},
			fields=["warehouse", "qty"],
		)
		warehouses = {s.warehouse for s in sles}
		self.assertIn(self.transit_wh, warehouses)
		self.assertNotIn(to_wh, warehouses)

	# ---------- Tests 5-6: make_grn_completion endpoint ----------

	def test_05_make_grn_completion_builds_correct_ste(self):
		sender = _company_supplier("_T_GRN5_Sender")
		receiver = _company_supplier("_T_GRN5_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)

		ste = frappe.get_doc("Stock Entry", ste_name)
		self.assertEqual(ste.purpose, "GRN Completion")
		self.assertEqual(ste.against, "Goods Received Note")
		self.assertEqual(ste.against_id, grn.name)
		self.assertEqual(ste.from_warehouse, self.transit_wh)
		self.assertEqual(ste.to_warehouse, to_wh)
		self.assertEqual(len(ste.items), 1)
		self.assertEqual(ste.items[0].qty, 5)
		self.assertEqual(ste.items[0].against, "Goods Received Note Item")
		self.assertEqual(ste.items[0].against_id_detail, grn.items[0].name)

	def test_06_double_make_grn_completion_rejected(self):
		sender = _company_supplier("_T_GRN6_Sender")
		receiver = _company_supplier("_T_GRN6_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		make_fn(grn.name)
		with self.assertRaises(frappe.ValidationError):
			make_fn(grn.name)

	# ---------- Tests 7-9: completion STE behavior ----------

	def test_07_partial_completion_keeps_transfer_complete_zero(self):
		sender = _company_supplier("_T_GRN7_Sender")
		receiver = _company_supplier("_T_GRN7_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=6)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.items[0].qty = 2
		ste.items[0].stock_qty = 2
		ste.save(ignore_permissions=True)
		ste.submit()

		grn.reload()
		self.assertEqual(grn.transfer_complete, 0)
		self.assertAlmostEqual(grn.ste_transferred, 2, places=3)
		self.assertAlmostEqual(grn.ste_transferred_percent, 2 / 6 * 100, places=2)
		self.assertAlmostEqual(grn.items[0].ste_received_quantity, 2, places=3)

	def test_08_ste_qty_exceeding_pending_throws(self):
		sender = _company_supplier("_T_GRN8_Sender")
		receiver = _company_supplier("_T_GRN8_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=4)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.items[0].qty = 99
		ste.items[0].stock_qty = 99
		with self.assertRaises(frappe.ValidationError):
			ste.save(ignore_permissions=True)

	def test_09_full_completion_flips_transfer_complete(self):
		sender = _company_supplier("_T_GRN9_Sender")
		receiver = _company_supplier("_T_GRN9_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()

		grn.reload()
		self.assertEqual(grn.transfer_complete, 1)
		self.assertAlmostEqual(grn.ste_transferred, 5, places=3)
		self.assertAlmostEqual(grn.ste_transferred_percent, 100, places=1)

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
		sender = _company_supplier("_T_GRN10_Sender")
		receiver = _company_supplier("_T_GRN10_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()

		grn.reload()
		self.assertEqual(grn.transfer_complete, 1)

		ste.reload()
		ste.cancel()

		grn.reload()
		self.assertEqual(grn.transfer_complete, 0)
		self.assertAlmostEqual(grn.ste_transferred, 0, places=3)
		self.assertAlmostEqual(grn.ste_transferred_percent, 0, places=2)
		self.assertAlmostEqual(grn.items[0].ste_received_quantity, 0, places=3)

	# ---------- Test 11: GRN cancel cascades STE cancel ----------

	def test_11_grn_cancel_cascades_ste_cancel(self):
		sender = _company_supplier("_T_GRN11_Sender")
		receiver = _company_supplier("_T_GRN11_Receiver")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
		grn.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.goods_received_note.goods_received_note.make_grn_completion"
		)
		ste_name = make_fn(grn.name)
		ste = frappe.get_doc("Stock Entry", ste_name)
		ste.submit()

		grn.reload()
		grn.cancel()

		ste.reload()
		self.assertEqual(ste.docstatus, 2)
		grn.reload()
		self.assertEqual(grn.transfer_complete, 0)
		self.assertAlmostEqual(grn.ste_transferred, 0, places=3)

	# ---------- Test 12: missing transit warehouse blocks GRN submit ----------

	def test_12_missing_transit_warehouse_blocks_submit(self):
		frappe.db.set_single_value("YRP Stock Settings", "transit_warehouse", None)
		try:
			sender = _company_supplier("_T_GRN12_Sender")
			receiver = _company_supplier("_T_GRN12_Receiver")
			wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
			grn = _make_grn(wo, from_wh, to_wh, iv, uom, qty=5)
			with self.assertRaises(frappe.ValidationError):
				grn.submit()
		finally:
			frappe.db.set_single_value(
				"YRP Stock Settings", "transit_warehouse", self.transit_wh
			)
