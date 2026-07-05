"""Verify Stock Reservation Entry (SRE) lifecycle on Delivery Challan
submit and cancel — for both rework WOs (auto-created SRE) and normal
WOs (manually-created SRE).

Lifecycle:
  WO submit         → SRE present, reserved_qty=qty,    delivered_qty=0, status='Reserved'
  DC submit         → SRE.delivered_qty += dispatched, status='Delivered' or 'Partially Reserved'
  DC cancel         → SRE.delivered_qty -= dispatched (clamped 0), status reverts to 'Reserved'
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.yrp.doctype.work_order.test_rework_flow import (
	_make_parent_grn,
	_make_parent_work_order,
	_received_type,
	_set_rejected_received_type,
)
from yrp.yrp.doctype.work_order.work_order import (
	_stock_dimension_values,
	create_rework_work_order,
	get_rework_source_rows,
)


def _get_rework_sre(rework_wo):
	sre_name = frappe.db.get_value(
		"Stock Reservation Entry",
		{
			"voucher_type": "Work Order",
			"voucher_no": rework_wo.name,
			"voucher_detail_no": rework_wo.deliverables[0].name,
			"docstatus": 1,
		},
		"name",
	)
	assert sre_name, "Rework WO must have a submitted SRE for its first deliverable."
	return frappe.get_doc("Stock Reservation Entry", sre_name)


class TestReworkSREDeliveryChallanLifecycle(FrappeTestCase):
	def test_dc_submit_and_cancel_restore_sre_delivered_qty(self):
		rework_rt = _received_type(f"_T_DCSRE_Source_{frappe.generate_hash(length=6)}")
		rejected_rt = _received_type(f"_T_DCSRE_Rejected_{frappe.generate_hash(length=6)}")
		_set_rejected_received_type(rejected_rt)

		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rework_rt, qty=10)

		sources = get_rework_source_rows(wo.name)
		source = next(row for row in sources if row["received_type"] == rework_rt)
		rework_wo_name = create_rework_work_order(
			wo.name,
			[{"source_key": source["source_key"], "qty": 6}],
		)
		rework_wo = frappe.get_doc("Work Order", rework_wo_name)
		rework_wo.submit()

		# Step 1 — after rework WO submit, SRE is fully reserved, nothing delivered.
		sre = _get_rework_sre(rework_wo)
		self.assertAlmostEqual(flt(sre.reserved_qty), 6)
		self.assertAlmostEqual(flt(sre.delivered_qty), 0)
		self.assertEqual(sre.status, "Reserved")

		# Step 2 — DC submit should bump delivered_qty by the dispatched qty.
		dc = frappe.get_doc({
			"doctype": "Delivery Challan",
			"work_order": rework_wo.name,
			"from_location": rework_wo.delivery_location,
			"supplier": rework_wo.supplier,
			"from_warehouse": delivery_wh,
			"to_warehouse": supplier_wh,
			"process_name": rework_wo.process_name,
			"item": rework_wo.item,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"items": [{
				"item_variant": item_variant,
				"qty": 6,
				"delivered_quantity": 6,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"received_type": rework_rt,
				"ref_doctype": "Work Order Deliverables",
				"ref_docname": rework_wo.deliverables[0].name,
				"table_index": 0,
				"row_index": "0",
			}],
		})
		dc.insert(ignore_permissions=True)
		dc.submit()

		sre.reload()
		self.assertAlmostEqual(
			flt(sre.delivered_qty), 6,
			msg="DC submit must increase SRE.delivered_qty by the dispatched qty.",
		)
		self.assertEqual(
			sre.status, "Delivered",
			msg="SRE should flip to 'Delivered' once delivered_qty == reserved_qty.",
		)

		# Step 3 — DC cancel must release the reservation back: delivered_qty -=
		# dispatched, status reverts to 'Reserved' (nothing delivered anymore).
		dc.cancel()
		sre.reload()
		self.assertAlmostEqual(
			flt(sre.delivered_qty), 0,
			msg="DC cancel must restore SRE.delivered_qty back to 0.",
		)
		self.assertEqual(
			sre.status, "Reserved",
			msg="SRE status must revert to 'Reserved' after DC cancel undoes the dispatch.",
		)

	def test_normal_wo_manual_sre_is_also_updated_by_dc(self):
		"""For an ordinary (non-rework) Work Order, the user can manually
		create an SRE against a deliverable row. DC submit/cancel must drive
		that manual SRE's delivered_qty the same way it does for the
		auto-created rework SRE — the lifecycle is voucher-based, not
		conditional on is_rework.
		"""
		rework_rt = _received_type(f"_T_NormalWO_SRE_{frappe.generate_hash(length=6)}")
		rejected_rt = _received_type(f"_T_NormalWO_SRE_Rej_{frappe.generate_hash(length=6)}")
		_set_rejected_received_type(rejected_rt)

		# Parent WO is is_rework=0 — exactly what "normal WO" needs.
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		# Seed delivery_wh with stock under the WO's deliverable received_type
		# so the DC has something to ship out.
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rework_rt, qty=10)

		# Manually create the SRE against the WO's deliverable row at the
		# warehouse the DC will ship from. Override received_type to the seeded
		# bucket so the SRE matches the stock the DC will actually move.
		deliverable = wo.deliverables[0]
		dim_values = _stock_dimension_values(wo, deliverable)
		dim_values["received_type"] = rework_rt
		sre = frappe.get_doc({
			"doctype": "Stock Reservation Entry",
			"item_code": item_variant,
			"warehouse": delivery_wh,
			"voucher_type": "Work Order",
			"voucher_no": wo.name,
			"voucher_detail_no": deliverable.name,
			"stock_uom": uom,
			"available_qty": 10,
			"voucher_qty": 6,
			"reserved_qty": 6,
			"delivered_qty": 0,
			**dim_values,
		})
		sre.insert(ignore_permissions=True)
		sre.submit()
		self.assertEqual(sre.status, "Reserved")

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
				"qty": 6,
				"delivered_quantity": 6,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"received_type": rework_rt,
				"ref_doctype": "Work Order Deliverables",
				"ref_docname": deliverable.name,
				"table_index": 0,
				"row_index": "0",
			}],
		})
		dc.insert(ignore_permissions=True)
		dc.submit()

		sre.reload()
		self.assertAlmostEqual(
			flt(sre.delivered_qty), 6,
			msg="DC submit against a normal WO must update its manually-created SRE.",
		)
		self.assertEqual(sre.status, "Delivered")

		dc.cancel()
		sre.reload()
		self.assertAlmostEqual(
			flt(sre.delivered_qty), 0,
			msg="DC cancel against a normal WO must restore its manually-created SRE.",
		)
		self.assertEqual(sre.status, "Reserved")
