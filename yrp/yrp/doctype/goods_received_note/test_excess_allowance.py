"""Tests for the GRN excess-allowance feature.

PO side: Item.po_excess_allowed_percentage controls the max qty receivable on
GRN against Purchase Order, calculated as ordered_qty × (1 + pct/100).

WO side: Process.wo_excess_allowed_percentage controls the max qty receivable
on GRN against Work Order (per receivable line), using the source WO's process.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from yrp.yrp.doctype.delivery_challan.test_internal_unit_transfer import _make_wo
from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
	_process,
	_purchase_order,
	_purchase_order_grn,
	_supplier,
	_supplier_warehouse,
	_warehouse,
)


def _wo_grn(wo, from_wh, to_wh, item_variant, uom, qty):
	from frappe.utils import nowdate, nowtime

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
		}],
	})
	grn.insert(ignore_permissions=True)
	return grn


def _set_po_excess(item_variant, pct):
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	frappe.db.set_value("Item", parent_item, "po_excess_allowed_percentage", pct)
	frappe.clear_cache(doctype="Item")


def _set_wo_excess(process_name, pct):
	frappe.db.set_value("Process", process_name, "wo_excess_allowed_percentage", pct)
	frappe.clear_cache(doctype="Process")


class TestGRNExcessAllowance(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()

	# ---------- PO side ----------

	def test_01_po_strict_when_pct_zero(self):
		warehouse = _warehouse(f"_T_Excess_POZero_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		_set_po_excess(po.items[0].item_variant, 0)
		grn = _purchase_order_grn(po, qty=11)
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"):
			grn.submit()

	def test_02_po_within_allowance_succeeds(self):
		warehouse = _warehouse(f"_T_Excess_POIn_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=100, warehouse=warehouse)
		_set_po_excess(po.items[0].item_variant, 25)
		grn = _purchase_order_grn(po, qty=125)
		grn.submit()
		self.assertEqual(grn.docstatus, 1)

	def test_03_po_beyond_allowance_blocked(self):
		warehouse = _warehouse(f"_T_Excess_POOut_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=100, warehouse=warehouse)
		_set_po_excess(po.items[0].item_variant, 25)
		grn = _purchase_order_grn(po, qty=126)
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"):
			grn.submit()

	def test_04_po_excess_consumes_across_multiple_grns(self):
		"""Two GRNs against same PO. ordered=100, pct=25, allowance=125 total.
		First GRN of 100 leaves pending=0. Second GRN of 25 is exactly at limit
		(still within allowance). A third GRN of 1 would exceed."""
		warehouse = _warehouse(f"_T_Excess_POMulti_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=100, warehouse=warehouse)
		_set_po_excess(po.items[0].item_variant, 25)

		grn_a = _purchase_order_grn(po, qty=100)
		grn_a.submit()

		po.reload()
		# Pending is now 0 but the allowance still permits 25 more
		grn_b = _purchase_order_grn(po, qty=25)
		grn_b.submit()
		self.assertEqual(grn_b.docstatus, 1)

		po.reload()
		# Excess driven pending_quantity to -25 — verify it's NOT clamped
		self.assertAlmostEqual(flt(po.items[0].pending_quantity), -25, places=4)
		self.assertAlmostEqual(flt(po.items[0].received_quantity), 125, places=4)

		# Third receipt of 1 must fail — allowance fully consumed
		grn_c = _purchase_order_grn(po, qty=1)
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"):
			grn_c.submit()

	# ---------- WO side ----------

	def test_05_wo_within_allowance_succeeds(self):
		sender = _supplier(f"_T_Excess_WO_Sup_{frappe.generate_hash(length=6)}")
		frappe.db.set_value("Supplier", sender, "is_company_location", 0)
		receiver = _supplier(f"_T_Excess_WO_Loc_{frappe.generate_hash(length=6)}")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=100)
		_set_wo_excess(wo.process_name, 25)
		grn = _wo_grn(wo, from_wh, to_wh, iv, uom, qty=125)
		grn.submit()
		self.assertEqual(grn.docstatus, 1)

	def test_06_wo_beyond_allowance_blocked(self):
		sender = _supplier(f"_T_Excess_WO_Sup_{frappe.generate_hash(length=6)}")
		frappe.db.set_value("Supplier", sender, "is_company_location", 0)
		receiver = _supplier(f"_T_Excess_WO_Loc_{frappe.generate_hash(length=6)}")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=100)
		_set_wo_excess(wo.process_name, 25)
		grn = _wo_grn(wo, from_wh, to_wh, iv, uom, qty=126)
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"):
			grn.submit()

	def test_07_wo_strict_when_pct_zero(self):
		sender = _supplier(f"_T_Excess_WO_Sup_{frappe.generate_hash(length=6)}")
		frappe.db.set_value("Supplier", sender, "is_company_location", 0)
		receiver = _supplier(f"_T_Excess_WO_Loc_{frappe.generate_hash(length=6)}")
		wo, from_wh, to_wh, iv, uom = _make_wo(sender, receiver, qty=10)
		_set_wo_excess(wo.process_name, 0)
		grn = _wo_grn(wo, from_wh, to_wh, iv, uom, qty=11)
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"):
			grn.submit()
