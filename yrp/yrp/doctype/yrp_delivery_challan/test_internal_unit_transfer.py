"""Tests for the Delivery Challan internal-unit transit flow.

Covers the 12 assertions from the design plan:
  1-3:  is_internal_unit computation
  4:    DC submit routes SLE to transit warehouse
  5-6:  make_dc_completion endpoint + double-draft guard
  7-9:  partial / full completion STE behavior
  10-11: cancel rollback paths (STE + DC)
  12:   missing transit warehouse blocks DC submit
"""

from contextlib import nullcontext
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from yrp.stock.dimensions import get_stock_dimensions
from yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan import DeliveryChallan
from yrp.yrp.doctype.yrp_goods_received_note.test_purchase_order_grn import (
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


def _neutral_production_group_dimensions(item=None):
	"""Use dimension values that cannot impersonate a live production order.

	Host apps may make a production dimension mandatory (Essdee uses Lot).  The
	base transit tests still have to populate it, but selecting an arbitrary live
	Lot can bind the test Item to another product.  Prefer a dimension record
	whose optional ``item`` link is empty, while retaining the generic fallback
	for dimensions without such a field.
	"""
	values = _production_group_dimensions()
	for dimension in get_stock_dimensions():
		if not dimension.get("is_production_group"):
			continue
		doctype = dimension["dimension_doctype"]
		meta = frappe.get_meta(doctype)
		if not meta.has_field("item"):
			continue
		if item:
			matches = frappe.get_all(
				doctype,
				filters={"item": item, "name": ["like", "_Test Internal Transit %"]},
				pluck="name",
				order_by="name asc",
				limit=1,
			)
			neutral = matches[0] if matches else None
			if not neutral:
				naming_field = (meta.autoname or "").removeprefix("field:")
				if not naming_field or not meta.has_field(naming_field):
					continue
				neutral = frappe.get_doc(
					{
						"doctype": doctype,
						naming_field: f"_Test Internal Transit {frappe.generate_hash(length=8)}",
						"item": item,
					}
				).insert(ignore_permissions=True).name
		else:
			neutral = frappe.db.get_value(doctype, {"item": ["is", "not set"]}, "name")
		if neutral:
			values[dimension["fieldname"]] = neutral
	return values


def _company_supplier(prefix):
	sup = _supplier(f"{prefix}_{frappe.generate_hash(length=6)}")
	frappe.db.set_value('YRP Supplier', sup, "is_company_location", 1)
	return sup


def _non_company_supplier(prefix):
	sup = _supplier(f"{prefix}_{frappe.generate_hash(length=6)}")
	frappe.db.set_value('YRP Supplier', sup, "is_company_location", 0)
	return sup


def _row_dimensions(row):
	return {
		dimension["fieldname"]: row.get(dimension["fieldname"])
		for dimension in get_stock_dimensions()
		if row.get(dimension["fieldname"])
	}


def _test_stock_item_variant():
	variants = frappe.db.sql(
		"""
		SELECT iv.name
		FROM `tabYRP Item Variant` iv
		INNER JOIN `tabYRP Item` item ON item.name = iv.item
		WHERE COALESCE(item.is_stock_item, 0) = 1
		  AND COALESCE(item.default_unit_of_measure, '') != ''
		ORDER BY iv.name
		LIMIT 1
		""",
		pluck=True,
	)
	return variants[0] if variants else _test_item_variant()


def _seed_stock(item_variant, warehouse, qty, dimensions=None, posting_date=None):
	# Transit-flow tests are deliberately dimension-neutral.  Borrowing the
	# first live production dimension (for example an Essdee Lot) couples this
	# base-YRP test to unrelated host-app validation and mutable site data.
	parent_item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
	dimensions = (
		_neutral_production_group_dimensions(parent_item)
		if dimensions is None
		else dimensions
	)
	ste = frappe.get_doc({
		"doctype": 'YRP Stock Entry',
		"purpose": "Material Receipt",
		"to_warehouse": warehouse,
		"edit_posting_date_and_time": 1 if posting_date else 0,
		"posting_date": posting_date,
		"posting_time": "09:00:00" if posting_date else None,
		"items": [{
			"item": item_variant,
			"qty": qty,
			"uom": _item_uom(item_variant),
			"conversion_factor": 1,
			"rate": 10,
			**dimensions,
		}],
	})
	ste.insert(ignore_permissions=True)
	ste.submit()
	return ste


def _make_wo(from_location, to_supplier, qty=10):
	item_variant = _test_stock_item_variant()
	parent_item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
	uom = _item_uom(item_variant)
	from_wh = _supplier_warehouse(from_location, f"_T_DC_From_WH_{frappe.generate_hash(length=6)}")
	to_wh = _supplier_warehouse(to_supplier, f"_T_DC_To_WH_{frappe.generate_hash(length=6)}")
	process_name = _process("_Test DC Internal Process")
	dimensions = _neutral_production_group_dimensions(parent_item)
	_process_cost(process_name, parent_item, to_supplier, dimensions)
	wo = frappe.get_doc({
		"doctype": 'YRP Work Order',
		"supplier": to_supplier,
		"delivery_location": from_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_T_DC_To_Addr_{frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_T_DC_From_Addr_{frappe.generate_hash(length=6)}"),
		"process_name": process_name,
		"item": parent_item,
		**dimensions,
		"deliverables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"table_index": 0,
			"row_index": 0,
			**dimensions,
		}],
		"receivables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"cost": 12,
			"table_index": 0,
			"row_index": 0,
			**dimensions,
		}],
		"work_order_calculated_items": [{
			"item_variant": item_variant,
			"quantity": qty,
			"received_qty": 0,
			"billed_qty": 0,
			"set_combination": {},
		}],
	})
	# This is a base-YRP routing fixture, not a host-app production-order
	# fixture.  When Essdee is installed its Lot/IPD selector is independently
	# covered in Essdee tests and must not turn this test into a garment setup.
	host_validator = (
		patch("essdee_yrp.work_order_hooks.validate_lot_process_selection")
		if "essdee_yrp" in frappe.get_installed_apps()
		else nullcontext()
	)
	with host_validator:
		wo.insert(ignore_permissions=True)
		wo.submit()
	return wo, from_wh, to_wh, item_variant, uom


