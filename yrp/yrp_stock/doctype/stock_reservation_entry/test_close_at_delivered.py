"""Tests for SRE close-at-delivered: partial-delivery + plan-change scenario.

Scenario: reserved 100, DC delivered 50, plan changed, the remaining 50
must be released without losing audit on what was actually delivered.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.stock.utils import get_sre_reserved_qty
from yrp.yrp.doctype.work_order.test_rework_flow import (
	_make_parent_grn,
	_make_parent_work_order,
	_received_type,
	_set_rejected_received_type,
)
from yrp.yrp.doctype.work_order.work_order import _stock_dimension_values


def _build_normal_wo_with_sre(reserved=10, seed_qty=10):
	"""Create a normal (non-rework) WO + GRN-seeded delivery_wh + manual SRE.
	Returns (wo, sre, delivery_wh, supplier_wh, item_variant, uom, received_type).
	"""
	rt = _received_type(f"_T_CloseAtDel_{frappe.generate_hash(length=6)}")
	rejected_rt = _received_type(f"_T_CloseAtDel_Rej_{frappe.generate_hash(length=6)}")
	_set_rejected_received_type(rejected_rt)

	wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=reserved)
	_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rt, qty=seed_qty)

	deliverable = wo.deliverables[0]
	dim_values = _stock_dimension_values(wo, deliverable)
	dim_values["received_type"] = rt
	sre = frappe.get_doc({
		"doctype": "Stock Reservation Entry",
		"item_code": item_variant,
		"warehouse": delivery_wh,
		"voucher_type": "Work Order",
		"voucher_no": wo.name,
		"voucher_detail_no": deliverable.name,
		"stock_uom": uom,
		"available_qty": seed_qty,
		"voucher_qty": reserved,
		"reserved_qty": reserved,
		"delivered_qty": 0,
		"closed_qty": 0,
		**dim_values,
	})
	sre.insert(ignore_permissions=True)
	sre.submit()
	return wo, sre, delivery_wh, supplier_wh, item_variant, uom, rt


def _make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty):
	dc = frappe.get_doc({
		"doctype": "Delivery Challan",
		"work_order": wo.name,
		"from_location": wo.delivery_location,
		"supplier": wo.supplier,
		"from_warehouse": delivery_wh,
		"to_warehouse": supplier_wh,
		"process_name": wo.process_name,
		"item": wo.item,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"items": [{
			"item_variant": item_variant,
			"qty": qty,
			"delivered_quantity": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"received_type": rt,
			"ref_doctype": "Work Order Deliverables",
			"ref_docname": wo.deliverables[0].name,
			"table_index": 0,
			"row_index": "0",
		}],
	})
	dc.insert(ignore_permissions=True)
	dc.submit()
	return dc


class TestSREClose(FrappeTestCase):
	def test_close_at_delivered_preserves_audit_and_releases_remaining(self):
		"""Reserved 10, delivered 6, close: closed_qty=4, status='Closed',
		reserved_qty and delivered_qty unchanged (audit preserved).
		"""
		wo, sre, delivery_wh, supplier_wh, item_variant, uom, rt = _build_normal_wo_with_sre(
			reserved=10, seed_qty=10,
		)
		_make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty=6)

		sre.reload()
		self.assertAlmostEqual(flt(sre.delivered_qty), 6)
		self.assertEqual(sre.status, "Partially Delivered")

		sre.close_at_delivered()

		sre.reload()
		# Audit fields untouched.
		self.assertAlmostEqual(flt(sre.reserved_qty), 10)
		self.assertAlmostEqual(flt(sre.delivered_qty), 6)
		# Released qty recorded separately.
		self.assertAlmostEqual(flt(sre.closed_qty), 4)
		self.assertEqual(sre.status, "Closed")

	def test_closed_sre_stops_counting_as_reserved(self):
		"""After close, get_sre_reserved_qty must net the closed portion out —
		live reserved drops by the released `closed_qty`.
		"""
		wo, sre, delivery_wh, supplier_wh, item_variant, uom, rt = _build_normal_wo_with_sre(
			reserved=10, seed_qty=10,
		)
		dim_filters = {"received_type": rt, "lot": sre.get("lot")}
		# Before any delivery: SRE reserves 10 → reserved seen = 10.
		reserved_before = get_sre_reserved_qty(
			item_code=item_variant, warehouse=delivery_wh, **dim_filters,
		)
		self.assertAlmostEqual(flt(reserved_before), 10)

		# Deliver 6, then close. SRE: delivered=6, closed=4, status='Closed'
		# → excluded from get_sre_reserved_qty entirely.
		_make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty=6)
		sre.reload()
		sre.close_at_delivered()

		reserved_after = get_sre_reserved_qty(
			item_code=item_variant, warehouse=delivery_wh, **dim_filters,
		)
		self.assertAlmostEqual(
			flt(reserved_after), 0,
			msg="A Closed SRE must stop counting in get_sre_reserved_qty.",
		)

	def test_close_at_delivered_with_zero_delivered_still_works(self):
		"""Reserved 10, delivered 0, close: closed_qty=10, status='Closed'."""
		wo, sre, *_ = _build_normal_wo_with_sre(reserved=10, seed_qty=10)
		sre.close_at_delivered()
		sre.reload()
		self.assertAlmostEqual(flt(sre.closed_qty), 10)
		self.assertAlmostEqual(flt(sre.delivered_qty), 0)
		self.assertEqual(sre.status, "Closed")

	def test_close_at_delivered_rejects_when_fully_delivered(self):
		"""If delivered == reserved, close_at_delivered must throw — there's
		nothing left to close."""
		wo, sre, delivery_wh, supplier_wh, item_variant, uom, rt = _build_normal_wo_with_sre(
			reserved=10, seed_qty=10,
		)
		_make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty=10)
		sre.reload()
		self.assertEqual(sre.status, "Delivered")
		with self.assertRaises(frappe.ValidationError):
			sre.close_at_delivered()

	def test_dc_submit_against_closed_sre_is_blocked(self):
		"""Once an SRE is closed, a fresh DC submit that would update it must
		throw — the SRE is frozen."""
		wo, sre, delivery_wh, supplier_wh, item_variant, uom, rt = _build_normal_wo_with_sre(
			reserved=10, seed_qty=10,
		)
		_make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty=4)
		sre.reload()
		sre.close_at_delivered()
		sre.reload()
		self.assertEqual(sre.status, "Closed")

		with self.assertRaises(frappe.ValidationError):
			_make_partial_dc(wo, delivery_wh, supplier_wh, item_variant, uom, rt, qty=2)

	def test_validate_blocks_delivered_plus_closed_exceeding_reserved(self):
		"""Direct attempt to set delivered + closed > reserved must throw."""
		wo, sre, *_ = _build_normal_wo_with_sre(reserved=10, seed_qty=10)
		sre.delivered_qty = 7
		sre.closed_qty = 5  # 7 + 5 = 12 > 10
		with self.assertRaises(frappe.ValidationError):
			sre.save()