def _make_dc(wo, from_wh, to_wh, item_variant, uom, qty=5):
	deliverable = wo.deliverables[0]
	dimensions = _row_dimensions(deliverable)
	dc = frappe.get_doc({
		"doctype": 'YRP Delivery Challan',
		"work_order": wo.name,
		"from_location": wo.delivery_location,
		"supplier": wo.supplier,
		"from_address": wo.delivery_address,
		"supplier_address": wo.supplier_address,
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
			"ref_doctype": 'YRP Work Order Deliverables',
			"ref_docname": deliverable.name,
			"table_index": 0,
			"row_index": "0",
			**dimensions,
		}],
	})
	dc.insert(ignore_permissions=True)
	return dc


class TestDCInternalUnitTransfer(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._original_transit = frappe.db.get_single_value(
			'YRP YRP Stock Settings', "transit_warehouse"
		)
		cls.transit_wh = _warehouse(f"_T_DC_Transit_{frappe.generate_hash(length=6)}")
		frappe.db.set_single_value(
			'YRP YRP Stock Settings', "transit_warehouse", cls.transit_wh
		)
		_default_received_type()

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value(
			'YRP YRP Stock Settings', "transit_warehouse", cls._original_transit
		)
		super().tearDownClass()

	# ---------- Tests 1-3: is_internal_unit computation ----------

	def test_selected_from_location_controls_source_warehouse(self):
		work_order_location = _company_supplier("_T_DC_Default_From")
		selected_location = _company_supplier("_T_DC_Selected_From")
		to_supplier = _company_supplier("_T_DC_Selected_To")
		wo, _from_wh, _to_wh, _iv, _uom = _make_wo(
			work_order_location, to_supplier
		)
		# Exercise the base controller directly: Essdee intentionally overrides
		# this fallback and requires the operator to choose both source fields.
		dc = DeliveryChallan({"doctype": 'YRP Delivery Challan'})
		dc.work_order = wo.name
		dc.from_location = selected_location

		def warehouse_for(supplier):
			return {
				selected_location: "SELECTED-WAREHOUSE",
				to_supplier: "TARGET-WAREHOUSE",
			}[supplier]

		with patch(
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan._get_warehouse_for_supplier",
			side_effect=warehouse_for,
		):
			dc.set_missing_values()

		self.assertEqual(dc.from_location, selected_location)
		self.assertEqual(dc.from_warehouse, "SELECTED-WAREHOUSE")
		self.assertEqual(dc.to_warehouse, "TARGET-WAREHOUSE")

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
			'YRP Stock Ledger Entry',
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
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)

		ste = frappe.get_doc('YRP Stock Entry', ste_name)
		self.assertEqual(ste.purpose, "DC Completion")
		self.assertEqual(ste.against, 'YRP Delivery Challan')
		self.assertEqual(ste.against_id, dc.name)
		self.assertEqual(ste.from_warehouse, from_wh)
		self.assertEqual(ste.to_warehouse, to_wh)
		self.assertEqual(len(ste.items), 1)
		self.assertEqual(ste.items[0].qty, 5)
		self.assertEqual(ste.items[0].against, 'YRP Delivery Challan Item')
		self.assertEqual(ste.items[0].against_id_detail, dc.items[0].name)

	def test_06_double_make_dc_completion_rejected(self):
		from_loc = _company_supplier("_T_DC6_From")
		to_sup = _company_supplier("_T_DC6_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
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
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc('YRP Stock Entry', ste_name)
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
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc('YRP Stock Entry', ste_name)
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
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc('YRP Stock Entry', ste_name)
		ste.submit()  # full qty

		dc.reload()
		self.assertEqual(dc.transfer_complete, 1)
		self.assertAlmostEqual(dc.ste_transferred, 5, places=3)
		self.assertAlmostEqual(dc.ste_transferred_percent, 100, places=1)

		# The ledger is always in the Item's stock UOM, while completion progress
		# remains in the transaction UOM.
		sles = frappe.db.get_all(
			'YRP Stock Ledger Entry',
			filters={"voucher_no": ste.name, "is_cancelled": 0},
			fields=["warehouse", "qty"],
		)
		balances = {s.warehouse: flt(s.qty) for s in sles}
		expected_stock_qty = 5 * flt(ste.items[0].conversion_factor)
		self.assertAlmostEqual(
			balances.get(self.transit_wh, 0), -expected_stock_qty, places=3
		)
		self.assertAlmostEqual(
			balances.get(to_wh, 0), expected_stock_qty, places=3
		)

	# ---------- Test 10: STE cancel rollback ----------

	def test_10_ste_cancel_rolls_back(self):
		from_loc = _company_supplier("_T_DC10_From")
		to_sup = _company_supplier("_T_DC10_To")
		wo, from_wh, to_wh, iv, uom = _make_wo(from_loc, to_sup, qty=10)
		_seed_stock(iv, from_wh, 50)
		dc = _make_dc(wo, from_wh, to_wh, iv, uom, qty=5)
		dc.submit()

		make_fn = frappe.get_attr(
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc('YRP Stock Entry', ste_name)
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
			"yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan.make_dc_completion"
		)
		ste_name = make_fn(dc.name)
		ste = frappe.get_doc('YRP Stock Entry', ste_name)
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
		frappe.db.set_single_value('YRP YRP Stock Settings', "transit_warehouse", None)
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
				'YRP YRP Stock Settings', "transit_warehouse", self.transit_wh
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
			'YRP Repost Item Valuation',
			{"voucher_type": 'YRP Delivery Challan', "voucher_no": dc.name},
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
		dc.edit_posting_date_and_time = 1
		dc.posting_date = add_days(nowdate(), -1)
		dc.posting_time = "00:00:00"
		dc.save(ignore_permissions=True)
		dc.submit()

		rivs = frappe.db.get_all(
			'YRP Repost Item Valuation',
			filters={"voucher_type": 'YRP Delivery Challan', "voucher_no": dc.name},
			fields=["name", "based_on", "status"],
		)
		self.assertEqual(len(rivs), 1, f"Expected 1 RIV, got {len(rivs)}: {rivs}")
		self.assertEqual(rivs[0].based_on, "Transaction")
